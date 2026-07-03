"""每个 run 的运行时装配:时钟/世界/注入器/server/adapter/注册表/HITL/记忆。

RNG 按子系统分流(random.Random 用字符串种子,跨平台稳定):
  {seed}:fault   故障触发采样        {seed}:battery 电量衰减噪声
  {seed}:nav     边耗时抖动          {seed}:sense   感知置信度抖动
消费顺序与流互不干扰 → 同 seed 事件流逐字节一致(有确定性回归测试守着)。
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .adapter import MockAdapter
from .clock import SimClock
from .events import EventLog
from .faults import FaultInjector, FaultSpec
from .hitl import HITLPolicy, ScriptedHITLPolicy
from .intent import Intent, default_intent
from .memory import RunMemory
from .mock_server import MockNavServer
from .planner_rules import RuleSelector
from .registry import ToolRegistry
from .safety import SafetyMonitor
from .world import World, default_map

STAGNATION_THRESHOLD_TICKS = 6   # feedback 停滞水位(编排层检测,非 server 自报)
MAX_GOAL_TICKS = 120             # 单目标兜底超时
MAX_WAIT_CHARGE_TICKS = 120
RECURSION_LIMIT = 1000


@dataclass
class RunConfig:
    condition: str
    seed: int
    fault_specs: list[FaultSpec]
    gates_on: bool = True
    hitl_rules: Optional[list[tuple[str, str]]] = None
    tick_duration_s: float = 0.0
    log_path: Optional[Path] = None
    intent: Optional[Intent] = None
    initial_battery_pct: Optional[float] = None  # 对抗/消融条件用(挑战电量闸)


@dataclass
class Runtime:
    config: RunConfig
    clock: SimClock
    world: World
    event_log: EventLog
    safety: SafetyMonitor
    injector: FaultInjector
    server: MockNavServer
    adapter: MockAdapter
    registry: ToolRegistry
    memory: RunMemory
    intent: Intent
    selector: RuleSelector


def build_runtime(cfg: RunConfig, hitl: Optional[HITLPolicy] = None) -> Runtime:
    clock = SimClock(cfg.tick_duration_s)
    run_id = f"{cfg.condition}-s{cfg.seed}"
    log = EventLog(clock, run_id, cfg.condition, cfg.seed, cfg.log_path)

    rng = lambda stream: random.Random(f"{cfg.seed}:{stream}")  # noqa: E731
    topo = default_map()
    world = World(topo=topo, rng_battery=rng("battery"),
                  anomalies={"a2": "unattended_box"})
    if cfg.initial_battery_pct is not None:
        world.battery_pct = cfg.initial_battery_pct
    safety = SafetyMonitor(topo, world, log)
    injector = FaultInjector(cfg.fault_specs, rng("fault"), world, log)
    server = MockNavServer(topo, world, log, rng("nav"), safety)
    adapter = MockAdapter(clock, world, server, injector, rng("sense"))
    if hitl is None:
        hitl = ScriptedHITLPolicy(cfg.hitl_rules or [], default="deny")
    registry = ToolRegistry(adapter, topo, clock, log, injector, hitl,
                            gates_on=cfg.gates_on)
    return Runtime(
        config=cfg, clock=clock, world=world, event_log=log, safety=safety,
        injector=injector, server=server, adapter=adapter, registry=registry,
        memory=RunMemory(), intent=cfg.intent or default_intent(),
        selector=RuleSelector(),
    )
