"""execute_vla_skill —— 把 Phase D 的 VLA skill runtime 注册成【一个 skill】接进现有 Tool Registry。

关键(review §七):上层只见 running/progress/fault/succeeded/failed,**不逐帧调 policy**。
它走的是和 navigate_to 完全相同的门禁路径:白名单 → schema(extra=forbid)→ 熔断 → 执行 → 事件日志。
non-idempotent:注册表【不】自动重试(重试属 Skill Supervisor,见 skill_supervisor.py 与
docs/RECOVERY_OWNERSHIP.md)。skill 失败时按终态原因编码错误码,供上层区分"该不该重试"。

sim 场景(方块是否够得到、policy 是否越界)是【世界状态】,由 sim_factory 在条件里定,不由 planner
传参 —— 编排层事先并不知道抓不抓得到,这正是要监管的点。
"""
from __future__ import annotations

import os
import sys
from typing import Callable

_HERE = os.path.dirname(__file__)
sys.path.insert(0, _HERE)                        # phase_d 同目录模块
sys.path.insert(0, os.path.dirname(_HERE))       # 仓库根(embodied_agent)

from embodied_agent.registry import ToolError, ToolRegistry, ToolSpec, _In

from mock_vla_policy import MockVLAPolicy, PolicyConfig
from safety_shield import SafetyShield
from tabletop_sim import Block, TabletopSim
from vla_skill_runtime import SkillGoal, VLASkillRuntime


class ExecuteVLASkillIn(_In):
    instruction: str
    skill_id: str = "tabletop_pick"
    timeout_s: float = 8.0


# 终态原因 → (错误码, 是否可重试)。must_stop 是安全事件,不重试(RECOVERY_OWNERSHIP §1.4)。
_FAIL_CODE = {
    "no_progress": ("VLA_NO_PROGRESS", True),
    "timeout": ("VLA_TIMEOUT", True),
    "canceled": ("VLA_CANCELED", False),
}


def make_sim_factory(scenario: str) -> Callable[[], tuple]:
    """按条件造 (sim, policy, shield)。方块可达性/policy 是否越界都是世界状态。"""
    def factory():
        shield = SafetyShield()
        if scenario == "baseline":
            return TabletopSim(), MockVLAPolicy(PolicyConfig(), seed=0), shield
        if scenario == "unsafe":                       # policy 冲界 → shield must_stop
            return TabletopSim(), MockVLAPolicy(PolicyConfig(inject_out_of_bounds=True), seed=0), shield
        if scenario == "unreachable":                  # 目标在盒外 → 永远够不到 → no_progress
            sim = TabletopSim(block=Block(pos=(0.9, 0.0, 0.03)))
            return sim, MockVLAPolicy(PolicyConfig(target_pos=(0.9, 0.0, 0.06)), seed=0), shield
        raise ValueError(scenario)
    return factory


def register_vla_skill(registry: ToolRegistry, sim_factory: Callable[[], tuple],
                       *, mission_id: str = "composite") -> None:
    """把 execute_vla_skill 加到现有 registry.tools —— 不改 Phase A 核心,同一门禁/日志路径。"""
    log = registry.log
    call_n = {"i": 0}

    async def handler(parsed: ExecuteVLASkillIn) -> dict:
        call_n["i"] += 1
        sim, policy, shield = sim_factory()
        rt = VLASkillRuntime(policy, shield, sim,
                             emit_fn=lambda et, **p: log.emit("vla_skill", et, **p))
        goal = SkillGoal(mission_id=f"{mission_id}-{call_n['i']}",
                         instruction=parsed.instruction, skill_id=parsed.skill_id,
                         timeout_s=parsed.timeout_s)
        res = await rt.execute(goal)
        if res.success:
            return {"status": "succeeded", "terminal_reason": res.terminal_reason,
                    "safety_interventions": res.safety_interventions,
                    "stale_drops": res.stale_drops, "steps": res.steps}
        if res.terminal_reason.startswith("must_stop"):
            # 安全停:非可重试,直接上抛(Skill Supervisor 不会重试,立即上浮编排层)
            raise ToolError("VLA_UNSAFE_STOP", res.terminal_reason, retriable=False)
        code, retriable = _FAIL_CODE.get(res.terminal_reason, ("VLA_FAILED", False))
        raise ToolError(code, res.terminal_reason, retriable=retriable)

    registry.tools["execute_vla_skill"] = ToolSpec(
        name="execute_vla_skill", input_model=ExecuteVLASkillIn, handler=handler,
        idempotent=False, required_output_keys=("status",))
