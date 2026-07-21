"""把 Phase D 的 VLA skill 注册成【一组异步 goal-handle 工具】接进现有 Tool Registry(D1)。

与 `navigate_to` **完全同构**的契约(codex 评审:此前 registry 阻塞等待 skill 终态、无外部
goal/feedback/cancel,与 nav 语义分裂):

    execute_vla_skill   → {skill_goal_id}      立即返回,不阻塞
    get_skill_feedback  → {status, steps, …}   在飞可轮询
    cancel_skill        → {canceled}           在飞可取消
    get_skill_result    → {status, code, …}    终态后可取

四个工具都走和 nav 一模一样的门禁路径:白名单 → schema(extra=forbid)→ 熔断 → 事件日志。
上层只见 executing/succeeded/failed 与进度,**不逐帧调 policy**(review §七)。

重试归属:`execute_vla_skill`/`cancel_skill` **非幂等**(注册表不自动重试);重试是 Skill
Supervisor 的职责(见 skill_supervisor.py 与 docs/RECOVERY_OWNERSHIP.md)。查询类幂等。

sim 场景(方块够不够得到、policy 是否越界)是【世界状态】,由 sim_factory 在条件里定,不由
planner 传参 —— 编排层事先并不知道抓不抓得到,这正是要监管的点。
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
from policy_contract import PolicyContract
from safety_shield import SafetyShield
from skill_server import SkillServer
from tabletop_sim import Block, TabletopSim
from vla_skill_runtime import SkillGoal, VLASkillRuntime


class ExecuteVLASkillIn(_In):
    instruction: str
    skill_id: str = "tabletop_pick"
    timeout_s: float = 8.0


class SkillGoalIdIn(_In):
    skill_goal_id: str


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
                       *, mission_id: str = "composite",
                       contract: PolicyContract | None = None) -> SkillServer:
    """把四个 skill 工具加到现有 registry.tools —— 不改 Phase A 核心,同一门禁/日志路径。
    返回 SkillServer,供**独立 postcheck** 读末态(见 sim_of)。"""
    log = registry.log
    call_n = {"i": 0}

    def runtime_factory() -> VLASkillRuntime:
        sim, policy, shield = sim_factory()
        return VLASkillRuntime(policy, shield, sim, contract=contract,
                               emit_fn=lambda et, **p: log.emit("vla_skill", et, **p))

    server = SkillServer(runtime_factory)

    async def execute_vla_skill(p: ExecuteVLASkillIn) -> dict:
        call_n["i"] += 1
        goal = SkillGoal(mission_id=f"{mission_id}-{call_n['i']}",
                         instruction=p.instruction, skill_id=p.skill_id,
                         timeout_s=p.timeout_s)
        res = server.send_goal(goal)
        if "error" in res:
            raise ToolError("SKILL_BUSY", res["error"])
        log.emit("vla_skill", "skill_goal_accepted", skill_goal_id=res["skill_goal_id"],
                 instruction=p.instruction, skill_id=p.skill_id)
        return {"skill_goal_id": res["skill_goal_id"]}

    async def get_skill_feedback(p: SkillGoalIdIn) -> dict:
        fb = server.feedback(p.skill_goal_id)
        if fb is None:
            raise ToolError("UNKNOWN_SKILL_GOAL", f"skill goal {p.skill_goal_id} 不存在")
        return fb

    async def cancel_skill(p: SkillGoalIdIn) -> dict:
        ok = server.cancel(p.skill_goal_id)
        log.emit("vla_skill", "skill_cancel_requested",
                 skill_goal_id=p.skill_goal_id, accepted=ok)
        return {"canceled": ok}

    async def get_skill_result(p: SkillGoalIdIn) -> dict:
        r = server.result(p.skill_goal_id)
        if r is None:
            if server.feedback(p.skill_goal_id) is None:
                raise ToolError("UNKNOWN_SKILL_GOAL", f"skill goal {p.skill_goal_id} 不存在")
            raise ToolError("SKILL_NOT_FINISHED", "skill 仍在飞,请先轮询 get_skill_feedback",
                            retriable=True)
        return r

    async def verify_skill_postcondition(p: SkillGoalIdIn) -> dict:
        """【独立后置校验】回读 sim 末态判定,**不采信 skill 自报的 success**。
        并返回 agrees_with_skill —— 自报与实测背离本身就是高价值审计信号。"""
        sim = server.sim_of(p.skill_goal_id)
        if sim is None:
            raise ToolError("UNKNOWN_SKILL_GOAL", f"skill goal {p.skill_goal_id} 不存在")
        r = server.result(p.skill_goal_id)
        reported = bool(r and r.get("status") == "succeeded")
        grasped = bool(sim.block.grasped)
        return {"verified": grasped, "method": "independent_sim_readback",
                "block_grasped": grasped, "skill_reported_success": reported,
                "agrees_with_skill": grasped == reported}

    registry.tools["verify_skill_postcondition"] = ToolSpec(
        "verify_skill_postcondition", SkillGoalIdIn, verify_skill_postcondition,
        idempotent=True, required_output_keys=("verified",))
    registry.tools["execute_vla_skill"] = ToolSpec(
        "execute_vla_skill", ExecuteVLASkillIn, execute_vla_skill,
        idempotent=False, required_output_keys=("skill_goal_id",))
    registry.tools["get_skill_feedback"] = ToolSpec(
        "get_skill_feedback", SkillGoalIdIn, get_skill_feedback,
        idempotent=True, required_output_keys=("status",))
    registry.tools["cancel_skill"] = ToolSpec(
        "cancel_skill", SkillGoalIdIn, cancel_skill,
        idempotent=True, required_output_keys=("canceled",))
    registry.tools["get_skill_result"] = ToolSpec(
        "get_skill_result", SkillGoalIdIn, get_skill_result,
        idempotent=True, required_output_keys=("status",))
    return server
