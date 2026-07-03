"""mock ROS2 action server:精确复刻 NavigateToPose 的异步 goal-handle 语义(评审 B1)。

契约(与 rclpy ActionClient 同构,见 docs/ADAPTER_CONTRACT.md):
  send_goal → goal_id(立即返回,不阻塞);
  feedback(goal_id) 在飞可轮询;cancel(goal_id) 可中途取消;
  result(goal_id) 终态后可取。

关键诚实性设计:边被阻断时 server 只是停滞(velocity=0、progress 不动),
不会主动返回 blocked——受阻必须由编排层的水位检测(feedback 停滞)发现。

server 不做任何访问级/电量门禁(它只是底盘)。门禁属于注册表;
地面真值 SafetyMonitor 在这里记录实际发生的进入事件。
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .events import EventLog
from .safety import SafetyMonitor
from .world import TopoMap, World, edge_key


class GoalStatus(str, Enum):
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    ABORTED = "aborted"
    CANCELED = "canceled"


@dataclass
class NavGoal:
    goal_id: str
    target: str
    route: list[str]
    authorized: bool
    started_tick: int
    status: GoalStatus = GoalStatus.EXECUTING
    reason: Optional[str] = None
    edge_idx: int = 0
    edge_ticks_done: int = 0
    edge_cost: int = 0
    stall_ticks: int = 0
    finished_tick: Optional[int] = None
    ticks_stalled_total: int = 0
    extra: dict = field(default_factory=dict)


class MockNavServer:
    def __init__(
        self,
        topo: TopoMap,
        world: World,
        event_log: EventLog,
        rng_nav: random.Random,
        safety: SafetyMonitor,
    ):
        self.topo = topo
        self.world = world
        self.log = event_log
        self.rng = rng_nav
        self.safety = safety
        self._goals: dict[str, NavGoal] = {}
        self._active: Optional[NavGoal] = None
        self._counter = 0

    # ---- goal-handle API -------------------------------------------------

    def send_goal(
        self,
        target: str,
        *,
        authorized: bool = False,
        avoid_edges: set = frozenset(),
        allow_restricted: bool = False,
        allow_forbidden_target: bool = False,
    ) -> dict:
        if self._active is not None:
            return {"error": "busy", "active_goal": self._active.goal_id}
        self._counter += 1
        goal_id = f"goal-{self._counter}"

        route = None
        reason = None
        if not self.topo.has(target):
            reason = "unknown_node"
        elif target in self.world.unreachable_nodes:
            reason = "unreachable"
        else:
            route = self.topo.route(
                self.world.robot_node, target,
                avoid_edges=avoid_edges,
                allow_restricted=allow_restricted,
                allow_forbidden_target=allow_forbidden_target,
            )
            if route is None:
                reason = "unreachable"

        if route is None:
            goal = NavGoal(goal_id, target, [], authorized, self.log.clock.tick,
                           status=GoalStatus.ABORTED, reason=reason,
                           finished_tick=self.log.clock.tick)
            self._goals[goal_id] = goal
            self.log.emit("server", "goal_finished", goal_id=goal_id,
                          status=goal.status.value, reason=reason)
            return {"goal_id": goal_id}

        goal = NavGoal(goal_id, target, route, authorized, self.log.clock.tick)
        goal.edge_cost = self._edge_cost(route, 0) if len(route) > 1 else 0
        self._goals[goal_id] = goal
        self._active = goal
        self.log.emit("server", "goal_accepted", goal_id=goal_id,
                      target=target, route=route)
        if len(route) == 1:  # 目标即当前位置:没有实际移动,不过安全监视
            self._finish(goal, GoalStatus.SUCCEEDED)
        else:
            # 地面真值:只有真的要出发才算"启动"(违规=实际发生的不安全事件,
            # 没动过的尝试只是拦截/错误)
            self.safety.on_goal_start(goal_id, target, authorized)
        return {"goal_id": goal_id}

    def feedback(self, goal_id: str) -> Optional[dict]:
        goal = self._goals.get(goal_id)
        if goal is None:
            return None
        route_len = max(1, len(goal.route) - 1)
        current_edge = None
        if goal.status is GoalStatus.EXECUTING and goal.edge_idx < len(goal.route) - 1:
            current_edge = [goal.route[goal.edge_idx], goal.route[goal.edge_idx + 1]]
        stalled = goal.stall_ticks > 0
        return {
            "goal_id": goal_id,
            "status": goal.status.value,
            "reason": goal.reason,
            "current_node": self.world.robot_node,
            "current_edge": current_edge,
            "edges_done": goal.edge_idx,
            "edges_total": route_len,
            "velocity": 0.0 if (stalled or goal.status is not GoalStatus.EXECUTING) else 1.0,
            "stall_ticks": goal.stall_ticks,
        }

    def result(self, goal_id: str) -> Optional[dict]:
        goal = self._goals.get(goal_id)
        if goal is None or goal.status is GoalStatus.EXECUTING:
            return None
        return {
            "goal_id": goal_id,
            "status": goal.status.value,
            "reason": goal.reason,
            "ticks": (goal.finished_tick or 0) - goal.started_tick,
        }

    def cancel(self, goal_id: str) -> bool:
        goal = self._goals.get(goal_id)
        if goal is None or goal.status is not GoalStatus.EXECUTING:
            return False
        self._finish(goal, GoalStatus.CANCELED, reason="canceled_by_client")
        return True

    def active_edge(self) -> Optional[tuple[str, str]]:
        goal = self._active
        if goal is None or goal.edge_idx >= len(goal.route) - 1:
            return None
        return (goal.route[goal.edge_idx], goal.route[goal.edge_idx + 1])

    # ---- 世界推进 ---------------------------------------------------------

    def on_tick(self) -> None:
        goal = self._active
        moving = goal is not None
        if self.world.robot_node == "dock" and not moving:
            self.world.charge()
        else:
            self.world.decay(moving=moving)

        if goal is None:
            return
        if self.world.battery_pct <= 0.0:
            self._finish(goal, GoalStatus.ABORTED, reason="battery_dead")
            return

        a, b = goal.route[goal.edge_idx], goal.route[goal.edge_idx + 1]
        if edge_key(a, b) in self.world.blocked_edges:
            goal.stall_ticks += 1
            goal.ticks_stalled_total += 1
            return
        goal.stall_ticks = 0
        goal.edge_ticks_done += 1
        if goal.edge_ticks_done < goal.edge_cost:
            return
        # 到达下一节点
        self.world.robot_node = b
        self.safety.on_node_entered(goal.goal_id, b, goal.authorized)
        goal.edge_idx += 1
        goal.edge_ticks_done = 0
        if b == goal.target:
            self._finish(goal, GoalStatus.SUCCEEDED)
        else:
            goal.edge_cost = self._edge_cost(goal.route, goal.edge_idx)

    # ---- 内部 -------------------------------------------------------------

    def _edge_cost(self, route: list[str], idx: int) -> int:
        base = self.topo.cost(route[idx], route[idx + 1])
        return base + self.rng.choice([0, 0, 1])  # seed 控制的耗时抖动

    def _finish(self, goal: NavGoal, status: GoalStatus, reason: Optional[str] = None) -> None:
        goal.status = status
        goal.reason = reason
        goal.finished_tick = self.log.clock.tick
        if self._active is goal:
            self._active = None
        self.log.emit("server", "goal_finished", goal_id=goal.goal_id,
                      status=status.value, reason=reason,
                      final_node=self.world.robot_node)
