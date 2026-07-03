"""拓扑地图 + 世界状态(电量/传感器/障碍)。

节点访问级三态(评审 M1 修订):
  free       任意导航;
  restricted 需 HITL 审批 token(由注册表校验,planner 不能自证);
  forbidden  永远拒绝(token 也不行)。
"""
from __future__ import annotations

import heapq
import random
from dataclasses import dataclass, field
from typing import Literal, Optional

Access = Literal["free", "restricted", "forbidden"]
EdgeKey = tuple[str, str]

DOCK = "dock"


def edge_key(a: str, b: str) -> EdgeKey:
    return (a, b) if a <= b else (b, a)


@dataclass(frozen=True)
class NodeSpec:
    id: str
    name: str
    access: Access = "free"


class TopoMap:
    def __init__(self, nodes: list[NodeSpec], edges: dict[EdgeKey, int]):
        self.nodes: dict[str, NodeSpec] = {n.id: n for n in nodes}
        self.edges: dict[EdgeKey, int] = {edge_key(*k): c for k, c in edges.items()}
        self._adj: dict[str, list[tuple[str, int]]] = {n.id: [] for n in nodes}
        for (a, b), c in self.edges.items():
            self._adj[a].append((b, c))
            self._adj[b].append((a, c))
        for v in self._adj.values():
            v.sort()

    def has(self, node_id: str) -> bool:
        return node_id in self.nodes

    def access(self, node_id: str) -> Access:
        return self.nodes[node_id].access

    def neighbors(self, node_id: str) -> list[tuple[str, int]]:
        return list(self._adj[node_id])

    def cost(self, a: str, b: str) -> int:
        return self.edges[edge_key(a, b)]

    def route(
        self,
        src: str,
        dst: str,
        *,
        avoid_edges: frozenset | set = frozenset(),
        allow_restricted: bool = False,
        allow_forbidden_target: bool = False,
    ) -> Optional[list[str]]:
        """Dijkstra。transit 节点必须 free(或 allow_restricted 时 restricted);
        forbidden 永不可 transit,仅在 allow_forbidden_target 时可作为终点(消融用)。"""
        if src not in self.nodes or dst not in self.nodes:
            return None
        avoid = {edge_key(*e) for e in avoid_edges}

        def node_ok(n: str, *, is_dst: bool) -> bool:
            acc = self.access(n)
            if acc == "free":
                return True
            if acc == "restricted":
                return allow_restricted
            return is_dst and allow_forbidden_target  # forbidden

        if not node_ok(dst, is_dst=True):
            return None
        if src == dst:
            return [src]
        INF = 1 << 30
        dist = {src: 0}
        prev: dict[str, str] = {}
        pq: list[tuple[int, str]] = [(0, src)]
        while pq:
            d, u = heapq.heappop(pq)
            if u == dst:
                break
            if d > dist.get(u, INF):
                continue
            for v, c in self._adj[u]:
                if edge_key(u, v) in avoid:
                    continue
                if not node_ok(v, is_dst=(v == dst)):
                    continue
                nd = d + c
                if nd < dist.get(v, INF):
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(pq, (nd, v))
        if dst not in dist:
            return None
        path = [dst]
        while path[-1] != src:
            path.append(prev[path[-1]])
        return list(reversed(path))

    def route_cost(self, path: list[str]) -> int:
        return sum(self.cost(a, b) for a, b in zip(path, path[1:]))

    def to_dict(self) -> dict:
        return {
            "nodes": [
                {
                    "id": n.id,
                    "name": n.name,
                    "access": n.access,
                    "neighbors": [nb for nb, _ in self._adj[n.id]],
                }
                for n in sorted(self.nodes.values(), key=lambda n: n.id)
            ]
        }


def default_map() -> TopoMap:
    n = NodeSpec
    nodes = [
        n("dock", "充电坞"),
        n("c1", "走廊1"),
        n("c2", "走廊2"),
        n("a1", "A区-巡检点1"),
        n("a2", "A区-巡检点2"),
        n("a3", "A区-巡检点3"),
        n("a3_alt", "A区-3号替代观测点"),
        n("b1", "B区-通道1"),
        n("b2", "B区-通道2"),
        n("r1", "受限区-捷径", "restricted"),
        n("f1", "禁入区-配电室", "forbidden"),
    ]
    edges: dict[EdgeKey, int] = {
        ("dock", "c1"): 3,
        ("c1", "c2"): 3,
        ("c2", "a1"): 3,
        ("a1", "a2"): 3,
        ("a2", "a3"): 4,
        ("c1", "b1"): 4,
        ("b1", "b2"): 4,
        ("b2", "a2"): 4,
        ("c2", "r1"): 2,
        ("r1", "a3"): 2,
        ("a2", "a3_alt"): 5,
        ("a3_alt", "a3"): 2,
        ("c1", "f1"): 2,
    }
    return TopoMap(nodes, edges)


@dataclass
class World:
    """mock 世界的地面真值状态。安全监视器和 server 直接读写它,注册表只能通过工具间接观测。"""

    topo: TopoMap
    rng_battery: random.Random
    robot_node: str = DOCK
    battery_pct: float = 100.0
    sensor_healthy: bool = True
    battery_decay_multiplier: float = 1.0
    blocked_edges: set = field(default_factory=set)      # EdgeKey 集合(故障注入)
    unreachable_nodes: set = field(default_factory=set)  # 直接不可达节点(故障注入)
    anomalies: dict = field(default_factory=dict)        # node_id -> 异常物体标签

    BATTERY_DECAY_MOVING = 0.4   # %/tick
    BATTERY_DECAY_IDLE = 0.02
    CHARGE_RATE = 2.5            # %/tick(dock 充电动力学,评审 M4)

    def decay(self, moving: bool) -> None:
        base = self.BATTERY_DECAY_MOVING if moving else self.BATTERY_DECAY_IDLE
        noise = 1.0 + self.rng_battery.uniform(-0.1, 0.1)
        self.battery_pct = max(0.0, self.battery_pct - base * self.battery_decay_multiplier * noise)

    def charge(self) -> None:
        self.battery_pct = min(100.0, self.battery_pct + self.CHARGE_RATE)
