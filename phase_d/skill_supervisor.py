"""Skill Supervisor —— learned-skill 的恢复归属层(docs/RECOVERY_OWNERSHIP.md 的"连续抓取失败"行)。

夹在编排层与 VLA skill 之间:skill 失败时按【终态原因】决定重试还是上浮:
  · 安全停(VLA_UNSAFE_STOP)→ 【绝不重试】,立即上浮编排层(安全类,RECOVERY_OWNERSHIP §1.4);
  · 可重试失败(no_progress / timeout)→ 重试至多 max_retries 次;仍失败 → 上浮编排层;
  · 其它 → 上浮。
它不自己"发明"恢复,只做"该重试就重试、超出能力就上浮"这一层归属决策,并全程写共享事件日志。

D1:改走**异步 goal-handle**(execute → 轮询 feedback → 取 result),与 nav 的模式一致;
skill 在飞期间 supervisor 可随时 `cancel_skill`(此前阻塞调用根本没有这个能力)。
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))  # 仓库根(embodied_agent)

from embodied_agent.registry import ToolRegistry

POLL_PERIOD_S = 0.005


async def _run_once(registry: ToolRegistry, args: dict, *, poll_timeout_s: float) -> dict:
    """发一次 skill goal 并轮询到终态,返回 result 字典(含 status/code/retriable)。"""
    started = await registry.call("execute_vla_skill", args, caller="skill_supervisor")
    if not started.ok:
        return {"status": "failed", "code": started.error["code"], "retriable": False,
                "terminal_reason": started.error["code"]}
    sid = started.data["skill_goal_id"]

    loop = asyncio.get_event_loop()
    t0 = loop.time()
    while True:
        fb = await registry.call("get_skill_feedback", {"skill_goal_id": sid},
                                 caller="skill_supervisor", poll=True)
        if fb.ok and fb.data["status"] != "executing":
            break
        if loop.time() - t0 > poll_timeout_s:      # 兜底:在飞太久 → 主动取消,不悬挂
            await registry.call("cancel_skill", {"skill_goal_id": sid},
                                caller="skill_supervisor")
            break
        await asyncio.sleep(POLL_PERIOD_S)

    res = await registry.call("get_skill_result", {"skill_goal_id": sid},
                              caller="skill_supervisor")
    if not res.ok:
        return {"status": "failed", "code": res.error["code"], "retriable": False,
                "terminal_reason": res.error["code"]}
    return res.data


async def run_skill_supervised(registry: ToolRegistry, args: dict, *,
                               max_retries: int = 2,
                               poll_timeout_s: float = 30.0) -> dict:
    log = registry.log
    last_code = None
    for attempt in range(max_retries + 1):
        r = await _run_once(registry, args, poll_timeout_s=poll_timeout_s)
        if r["status"] == "succeeded":
            log.emit("skill_supervisor", "skill_ok", attempt=attempt,
                     safety_interventions=r.get("safety_interventions"),
                     steps=r.get("steps"))
            return {"outcome": "succeeded", "attempts": attempt + 1, **r}
        last_code = r.get("code")
        if last_code == "VLA_UNSAFE_STOP":            # 安全:不重试,立即上浮
            log.emit("skill_supervisor", "escalate_unsafe", attempt=attempt,
                     code=last_code, reason=r.get("terminal_reason"))
            return {"outcome": "aborted_unsafe", "attempts": attempt + 1, "code": last_code}
        if r.get("retriable") and attempt < max_retries:
            log.emit("skill_supervisor", "retry", attempt=attempt, code=last_code)
            continue
        break
    log.emit("skill_supervisor", "escalate_exhausted", attempts=max_retries + 1, code=last_code)
    return {"outcome": "escalated", "attempts": max_retries + 1, "code": last_code}
