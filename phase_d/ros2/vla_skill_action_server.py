#!/usr/bin/env python3
"""ExecuteVLASkill 的 ROS 2 Action server —— 把 D1 的异步 skill goal-handle 契约映射到真实 ROS 2 传输。

映射关系(同一契约、两种传输):
  in-process(phase_d/skill_server.py)      ROS 2(本文件)
  server.send_goal(goal) → skill_goal_id    ActionClient.send_goal_async → goal handle
  server.feedback(sid)                      goal_handle.publish_feedback(...)
  server.cancel(sid)                        cancel_goal_async → CancelResponse.ACCEPT → rt.cancel()
  server.result(sid)                        result future(succeed/abort/canceled + Result 消息)

设计要点:
  · runtime 的 asyncio 控制环跑在【专用线程的私有事件循环】里(asyncio.run):rclpy executor 的
    协程机制不是 asyncio,直接在回调里跑 asyncio 代码(create_task/to_thread)会炸;线程隔离两个世界。
  · execute 回调是同步的:轮询 runtime 线程,期间转发 rt.live 进度为 action feedback、
    响应 is_cancel_requested → rt.cancel()(runtime 下一拍 hold + 终态 canceled)。
  · D1 版本化 Policy Contract 上线到 wire:goal 里带 policy_contract_version,major 不符在
    goal_callback 里直接 REJECT —— 不兼容的客户端连 goal 都发不进来。
  · 场景(baseline/unsafe/unreachable)是【世界状态】,由 server 侧参数决定,不由客户端传参
    (与 vla_skill_tool.make_sim_factory 同一口径:编排层不知道抓不抓得到,这正是要监管的点)。

诚实边界:sim/policy 仍是 Phase D 的 mock(运动学 sim + 确定性桩);本文件证明的是【契约在真实
ROS 2 action 传输上成立】,不是真实操作。宿主侧 registry 的 execute_vla_skill 工具目前接的是
in-process SkillServer;把它切到 ActionClient(经本 server)是 adapter 层的后续接线,不在本轮伪造。
只在容器内(rclpy + 已 source vla_skill_interfaces)运行。
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))  # phase_d 平铺模块

import rclpy
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.node import Node

from vla_skill_interfaces.action import ExecuteVLASkill

from mock_vla_policy import MockVLAPolicy, PolicyConfig
from policy_contract import PolicyContract
from safety_shield import SafetyShield
from skill_server import classify
from tabletop_sim import Block, TabletopSim
from vla_skill_runtime import SkillGoal, VLASkillRuntime

FEEDBACK_PERIOD_S = 0.05


def make_runtime(scenario: str, contract: PolicyContract) -> VLASkillRuntime:
    """按场景造 runtime。与 vla_skill_tool.make_sim_factory 同构的独立副本 ——
    容器内不携带 embodied_agent.registry 的依赖链(pydantic 等),保持 rclpy-only。"""
    shield = SafetyShield()
    if scenario == "baseline":
        sim, policy = TabletopSim(), MockVLAPolicy(PolicyConfig(), seed=0)
    elif scenario == "unsafe":                       # policy 冲界 → shield must_stop
        sim, policy = TabletopSim(), MockVLAPolicy(PolicyConfig(inject_out_of_bounds=True), seed=0)
    elif scenario == "unreachable":                  # 目标在盒外 → no_progress
        sim = TabletopSim(block=Block(pos=(0.9, 0.0, 0.03)))
        policy = MockVLAPolicy(PolicyConfig(target_pos=(0.9, 0.0, 0.06)), seed=0)
    else:
        raise ValueError(f"unknown scenario: {scenario}")
    return VLASkillRuntime(policy, shield, sim, contract=contract)


class VLASkillActionServer(Node):
    def __init__(self, scenario: str = "baseline", *, node_name: str | None = None):
        super().__init__(node_name or f"vla_skill_server_{scenario}")
        self.scenario = scenario
        self.contract = PolicyContract()
        self._seq = 0
        self._srv = ActionServer(
            self, ExecuteVLASkill, "execute_vla_skill",
            execute_callback=self._execute,
            goal_callback=self._on_goal,
            cancel_callback=self._on_cancel,
            callback_group=ReentrantCallbackGroup())

    # ---- goal 门:契约版本不符连 goal 都进不来(D1 契约上线到 wire) ----
    def _on_goal(self, request) -> GoalResponse:
        if not self.contract.accepts_version(request.policy_contract_version):
            self.get_logger().warning(
                f"goal rejected: policy_contract_version={request.policy_contract_version!r} "
                f"与 server 契约 {self.contract.version!r} major 不符")
            return GoalResponse.REJECT
        return GoalResponse.ACCEPT

    def _on_cancel(self, _goal_handle) -> CancelResponse:
        return CancelResponse.ACCEPT

    # ---- 执行:runtime 在专用线程私有 asyncio loop,回调轮询转发 ----
    def _execute(self, goal_handle) -> ExecuteVLASkill.Result:
        req = goal_handle.request
        self._seq += 1
        rt = make_runtime(self.scenario, self.contract)
        goal = SkillGoal(mission_id=f"ros-{self._seq}",
                         instruction=req.instruction,
                         skill_id=req.skill_id or "tabletop_pick",
                         timeout_s=req.timeout_s if req.timeout_s > 0 else 8.0)

        box: dict = {}

        def run() -> None:
            try:
                box["res"] = asyncio.run(rt.execute(goal))
            except Exception as e:                     # 收敛成终态,不悬挂 action
                box["err"] = e

        th = threading.Thread(target=run, daemon=True)
        th.start()
        while th.is_alive():
            if goal_handle.is_cancel_requested:
                rt.cancel()                            # runtime 下一拍 hold + 终态 canceled
            live = rt.live
            fb = ExecuteVLASkill.Feedback()
            fb.steps = int(getattr(live, "steps", 0) or 0)
            fb.safety_interventions = int(getattr(live, "safety_interventions", 0) or 0)
            fb.stale_drops = int(getattr(live, "stale_drops", 0) or 0)
            goal_handle.publish_feedback(fb)
            time.sleep(FEEDBACK_PERIOD_S)
        th.join()

        out = ExecuteVLASkill.Result()
        if "err" in box:
            out.status, out.terminal_reason = "failed", f"runtime_error:{type(box['err']).__name__}"
            out.code, out.retriable = "VLA_RUNTIME_ERROR", False
            goal_handle.abort()
            return out
        res = box["res"]
        out.terminal_reason = res.terminal_reason
        out.steps = res.steps
        out.safety_interventions = res.safety_interventions
        out.stale_drops = res.stale_drops
        if res.success:
            out.status, out.code, out.retriable = "succeeded", "", False
            goal_handle.succeed()
        elif res.terminal_reason == "canceled" and goal_handle.is_cancel_requested:
            out.status, out.code, out.retriable = "canceled", "VLA_CANCELED", False
            goal_handle.canceled()
        else:
            code, retriable = classify(res.terminal_reason)   # 与 in-process 完全同一分类表
            out.status, out.code, out.retriable = "failed", code, retriable
            goal_handle.abort()
        return out


def main() -> None:
    scenario = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    rclpy.init()
    node = VLASkillActionServer(scenario)
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
