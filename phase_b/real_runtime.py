"""Day-4 编排整合:把【同一套 LangGraph 编排图 + Tool Registry】接到真实 Nav2 上。
做法是给 embodied_agent.runtime.Runtime 喂三个 shim,adapter 换成 RclpyAdapter:
  · RealClock   —— .tick = 墙钟秒(MAX_GOAL_TICKS=120 → 120s 超时);替代 SimClock 的虚拟 tick。
  · RealWorld   —— 只提供编排图真正读的 .topo 与 .robot_node(后者动态取自 adapter._cur_node()),
                   外加 registry._last_known_battery 要的 .battery_pct(loopback 恒 100)。
  · NoopInjector —— 真实栈里没有 mock 的工具级故障注入(故障走 keepout 掩码),tool_intercept 恒 None。
registry / graph / memory / intent / selector / event_log 全部【原样复用】——这正是"同一编排,换 adapter"。

Runtime 是普通 dataclass(不做运行时类型校验),故可直接塞这些 shim。只在容器内运行。
"""
from __future__ import annotations

import time
from typing import Optional

from embodied_agent.events import EventLog
from embodied_agent.hitl import HITLPolicy, ScriptedHITLPolicy
from embodied_agent.intent import Intent, default_intent
from embodied_agent.memory import RunMemory
from embodied_agent.planner_rules import RuleSelector
from embodied_agent.registry import ToolRegistry
from embodied_agent.runtime import RunConfig, Runtime
from embodied_agent.world import DOCK, default_map


class RealClock:
    """.tick = 自 t0 起的墙钟秒(整数)。真实时间自走,无需拨钟。"""

    def __init__(self):
        self._t0 = time.monotonic()
        self.tick_duration_s = 1.0

    @property
    def tick(self) -> int:
        return int(time.monotonic() - self._t0)

    async def advance(self, n: int = 1) -> int:   # API 兼容;真实时间自走,不主动拨钟
        return self.tick


class RealWorld:
    """编排图/注册表实际读到的只有:.topo、.robot_node、.battery_pct、.sensor_healthy。
    robot_node 动态取自 adapter 的滞回最近邻(地面真值来自 TF)。"""

    def __init__(self, topo, adapter):
        self.topo = topo
        self._adapter = adapter
        self.battery_pct = 100.0     # loopback 无耗电模型:恒额定(mock-only 语义)
        self.sensor_healthy = True

    @property
    def robot_node(self) -> str:
        return self._adapter._cur_node() or DOCK


class NoopInjector:
    """真实栈无 mock 工具级故障注入(故障=keepout 掩码在 costmap 层)。"""

    def tool_intercept(self, name: str) -> Optional[str]:
        return None

    def on_tick(self, *a, **k) -> None:
        return None


def build_real_runtime(adapter, *, intent: Optional[Intent] = None,
                       hitl: Optional[HITLPolicy] = None, gates_on: bool = True,
                       log_path=None, condition: str = "real", seed: int = 0) -> Runtime:
    """用已构造并 bootstrap 过的 RclpyAdapter 装配一个 Runtime(供 build_graph 用)。"""
    cfg = RunConfig(condition=condition, seed=seed, fault_specs=[],
                    gates_on=gates_on, log_path=log_path,
                    intent=intent or default_intent())
    clock = RealClock()
    log = EventLog(clock, f"{condition}-s{seed}", condition, seed, log_path)
    topo = default_map()
    world = RealWorld(topo, adapter)
    adapter.world = world            # registry._last_known_battery 经 adapter.world.battery_pct
    injector = NoopInjector()
    if hitl is None:
        hitl = ScriptedHITLPolicy([], default="deny")
    registry = ToolRegistry(adapter, topo, clock, log, injector, hitl, gates_on=gates_on)
    return Runtime(
        config=cfg, clock=clock, world=world, event_log=log, safety=None,
        injector=injector, server=None, adapter=adapter, registry=registry,
        memory=RunMemory(), intent=cfg.intent, selector=RuleSelector(),
    )
