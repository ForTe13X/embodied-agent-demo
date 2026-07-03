"""故障注入器:消费 faults.yaml(预注册工件),按 seed 解析触发条件。

seed 的作用域(评审 B2 确定性边界):故障触发 tick、初始电量、工具故障窗口/模式。
每次解析/激活都写事件日志(fault_armed / fault_activated),指标脚本据此归因。
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from .events import EventLog
from .world import World, edge_key


@dataclass
class FaultSpec:
    fault_id: str
    description: str
    target: str
    mode: str
    trigger: dict
    params: dict
    expected_detection: str
    expected_recovery_chain: list[str]


def load_fault_specs(path: Path) -> dict[str, FaultSpec]:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    specs = {}
    for item in raw:
        spec = FaultSpec(
            fault_id=item["fault_id"],
            description=item.get("description", ""),
            target=item["target"],
            mode=item["mode"],
            trigger=item["trigger"],
            params=item.get("params", {}),
            expected_detection=item.get("expected_detection", ""),
            expected_recovery_chain=item.get("expected_recovery_chain", []),
        )
        specs[spec.fault_id] = spec
    return specs


class FaultInjector:
    def __init__(
        self,
        specs: list[FaultSpec],
        rng: random.Random,
        world: World,
        event_log: EventLog,
    ):
        self.specs = specs
        self.rng = rng
        self.world = world
        self.log = event_log
        self._armed: list[dict] = []       # 待触发的 tick 型故障
        self._tool_fault: Optional[dict] = None
        self._tool_call_counts: dict[str, int] = {}
        self._setup()

    def _setup(self) -> None:
        for spec in self.specs:
            trig = spec.trigger
            if trig["kind"] == "at_start":
                self._activate_static(spec)
            elif trig["kind"] == "tick_uniform":
                at_tick = self.rng.randint(trig["min"], trig["max"])
                self._armed.append({"spec": spec, "at_tick": at_tick, "active": False})
                self.log.emit("fault_injector", "fault_armed",
                              fault_id=spec.fault_id, at_tick=at_tick)
            else:
                raise ValueError(f"unknown trigger kind: {trig['kind']}")

    def _activate_static(self, spec: FaultSpec) -> None:
        if spec.mode == "isolate_node":
            self.world.unreachable_nodes.add(spec.params["node"])
            self.log.emit("fault_injector", "fault_activated",
                          fault_id=spec.fault_id, node=spec.params["node"])
        elif spec.mode == "battery_drain":
            initial = self.rng.uniform(spec.params["initial_pct_min"],
                                       spec.params["initial_pct_max"])
            self.world.battery_pct = round(initial, 2)
            self.world.battery_decay_multiplier = spec.params["decay_multiplier"]
            self.log.emit("fault_injector", "fault_activated",
                          fault_id=spec.fault_id,
                          initial_battery_pct=self.world.battery_pct,
                          decay_multiplier=spec.params["decay_multiplier"])
        elif spec.mode == "tool_fault":
            fail_first = self.rng.choice(spec.params["fail_first_choices"])
            fault_mode = self.rng.choice(spec.params["modes"])
            self._tool_fault = {
                "fault_id": spec.fault_id,
                "tool": spec.params["tool"],
                "fail_first": fail_first,
                "mode": fault_mode,
                "announced": False,
            }
            self.log.emit("fault_injector", "fault_armed",
                          fault_id=spec.fault_id, tool=spec.params["tool"],
                          fail_first=fail_first, mode=fault_mode)
        else:
            raise ValueError(f"unknown at_start mode: {spec.mode}")

    def on_tick(self, tick: int, active_edge: Optional[tuple[str, str]]) -> None:
        """每 tick 由 adapter.wait() 调用;tick 型故障到点即激活。"""
        for armed in self._armed:
            if armed["active"] or tick < armed["at_tick"]:
                continue
            spec: FaultSpec = armed["spec"]
            if spec.mode == "block_active_edge":
                if active_edge is None:
                    continue  # 机器人此刻不在边上,推迟到下一个在边上的 tick
                ek = edge_key(*active_edge)
                self.world.blocked_edges.add(ek)
                armed["active"] = True
                self.log.emit("fault_injector", "fault_activated",
                              fault_id=spec.fault_id, edge=list(ek))
            elif spec.mode == "sensor_down":
                self.world.sensor_healthy = False
                armed["active"] = True
                self.log.emit("fault_injector", "fault_activated",
                              fault_id=spec.fault_id)
            else:
                raise ValueError(f"unknown tick mode: {spec.mode}")

    def tool_intercept(self, tool_name: str) -> Optional[str]:
        """注册表每次(含重试)执行工具前调用;命中窗口则返回注入模式。"""
        count = self._tool_call_counts.get(tool_name, 0) + 1
        self._tool_call_counts[tool_name] = count
        tf = self._tool_fault
        if tf and tf["tool"] == tool_name and count <= tf["fail_first"]:
            if not tf["announced"]:
                tf["announced"] = True
                self.log.emit("fault_injector", "fault_activated",
                              fault_id=tf["fault_id"], tool=tool_name,
                              mode=tf["mode"], fail_first=tf["fail_first"])
            self.log.emit("fault_injector", "tool_fault_injected",
                          tool=tool_name, call_index=count, mode=tf["mode"])
            return tf["mode"]
        return None
