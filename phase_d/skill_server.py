"""SkillServer —— VLA skill 的 goal-handle 服务端(D1)。

**为什么要有它**(codex 评审:Registry 阻塞等待 skill 终态,无外部 goal/feedback/cancel):
此前 `execute_vla_skill` 的注册表 handler 里是 `res = await rt.execute(goal)` —— 一路**阻塞**到
skill 终态。这意味着:
  · 编排层拿不到 skill 的 goal 句柄,**没法在飞取消**(唯一的取消是 runtime 内部的 `cancel()`,
    上层够不着);
  · 在飞期间**拿不到 progress**,只能等一个最终结果;
  · 与 `navigate_to` 的异步 goal-handle 契约**不对等** —— 同一个 registry 里两类 skill 语义分裂。

本服务端把 VLA skill 对齐到**与 `MockNavServer` / Nav2 `NavigateToPose` 同构**的语义:
`send_goal` 立即返回 `skill_goal_id`;`feedback` / `cancel` 在飞可用;`result` 终态后可取。
这样"可管理、可停止的 skill 边界"才成立,编排层对 Nav 和 VLA 用同一套异步模式。

生命周期由 asyncio.Task 承载:任务在后台跑 `VLASkillRuntime.execute()`,服务端只做句柄记账。
"""
from __future__ import annotations

import asyncio
from typing import Callable, Optional

from vla_skill_runtime import SkillGoal, VLASkillRuntime


# 终态原因 → (错误码, 是否可重试)。must_stop 是安全事件,永不重试(RECOVERY_OWNERSHIP §1.4)。
FAIL_CODE = {
    "no_progress": ("VLA_NO_PROGRESS", True),
    "timeout": ("VLA_TIMEOUT", True),
    "canceled": ("VLA_CANCELED", False),
}


def classify(terminal_reason: str) -> tuple[str, bool]:
    """把终态原因映射成 (错误码, 可重试)。安全停单独归类,供 Skill Supervisor 判"不该重试"。"""
    if terminal_reason.startswith("must_stop"):
        return "VLA_UNSAFE_STOP", False
    return FAIL_CODE.get(terminal_reason, ("VLA_FAILED", False))


class SkillServer:
    """VLA skill 的异步 goal-handle 服务端。与 MockNavServer 同构:一次只跑一个在飞 goal。"""

    def __init__(self, runtime_factory: Callable[[], VLASkillRuntime]):
        self._factory = runtime_factory
        self._seq = 0
        self._handles: dict[str, dict] = {}
        self._active: Optional[str] = None

    # ---- goal-handle API --------------------------------------------------

    def send_goal(self, goal: SkillGoal) -> dict:
        """立即返回 skill_goal_id;skill 在后台 asyncio.Task 里跑,不阻塞调用方。"""
        if self._active is not None:
            self._reap(self._active, self._handles[self._active])   # 兜底:进门先收尸
        if self._active is not None:
            return {"error": "busy", "active_goal": self._active}
        self._seq += 1
        sid = f"skill-{self._seq}"
        rt = self._factory()
        task = asyncio.create_task(rt.execute(goal))
        self._handles[sid] = {"rt": rt, "task": task, "goal": goal,
                              "terminal": None, "cancel_requested": False}
        self._active = sid
        # 在飞锁必须由【任务自身完成】释放,不能依赖调用方"记得再轮询一次"。
        # 复审实测:watchdog/poll_failures 取消后不再轮询 → _reap 永不触发 → _active 永久占用
        # → 后续所有 skill goal 恒 SKILL_BUSY(被静默降级跳过)。done-callback 是唯一
        # 不依赖调用方礼貌的释放点。
        task.add_done_callback(lambda _t, s=sid: self._reap(s, self._handles[s]))
        return {"skill_goal_id": sid}

    def feedback(self, sid: str) -> Optional[dict]:
        """在飞进度 / 终态。未知句柄返回 None(调用方据此报 UNKNOWN_GOAL)。"""
        h = self._handles.get(sid)
        if h is None:
            return None
        self._reap(sid, h)
        live = h["rt"].live
        if h["terminal"] is not None:
            t = h["terminal"]
            return {"skill_goal_id": sid, "status": t["status"],
                    "terminal_reason": t["terminal_reason"], "steps": t["steps"],
                    "safety_interventions": t["safety_interventions"],
                    "stale_drops": t["stale_drops"]}
        return {"skill_goal_id": sid, "status": "executing", "terminal_reason": None,
                "steps": getattr(live, "steps", 0),
                "safety_interventions": getattr(live, "safety_interventions", 0),
                "stale_drops": getattr(live, "stale_drops", 0)}

    def result(self, sid: str) -> Optional[dict]:
        """终态后返回结果;仍在飞返回 None(与 nav 的 result 语义一致)。"""
        h = self._handles.get(sid)
        if h is None:
            return None
        self._reap(sid, h)
        return h["terminal"]

    def cancel(self, sid: str) -> bool:
        """在飞取消。已终态返回 False(与 MockNavServer.cancel 一致)。"""
        h = self._handles.get(sid)
        if h is None:
            return False
        self._reap(sid, h)
        if h["terminal"] is not None:
            return False
        h["cancel_requested"] = True
        h["rt"].cancel()          # runtime 下一拍看到标志 → hold + 终态 canceled
        return True

    def sim_of(self, sid: str):
        """取该 goal 的 sim —— 供**独立 postcheck** 直接读末态,而不是复用 skill 自报的 success。"""
        h = self._handles.get(sid)
        return None if h is None else h["rt"].sim

    # ---- 内部 -------------------------------------------------------------

    def _reap(self, sid: str, h: dict) -> None:
        """任务完成即收敛为终态(幂等),并释放在飞锁。"""
        if h["terminal"] is not None or not h["task"].done():
            return
        try:
            res = h["task"].result()
            code, retriable = (None, False) if res.success else classify(res.terminal_reason)
            h["terminal"] = {
                "skill_goal_id": sid,
                "status": "succeeded" if res.success else "failed",
                "terminal_reason": res.terminal_reason,
                "code": code, "retriable": retriable,
                "steps": res.steps, "safety_interventions": res.safety_interventions,
                "stale_drops": res.stale_drops, "inference_calls": res.inference_calls,
                "elapsed_s": res.elapsed_s,
            }
        except asyncio.CancelledError:
            h["terminal"] = {"skill_goal_id": sid, "status": "failed",
                             "terminal_reason": "canceled", "code": "VLA_CANCELED",
                             "retriable": False, "steps": 0, "safety_interventions": 0,
                             "stale_drops": 0, "inference_calls": 0, "elapsed_s": 0.0}
        except Exception as e:                      # runtime 内部异常也要收敛成终态,不悬挂
            h["terminal"] = {"skill_goal_id": sid, "status": "failed",
                             "terminal_reason": f"runtime_error:{type(e).__name__}",
                             "code": "VLA_RUNTIME_ERROR", "retriable": False,
                             "steps": 0, "safety_interventions": 0, "stale_drops": 0,
                             "inference_calls": 0, "elapsed_s": 0.0}
        if self._active == sid:
            self._active = None
