#!/usr/bin/env python3
"""ExecuteVLASkill ROS 2 Action 端到端 smoke(容器内跑,需已 source vla_skill_interfaces)。

验证【同一契约在真实 ROS 2 action 传输上成立】,与 in-process goal-handle 测试
(phase_d/test_skill_server.py)逐条对应:

  [A] baseline:goal 接受 → 在飞收到 feedback → result succeeded / grasped。
  [B] cancel:在飞 cancel_goal_async → 终态 canceled + code=VLA_CANCELED + retriable=False
      (在飞取消正是旧阻塞实现做不到的能力,现在在 wire 上也成立)。
  [C] unsafe:shield must_stop → result failed + code=VLA_UNSAFE_STOP + retriable=False。
  [D] 契约版本门:policy_contract_version="99.0"(major 不符)→ goal 直接被拒(accepted=False)。

跑法(容器内):bash /host/phase_d/ros2/build_and_smoke.sh
"""
from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import rclpy
from action_msgs.msg import GoalStatus
from rclpy.action import ActionClient
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node

from vla_skill_interfaces.action import ExecuteVLASkill

from policy_contract import POLICY_CONTRACT_VERSION
from vla_skill_action_server import VLASkillActionServer

WAIT_S = 30.0


def _wait(fut, deadline_s=WAIT_S, what="future"):
    t0 = time.monotonic()
    while not fut.done():
        if time.monotonic() - t0 > deadline_s:
            raise TimeoutError(f"{what} 在 {deadline_s}s 内未完成")
        time.sleep(0.01)
    return fut.result()


class _Case:
    """一个场景一对(server, client)节点 + 后台 executor;用完整体销毁,避免同名 action 并存。"""

    def __init__(self, scenario: str, tag: str):
        self.server = VLASkillActionServer(scenario, node_name=f"srv_{tag}")
        self.client_node = Node(f"cli_{tag}")
        self.client = ActionClient(self.client_node, ExecuteVLASkill, "execute_vla_skill")
        self.ex = MultiThreadedExecutor(num_threads=4)
        self.ex.add_node(self.server)
        self.ex.add_node(self.client_node)
        self.spin_thread = threading.Thread(target=self.ex.spin, daemon=True)
        self.spin_thread.start()
        assert self.client.wait_for_server(timeout_sec=10.0), "action server 未上线"
        self.feedbacks: list = []

    def send(self, *, version: str = POLICY_CONTRACT_VERSION, timeout_s: float = 8.0):
        goal = ExecuteVLASkill.Goal()
        goal.instruction = "pick up the red block"
        goal.skill_id = "tabletop_pick"
        goal.timeout_s = float(timeout_s)
        goal.policy_contract_version = version
        fut = self.client.send_goal_async(
            goal, feedback_callback=lambda m: self.feedbacks.append(m.feedback))
        return _wait(fut, what="send_goal")

    def close(self):
        self.ex.shutdown()
        self.server.destroy_node()
        self.client_node.destroy_node()


def main() -> None:
    fails: list[str] = []
    rclpy.init()

    # [A] baseline
    c = _Case("baseline", "a")
    try:
        gh = c.send()
        if not gh.accepted:
            fails.append("[A] goal 未被接受")
        else:
            res = _wait(gh.get_result_async(), what="result[A]")
            r, st = res.result, res.status
            print(f"[A] status={r.status} reason={r.terminal_reason} steps={r.steps} "
                  f"feedbacks={len(c.feedbacks)} goal_status={st}")
            if r.status != "succeeded" or r.terminal_reason != "grasped":
                fails.append(f"[A] 应 succeeded/grasped,实得 {r.status}/{r.terminal_reason}")
            if st != GoalStatus.STATUS_SUCCEEDED:
                fails.append(f"[A] action 状态应 SUCCEEDED,实得 {st}")
            if not c.feedbacks:
                fails.append("[A] 在飞未收到任何 feedback")
    finally:
        c.close()

    # [B] 在飞取消。用 unreachable(执行窗口宽,不会几百毫秒就自然成功)+【以首条 feedback 为
    # "确已在飞"的确定性信号】再取消 —— 固定 sleep 会跟 baseline 的自然完成赛跑(实测踩过:
    # baseline ~0.3s 就 grasped,cancel 晚到,server 如实报 succeeded 反而判我失败)。
    c = _Case("unreachable", "b")
    try:
        gh = c.send(timeout_s=30.0)
        assert gh.accepted
        t0 = time.monotonic()
        while not c.feedbacks:                            # 首条 feedback = 确已在飞
            if time.monotonic() - t0 > 10.0:
                raise TimeoutError("[B] 未收到任何 feedback")
            time.sleep(0.01)
        _wait(gh.cancel_goal_async(), what="cancel[B]")
        res = _wait(gh.get_result_async(), what="result[B]")
        r, st = res.result, res.status
        print(f"[B] status={r.status} code={r.code} retriable={r.retriable} goal_status={st}")
        if r.status != "canceled" or r.code != "VLA_CANCELED" or r.retriable:
            fails.append(f"[B] 应 canceled/VLA_CANCELED/retriable=False,实得 "
                         f"{r.status}/{r.code}/{r.retriable}")
        if st != GoalStatus.STATUS_CANCELED:
            fails.append(f"[B] action 状态应 CANCELED,实得 {st}")
    finally:
        c.close()

    # [C] unsafe → 安全停
    c = _Case("unsafe", "c")
    try:
        gh = c.send()
        assert gh.accepted
        res = _wait(gh.get_result_async(), what="result[C]")
        r, st = res.result, res.status
        print(f"[C] status={r.status} code={r.code} reason={r.terminal_reason} goal_status={st}")
        if r.status != "failed" or r.code != "VLA_UNSAFE_STOP" or r.retriable \
                or not r.terminal_reason.startswith("must_stop"):
            fails.append(f"[C] 应 failed/VLA_UNSAFE_STOP/must_stop*,实得 "
                         f"{r.status}/{r.code}/{r.terminal_reason}")
        if st != GoalStatus.STATUS_ABORTED:
            fails.append(f"[C] action 状态应 ABORTED,实得 {st}")
    finally:
        c.close()

    # [D] 契约版本门:major 不符 → goal 拒收
    c = _Case("baseline", "d")
    try:
        gh = c.send(version="99.0")
        print(f"[D] accepted={gh.accepted}")
        if gh.accepted:
            fails.append("[D] major 不符的契约版本竟被接受")
    finally:
        c.close()

    rclpy.shutdown()
    if fails:
        print("FAIL:\n  - " + "\n  - ".join(fails))
        sys.exit(3)
    print("PASS(ROS2 Action): 同一 skill 契约在真实 ROS 2 action 传输上成立 —— "
          "goal/feedback/cancel/result 同构,安全停语义与契约版本门皆生效。")
    sys.exit(0)


if __name__ == "__main__":
    main()
