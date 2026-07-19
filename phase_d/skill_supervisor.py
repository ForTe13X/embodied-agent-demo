"""Skill Supervisor —— learned-skill 的恢复归属层(docs/RECOVERY_OWNERSHIP.md 的"连续抓取失败"行)。

夹在编排层与 VLA skill 之间:skill 失败时按【终态原因】决定重试还是上浮:
  · 安全停(VLA_UNSAFE_STOP)→ 【绝不重试】,立即上浮编排层(安全类,RECOVERY_OWNERSHIP §1.4);
  · 可重试失败(no_progress / timeout)→ 重试至多 max_retries 次;仍失败 → 上浮编排层;
  · 其它 → 上浮。
它不自己"发明"恢复,只做"该重试就重试、超出能力就上浮"这一层归属决策,并全程写共享事件日志。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))  # 仓库根(embodied_agent)

from embodied_agent.registry import ToolRegistry


async def run_skill_supervised(registry: ToolRegistry, args: dict, *,
                               max_retries: int = 2) -> dict:
    log = registry.log
    last_code = None
    for attempt in range(max_retries + 1):
        res = await registry.call("execute_vla_skill", args, caller="skill_supervisor")
        if res.ok:
            log.emit("skill_supervisor", "skill_ok", attempt=attempt,
                     safety_interventions=res.data.get("safety_interventions"),
                     steps=res.data.get("steps"))
            return {"outcome": "succeeded", "attempts": attempt + 1, **res.data}
        last_code = res.error["code"]
        if last_code == "VLA_UNSAFE_STOP":            # 安全:不重试,立即上浮
            log.emit("skill_supervisor", "escalate_unsafe", attempt=attempt,
                     code=last_code, reason=res.error.get("message"))
            return {"outcome": "aborted_unsafe", "attempts": attempt + 1, "code": last_code}
        if last_code in ("VLA_NO_PROGRESS", "VLA_TIMEOUT") and attempt < max_retries:
            log.emit("skill_supervisor", "retry", attempt=attempt, code=last_code)
            continue
        break
    log.emit("skill_supervisor", "escalate_exhausted", attempts=max_retries + 1, code=last_code)
    return {"outcome": "escalated", "attempts": max_retries + 1, "code": last_code}
