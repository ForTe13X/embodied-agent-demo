"""RobotAdapter 契约 + Mock 实现。

同一接口在 Phase B 换成 rclpy 适配器(见 docs/ADAPTER_CONTRACT.md 的逐状态推导规则)。
接口是异步 goal-handle 式(评审 B1):send_goal 立即返回,feedback/cancel 在飞可用。
wait() 是时间推进原语:mock 下推进虚拟时钟并驱动世界,rclpy 下就是真实 sleep。
"""
from __future__ import annotations

import random
from typing import Optional, Protocol

from .clock import SimClock
from .faults import FaultInjector
from .mock_server import MockNavServer
from .world import DOCK, World


class SensorUnhealthy(Exception):
    pass


class RobotAdapter(Protocol):
    async def send_goal(self, target: str, *, authorized: bool = False,
                        avoid_edges: set = frozenset(),
                        restricted_ok_nodes: set = frozenset(),
                        allow_all_restricted: bool = False,
                        allow_forbidden_target: bool = False) -> dict: ...
    async def feedback(self, goal_id: str) -> Optional[dict]: ...
    async def result(self, goal_id: str) -> Optional[dict]: ...
    async def cancel(self, goal_id: str) -> bool: ...
    async def get_state(self) -> dict: ...
    async def get_map(self) -> dict: ...
    async def sense(self, query: str) -> dict: ...
    async def capture(self) -> dict: ...
    async def wait(self, ticks: int = 1) -> None: ...


class MockAdapter:
    def __init__(
        self,
        clock: SimClock,
        world: World,
        server: MockNavServer,
        injector: FaultInjector,
        rng_sense: random.Random,
    ):
        self.clock = clock
        self.world = world
        self.server = server
        self.injector = injector
        self.rng_sense = rng_sense
        self._image_counter = 0

    async def send_goal(self, target: str, *, authorized: bool = False,
                        avoid_edges: set = frozenset(),
                        restricted_ok_nodes: set = frozenset(),
                        allow_all_restricted: bool = False,
                        allow_forbidden_target: bool = False) -> dict:
        return self.server.send_goal(
            target, authorized=authorized, avoid_edges=avoid_edges,
            restricted_ok_nodes=restricted_ok_nodes,
            allow_all_restricted=allow_all_restricted,
            allow_forbidden_target=allow_forbidden_target)

    async def feedback(self, goal_id: str) -> Optional[dict]:
        return self.server.feedback(goal_id)

    async def result(self, goal_id: str) -> Optional[dict]:
        return self.server.result(goal_id)

    async def cancel(self, goal_id: str) -> bool:
        return self.server.cancel(goal_id)

    async def get_state(self) -> dict:
        active = self.server._active
        return {
            "pose": self.world.robot_node,
            "battery_pct": round(self.world.battery_pct, 2),
            "nav_status": "executing" if active else "idle",
            "sensor_health": self.world.sensor_healthy,
            "docked": self.world.robot_node == DOCK and active is None,
        }

    async def get_map(self) -> dict:
        return self.world.topo.to_dict()

    async def sense(self, query: str) -> dict:
        if not self.world.sensor_healthy:
            raise SensorUnhealthy("SENSOR_UNHEALTHY")
        objects = []
        label = self.world.anomalies.get(self.world.robot_node)
        if label:
            conf = round(0.78 + self.rng_sense.uniform(0.0, 0.18), 3)
            objects.append({"label": label, "confidence": conf})
        return {"objects": objects, "at_node": self.world.robot_node}

    async def capture(self) -> dict:
        self._image_counter += 1
        return {"image_id": f"img-{self._image_counter}", "tick": self.clock.tick}

    async def wait(self, ticks: int = 1) -> None:
        for _ in range(ticks):
            await self.clock.advance(1)
            self.injector.on_tick(self.clock.tick, self.server.active_edge())
            self.server.on_tick()
