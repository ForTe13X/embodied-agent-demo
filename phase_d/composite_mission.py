"""Phase D-2 端到端复合任务:同一个编排壳子同时管 Nav(skill)和 VLA(learned skill)。

任务:"去工作台(a1)→ 用 VLA 抓起红方块 → 校验 → 归坞"。
  · Nav 与 execute_vla_skill 走【同一个 Tool Registry】(同一门禁/schema/熔断/事件日志);
  · VLA skill 失败经 Skill Supervisor 按恢复职责矩阵路由(重试 / 上浮);
  · 全程一份共享事件日志,可回放、可审计。
诚实边界:这里 Nav 是 mock server(真实 Nav2 见 Phase B/C);D-2 证的是【skill 组合 + 恢复归属】,
不是又跑一遍真实 Nav2。VLA 是 mock policy + 运动学 sim(见 phase_d/README、docs/POSITIONING)。

用法:.venv\\Scripts\\python phase_d\\composite_mission.py [baseline|unsafe|unreachable]
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_HERE = os.path.dirname(__file__)
sys.path.insert(0, _HERE)                        # phase_d 同目录模块
sys.path.insert(0, os.path.dirname(_HERE))       # 仓库根(embodied_agent)

from embodied_agent.runtime import RunConfig, Runtime, build_runtime

from skill_supervisor import run_skill_supervised
from vla_skill_tool import make_sim_factory, register_vla_skill

PATROL_WORKSTATION = "a1"


async def _dispatch_and_wait(rt: Runtime, tool: str, args: dict, max_ticks: int = 200) -> str:
    """经 registry 发一个 nav 目标并推进 mock 世界到终态,返回终态 status。"""
    res = await rt.registry.call(tool, args)
    if not res.ok:
        return "rejected:" + res.error["code"]
    gid = res.data["goal_id"]
    for _ in range(max_ticks):
        fb = await rt.registry.call("get_nav_feedback", {"goal_id": gid}, poll=True)
        st = fb.data["status"] if fb.ok else "error"
        if st != "executing":
            return st
        await rt.adapter.wait(1)
    return "timeout"


def _independent_postcheck(server, skill: dict) -> tuple[bool, dict]:
    """【独立后置校验】直接读末态,**不复用 skill 自报的 success**(codex 评审)。

    旧实现是 `manipulation_ok = skill["outcome"] == "succeeded"` 然后 emit verified=True ——
    一个恒真的同义反复:skill 说成功就"校验通过"。真正要防的恰恰是"skill 自报成功但物体
    其实没抓住"这类失败模式,而那种情况下自报永远查不出来。
    这里改为回读 sim 中方块的实际状态,并把"是否与 skill 自报一致"一并写进审计日志——
    两者不一致本身就是高价值信号。
    """
    sid = skill.get("skill_goal_id")
    sim = server.sim_of(sid) if sid else None
    reported = skill.get("outcome") == "succeeded"
    if sim is None:
        return False, {"method": "independent_sim_readback", "reason": "no_sim_handle",
                       "skill_reported_success": reported}
    grasped = bool(sim.block.grasped)
    return grasped, {"method": "independent_sim_readback", "block_grasped": grasped,
                     "skill_reported_success": reported,
                     "agrees_with_skill": grasped == reported}


def _classify(manipulation_ok: bool, at_dock: bool) -> str:
    if at_dock and manipulation_ok:
        return "completed_full"
    if at_dock:                       # nav 完成、操作被安全放弃/上浮,已归坞
        return "degraded_complete"
    return "unsafe_failure"           # 没能安全回坞 = 搁浅


async def run_composite(condition: str, scenario: str, log_path: Path | None = None) -> dict:
    cfg = RunConfig(condition=condition, seed=0, fault_specs=[], gates_on=True, log_path=log_path)
    rt = build_runtime(cfg)
    server = register_vla_skill(rt.registry, make_sim_factory(scenario))
    log = rt.event_log
    log.emit("mission_planner", "composite_plan",
             steps=["navigate_to:a1", "execute_vla_skill", "verify_manipulation", "return_to_dock"],
             scenario=scenario)

    # 1) 去工作台
    nav1 = await _dispatch_and_wait(rt, "navigate_to", {"node_id": PATROL_WORKSTATION})
    if nav1 != "succeeded":
        return _finish(rt, "unsafe_failure", {"nav_to_workstation": nav1})

    # 2) VLA 抓取(经 Skill Supervisor)
    skill = await run_skill_supervised(
        rt.registry, {"instruction": "pick up the red block",
                      "skill_id": "tabletop_pick", "timeout_s": 8.0})
    # 3) 【独立后置校验】回读 sim 末态判定,不采信 skill 自报(见 _independent_postcheck)
    manipulation_ok, evidence = _independent_postcheck(server, skill)
    log.emit("postcheck", "verify_manipulation", verified=manipulation_ok, **evidence)
    if not evidence.get("agrees_with_skill", True):
        # 自报与实测不一致:审计层面的高价值信号(skill 说成了但物体没抓住,或反之)
        log.emit("postcheck", "verify_disagrees_with_skill", **evidence)

    # 4) 无论操作成败,都安全归坞
    navd = await _dispatch_and_wait(rt, "return_to_dock", {})
    at_dock = navd == "succeeded"

    outcome = _classify(manipulation_ok, at_dock)
    return _finish(rt, outcome, {"nav_to_workstation": nav1, "skill": skill,
                                 "at_dock": at_dock, "return": navd})


def _finish(rt: Runtime, outcome: str, detail: dict) -> dict:
    rt.event_log.emit("reporter", "run_summary", outcome_hint=outcome, detail=detail)
    rt.event_log.close()
    return {"condition": rt.config.condition, "outcome": outcome, "detail": detail}


async def _main():
    scenario = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    res = await run_composite(f"composite_{scenario}", scenario)
    print(f"\n[{scenario}] outcome = {res['outcome']}")
    print(f"  skill = {res['detail'].get('skill')}")
    print(f"  归坞 = {res['detail'].get('at_dock')}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(asyncio.run(_main()))
