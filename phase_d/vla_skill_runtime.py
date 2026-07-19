"""VLASkillRuntime —— 异步 action-chunk 运行时(review §六)。

它把一个不稳定的 learned policy 变成可执行的 skill:
  · inference 与 execution 并行(queue 快空时提前发下一次推理);
  · observation 带序号,**过期(stale)chunk 直接丢弃**,绝不执行陈旧动作;
  · **queue 空就 hold**(保持不动),绝不复用任意旧动作;
  · **每一个动作都过 SafetyShield**(见 safety_shield.py 的结构性保证);
  · cancel / 任务切换 / must_stop 使在飞结果立即失效;
  · no-progress / timeout 有兜底终态。
每一步都写事件日志(append-only,可回放、可审计)—— 这是"失败数据飞轮"的地基。

对上层(Mission Executive / Tool Registry):它就是【一个 skill】ExecuteVLASkill,只暴露
running / progress / fault / succeeded / failed,不逐帧暴露关节动作(review §七)。
"""
from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field
from typing import Optional

from action_types import Observation
from mock_vla_policy import MockVLAPolicy
from safety_shield import SafetyShield
from tabletop_sim import TabletopSim

STALE_TOLERANCE = 2          # chunk 基于的 obs 比当前落后超过此值 = 过期
CONTROLLER_PERIOD_S = 0.01   # 执行步周期(sim 里就是循环节流)
NO_PROGRESS_STEPS = 40       # 连续这么多步不接近目标 = 无进展


@dataclass
class SkillGoal:
    mission_id: str
    instruction: str
    skill_id: str = "tabletop_pick"
    timeout_s: float = 8.0
    execution_horizon: int = 3   # queue 低于此值就提前推理


@dataclass
class SkillResult:
    success: bool
    terminal_reason: str
    safety_interventions: int = 0     # clamp 次数
    stale_drops: int = 0
    inference_calls: int = 0
    steps: int = 0
    elapsed_s: float = 0.0


@dataclass
class _Clock:
    """可注入的单调时钟(测试用 fake,生产用 time.monotonic)。"""
    fn: object = field(default=time.monotonic)
    def now(self) -> float: return self.fn()


class VLASkillRuntime:
    def __init__(self, policy: MockVLAPolicy, shield: SafetyShield, sim: TabletopSim,
                 *, inference_latency_s: float = 0.0, clock: _Clock | None = None,
                 events: list | None = None, emit_fn=None):
        self.policy = policy
        self.shield = shield
        self.sim = sim
        self.inference_latency_s = inference_latency_s   # 模拟推理耗时(测 stale 用)
        self.clock = clock or _Clock()
        self.events = events if events is not None else []
        self.emit_fn = emit_fn      # 可选:把 skill 事件转发进共享事件日志(Phase D-2 编排集成)
        self._cancelled = False
        self._seq = 0

    def cancel(self) -> None:
        self._cancelled = True

    def _emit(self, etype: str, **payload) -> None:
        self.events.append({"seq": len(self.events), "t": round(self.clock.now(), 4),
                            "actor": "vla_skill_runtime", "event_type": etype, "payload": payload})
        if self.emit_fn is not None:
            self.emit_fn(etype, **payload)

    async def _infer(self, goal: SkillGoal, obs: Observation):
        if self.inference_latency_s > 0:
            await asyncio.sleep(self.inference_latency_s)
        return self.policy.predict_chunk(obs, goal.mission_id)

    def _target_dist(self) -> float:
        t = self.policy.cfg.target_pos
        return math.sqrt(sum((a - b) ** 2 for a, b in zip(self.sim.ee.pos, t)))

    async def execute(self, goal: SkillGoal) -> SkillResult:
        t0 = self.clock.now()
        res = SkillResult(success=False, terminal_reason="")
        queue: list = []
        inference_task: Optional[asyncio.Task] = None
        best_dist = None
        no_progress = 0
        self._emit("skill_started", mission_id=goal.mission_id, instruction=goal.instruction)

        while True:
            if self._cancelled:
                if inference_task:
                    inference_task.cancel()
                self.sim.hold_position()
                res.terminal_reason = "canceled"
                self._emit("skill_canceled")
                break
            if self.clock.now() - t0 > goal.timeout_s:
                self.sim.hold_position()
                res.terminal_reason = "timeout"
                self._emit("skill_timeout")
                break

            self._seq += 1
            obs = Observation(seq=self._seq, t=self.clock.now(), ee=self.sim.ee)

            # 1) queue 快空 → 提前发下一次推理(与执行并行)
            if len(queue) < goal.execution_horizon and inference_task is None:
                inference_task = asyncio.create_task(self._infer(goal, obs))
                res.inference_calls += 1

            # 2) 推理完成 → 校验是否过期,过期丢弃
            if inference_task is not None and inference_task.done():
                chunk = inference_task.result()
                inference_task = None
                stale = (chunk.mission_id != goal.mission_id
                         or chunk.observation_seq < self._seq - STALE_TOLERANCE)
                if stale:
                    res.stale_drops += 1
                    self._emit("chunk_dropped_stale", based_on=chunk.observation_seq, now=self._seq)
                else:
                    queue.extend(chunk.actions)
                    self._emit("chunk_accepted", n=len(chunk.actions), based_on=chunk.observation_seq)

            # 3) queue 空 → hold,绝不复用旧动作
            if not queue:
                self.sim.hold_position()
                self._emit("hold_empty_queue")
                await asyncio.sleep(CONTROLLER_PERIOD_S)
                continue

            # 4) 取一个动作,过 shield
            raw = queue.pop(0)
            safe, info = self.shield.project(raw, self.sim.ee)
            if info.any_clamp:
                res.safety_interventions += 1
                self._emit("safety_clamped", correction=round(info.correction, 4),
                           workspace=info.clamped_workspace, translation=info.clamped_translation)
            if info.must_stop:
                self.sim.emergency_stop()
                res.terminal_reason = f"must_stop:{info.reason}"
                self._emit("emergency_stop", reason=info.reason)
                break

            # 5) 送控制器(唯一执行入口,只收 SafeAction)
            self.sim.send(safe)
            res.steps += 1

            # 6) 到达后置条件?(玩具:抓住方块)
            if self.sim.block.grasped:
                res.success = True
                res.terminal_reason = "grasped"
                self._emit("skill_succeeded", steps=res.steps)
                break

            # 7) 无进展兜底
            d = self._target_dist()
            if best_dist is None or d < best_dist - 1e-4:
                best_dist = d
                no_progress = 0
            else:
                no_progress += 1
                if no_progress >= NO_PROGRESS_STEPS:
                    self.sim.hold_position()
                    res.terminal_reason = "no_progress"
                    self._emit("skill_no_progress")
                    break

            await asyncio.sleep(CONTROLLER_PERIOD_S)

        if inference_task and not inference_task.done():
            inference_task.cancel()
        res.elapsed_s = round(self.clock.now() - t0, 3)
        self._emit("skill_finished", success=res.success, reason=res.terminal_reason,
                   safety_interventions=res.safety_interventions, stale_drops=res.stale_drops,
                   inference_calls=res.inference_calls, steps=res.steps)
        return res
