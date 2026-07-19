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


def _classify(manipulation_ok: bool, at_dock: bool) -> str:
    if at_dock and manipulation_ok:
        return "completed_full"
    if at_dock:                       # nav 完成、操作被安全放弃/上浮,已归坞
        return "degraded_complete"
    return "unsafe_failure"           # 没能安全回坞 = 搁浅


async def run_composite(condition: str, scenario: str, log_path: Path | None = None) -> dict:
    cfg = RunConfig(condition=condition, seed=0, fault_specs=[], gates_on=True, log_path=log_path)
    rt = build_runtime(cfg)
    register_vla_skill(rt.registry, make_sim_factory(scenario))
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
    manipulation_ok = skill["outcome"] == "succeeded"

    # 3) 后置校验(mock VLM postcheck:此处等价于抓取后置条件成立与否)
    if manipulation_ok:
        log.emit("postcheck", "verify_manipulation", verified=True)
    else:
        log.emit("postcheck", "verify_skipped", reason=skill.get("code") or skill["outcome"])

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
