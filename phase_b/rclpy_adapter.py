#!/usr/bin/env python3
"""RclpyAdapter —— 与 embodied_agent.adapter.MockAdapter 同一 RobotAdapter 契约的真实实现。

设计要点(对照 docs/ADAPTER_CONTRACT.md):
  · 异步 goal-handle:send_goal 立即返回 goal_id;feedback/cancel/result 在飞可用。
  · `blocked` 不是状态:底盘只表现为 feedback 停滞(distance_remaining 不降、velocity≈0),
    受阻由上层 observer 的停滞水位判定 —— 与 mock 语义 1:1,换 adapter 检测逻辑不改。
  · node→pose 由 adapter 独占的静态 YAML(waypoints.yaml)提供;编排层永远只见 node_id。
  · 底盘不做访问级/电量门禁(注册表负责);access 故障靠 keepout 掩码在 send_goal 前改。
  · 结构性保证:本节点【不创建任何速度发布器】。assert_no_velocity_interface() 枚举本节点
    publishers,出现 cmd_vel/velocity/torque/effort 类 topic 即抛错 —— 'LLM 拿不到速度接口'
    是可核对的结构事实,不是口号。

终态收敛(评审修复):任务完成时统一经 _converge() 读一次 getResult()→映射终态→置 g['terminal']
→清 _active(幂等)。feedback 见终态即走 _terminal_fb,绝不在活跃分支里用 getResult() 推断终态,
从而:①canceled 不被误报成 aborted;②终态一旦出现立即释放 _active(不再泄漏 busy 锁);
③feedback 与 result 的 status 恒一致。

进度(edges_done):基于 send_goal 时按【access 规则】(与 world.route 同)算定的固定路线,
沿路线单调推进(max_edge_idx 不回退),不再用两条忽略 access 的 Dijkstra 相减 —— 避免 Nav2
绕开 restricted 节点时进度失真/回跳。当前节点用滞回最近邻,消除等距中点抖动。

只在容器内(有 rclpy + Nav2)运行。宿主 venv 不导入本文件。
"""
from __future__ import annotations

import heapq
import math
import re
import time
from typing import Optional

import rclpy
import rclpy.time
from geometry_msgs.msg import PoseStamped
from nav2_simple_commander.robot_navigator import BasicNavigator, TaskResult
from tf2_ros import Buffer, TransformListener

_VEL_PAT = re.compile(r"(cmd_vel|/velocity|/torque|/effort|joint_trajectory)", re.I)
_WP_PAT = re.compile(r"^\s*(\w+):\s*\{x:\s*([-\d.]+),\s*y:\s*([-\d.]+),\s*yaw:\s*([-\d.]+)")
_NODE_PAT = re.compile(
    r"^\s*(\w+):\s*\{name:\s*(.+?),\s*access:\s*(\w+),\s*neighbors:\s*\[([^\]]*)\]")
_EDGE_PAT = re.compile(r"^\s*-\s*\[(\w+),\s*(\w+),\s*(\d+)\]")


class VelocityInterfaceLeak(AssertionError):
    pass


def _load_waypoints(path):
    wp = {}
    for line in open(path, encoding="utf-8"):
        m = _WP_PAT.match(line)
        if m:
            wp[m.group(1)] = (float(m.group(2)), float(m.group(3)), float(m.group(4)))
    return wp


def _load_topo(path):
    nodes, edges = {}, []
    section = None
    for line in open(path, encoding="utf-8"):
        s = line.rstrip("\n")
        if s.startswith("nodes:"):
            section = "nodes"; continue
        if s.startswith("edges:"):
            section = "edges"; continue
        if section == "nodes":
            m = _NODE_PAT.match(s)
            if m:
                nbrs = [x.strip() for x in m.group(4).split(",") if x.strip()]
                nodes[m.group(1)] = {"name": m.group(2).strip(),
                                     "access": m.group(3), "neighbors": nbrs}
        elif section == "edges":
            m = _EDGE_PAT.match(s)
            if m:
                edges.append((m.group(1), m.group(2), int(m.group(3))))
    return nodes, edges


class RclpyAdapter:
    def __init__(self, waypoints="/hostpb/world/waypoints.yaml",
                 topo="/hostpb/world/topo.yaml", *, tick_seconds=1.0,
                 stall_eps_m=0.05, near_goal_m=0.35, cur_switch_margin_m=0.30):
        self.wp = _load_waypoints(waypoints)
        self.nodes, self.edges = _load_topo(topo)
        self._adj = self._build_adj(self.edges)
        self.tick_seconds = tick_seconds        # rclpy 下 ticks = 墙钟秒 / tick_seconds
        self.stall_eps_m = stall_eps_m          # 一拍内 distance_remaining 降幅阈值
        self.near_goal_m = near_goal_m          # 距目标 < 此值不再累加 stall(到达减速段免判)
        self.cur_switch_margin_m = cur_switch_margin_m  # 最近邻滞回:新节点近于当前超此值才切

        self.nav = BasicNavigator()             # 唯一运动接口 = 其内部 NavigateToPose ActionClient
        self.tf = Buffer()
        TransformListener(self.tf, self.nav)

        self._seq = 0
        self._active: Optional[str] = None      # 在飞 goal_id
        self._g: dict[str, dict] = {}           # goal_id -> 运行态记录
        self._cur: Optional[str] = None         # _cur_node 滞回缓存
        self._start_node: Optional[str] = None

    # ---- 结构性保证:无速度接口 -----------------------------------------
    def assert_no_velocity_interface(self) -> list[str]:
        pubs = self.nav.get_publisher_names_and_types_by_node(
            self.nav.get_name(), self.nav.get_namespace())
        topics = [t for t, _ in pubs]
        leaks = [t for t in topics if _VEL_PAT.search(t)]
        if leaks:
            raise VelocityInterfaceLeak(f"适配器泄漏了速度/力矩接口: {leaks}")
        return topics

    # ---- 生命周期引导(等价"把机器人放到起点",非运动指令) ----------------
    def bootstrap(self, start_node: str):
        x, y, yaw = self.wp[start_node]
        p = self._pose(x, y)
        p.header.stamp = self.nav.get_clock().now().to_msg()
        self.nav.setInitialPose(p)
        time.sleep(3)
        self.nav.waitUntilNav2Active(localizer="robot_localization")
        self._start_node = start_node
        self._cur = start_node

    # ---- RobotAdapter 契约 ----------------------------------------------
    async def send_goal(self, target: str, *, authorized: bool = False,
                        avoid_edges: set = frozenset(),
                        restricted_ok_nodes: set = frozenset(),
                        allow_all_restricted: bool = False,
                        allow_forbidden_target: bool = False) -> dict:
        # 注:avoid_edges/restricted_ok_nodes/allow_* 保留以与 Protocol 同签名。Phase B 里
        # 受阻边/隔离节点故障靠 keepout 掩码在 send_goal 前改(Day-3 接入);这些参数当前仅用于
        # 【进度路线的 access 感知计算】,不改变底盘是否发目标(门禁归注册表,与 mock server 一致)。
        if self._active is not None:
            return {"error": "busy", "active_goal": self._active}
        self._seq += 1
        goal_id = f"goal-{self._seq}"
        start = self._cur_node() or self._start_node
        if target not in self.wp:
            now = time.time()
            self._g[goal_id] = {"target": target, "terminal": "aborted",
                                "reason": "unknown_node", "t0": now, "finish_t": now,
                                "start": start, "route": [start] if start else [],
                                "max_edge_idx": 0, "stall": 0}
            return {"goal_id": goal_id}
        route = self._route_access(
            start, target, restricted_ok=set(restricted_ok_nodes),
            allow_all_restricted=allow_all_restricted,
            allow_forbidden_target=allow_forbidden_target,
            avoid={self._ek(*e) for e in avoid_edges})
        planned = route or self._route_access(start, target, allow_all_restricted=True,
                                              allow_forbidden_target=True) or [start, target]
        x, y, yaw = self.wp[target]
        goal = self._pose(x, y)
        goal.header.stamp = self.nav.get_clock().now().to_msg()
        # 注:BasicNavigator.goToPose 内部 spin 到 goal 被接受才返回——会短暂阻塞事件循环
        # (async 只是名义签名;真正的原生 async action future 是未来项)。codex 评审 F-05。
        accepted = self.nav.goToPose(goal)
        if not accepted:
            now = time.time()
            self._g[goal_id] = {"target": target, "terminal": "aborted",
                                "reason": "goal_rejected", "t0": now, "finish_t": now,
                                "start": start, "route": planned, "max_edge_idx": 0,
                                "stall": 0}
            return {"goal_id": goal_id}
        self._active = goal_id
        self._g[goal_id] = {"target": target, "terminal": None, "reason": None,
                            "t0": time.time(), "finish_t": None, "start": start,
                            "route": planned, "max_edge_idx": 0,
                            "last_dist": None, "stall": 0, "last_pose": None,
                            "last_t": time.time()}
        return {"goal_id": goal_id}

    async def feedback(self, goal_id: str) -> Optional[dict]:
        g = self._g.get(goal_id)
        if g is None:
            return None
        if g["terminal"] is not None:
            return self._terminal_fb(goal_id, g)
        conv = self._converge(goal_id)       # 完成即收敛(置 terminal、清 _active)
        if conv is not None:
            return self._terminal_fb(goal_id, conv)

        fb = self.nav.getFeedback()
        cur = self._cur_node()
        route = g["route"]
        edges_total = max(1, len(route) - 1)
        idx = route.index(cur) if cur in route else g["max_edge_idx"]
        g["max_edge_idx"] = min(edges_total, max(g["max_edge_idx"], idx))  # 单调、不越界
        edges_done = g["max_edge_idx"]
        current_edge = ([route[edges_done], route[edges_done + 1]]
                        if edges_done < len(route) - 1 else None)

        dist = float(fb.distance_remaining) if fb is not None else None
        vel = 0.0
        if fb is not None:
            p = fb.current_pose.pose.position
            now = time.time()
            if g["last_pose"] is not None:
                dx = p.x - g["last_pose"][0]; dy = p.y - g["last_pose"][1]
                dt = max(1e-3, now - g["last_t"])
                vel = (dx * dx + dy * dy) ** 0.5 / dt
            g["last_pose"] = (p.x, p.y); g["last_t"] = now
            # 停滞:distance_remaining 不再下降【且】离目标尚远(到达减速段不算停滞)
            if (g["last_dist"] is not None
                    and (g["last_dist"] - dist) < self.stall_eps_m
                    and dist > self.near_goal_m):
                g["stall"] += 1
            else:
                g["stall"] = 0
            g["last_dist"] = dist

        return {
            "goal_id": goal_id, "status": "executing", "reason": None,
            "current_node": cur, "current_edge": current_edge,
            "edges_done": edges_done, "edges_total": edges_total,
            "velocity": round(vel, 3), "stall_ticks": g["stall"],
            "distance_remaining": None if dist is None else round(dist, 3),
        }

    async def result(self, goal_id: str) -> Optional[dict]:
        g = self._g.get(goal_id)
        if g is None:
            return None
        if g["terminal"] is None:
            if self._converge(goal_id) is None:
                return None
        ticks = int(((g.get("finish_t") or time.time()) - g["t0"]) / self.tick_seconds)
        return {"goal_id": goal_id, "status": g["terminal"], "reason": g["reason"], "ticks": ticks}

    async def cancel(self, goal_id: str) -> bool:
        g = self._g.get(goal_id)
        if g is None or goal_id != self._active or g["terminal"] is not None:
            return False
        if self.nav.isTaskComplete():        # 已自然终止 → 收敛为真实终态,取消无效
            self._converge(goal_id)
            return False
        self.nav.cancelTask()
        g["terminal"] = "canceled"
        g["reason"] = "canceled_by_client"
        g["finish_t"] = time.time()
        self._active = None
        return True

    async def get_state(self) -> dict:
        cur = self._cur_node()
        idle = self._active is None
        return {
            "pose": cur,
            "nav_status": "idle" if idle else "executing",
            "docked": cur == "dock" and idle,
            # loopback 无耗电/传感器地面真值:battery 恒报额定 100(无耗电模型)、sensor 恒 healthy。
            # 报 float 100.0(而非 None)以保持契约类型,使上层电量水位比较不崩;此为 mock-only 语义
            # (ADAPTER_CONTRACT §5),真实机上由 BMS/诊断替换。
            "battery_pct": 100.0,
            "sensor_health": True,
            "_note": "battery/sensor 为 mock-only:loopback 无耗电/传感器模型,恒报 100/healthy",
        }

    async def get_map(self) -> dict:
        return {"nodes": [
            {"id": nid, "name": self.nodes[nid].get("name", nid),
             "access": self.nodes[nid]["access"],
             "neighbors": self.nodes[nid]["neighbors"]}
            for nid in sorted(self.nodes)]}

    async def sense(self, query: str) -> dict:
        # loopback 无相机;真实 VLM 感知走 Godot POV 管线(想法1)。诚实返回空。
        return {"objects": [], "at_node": self._cur_node(),
                "_note": "loopback 无相机;VLM 感知见 Godot POV/VLM 管线"}

    async def capture(self) -> dict:
        return {"image_id": None, "_note": "loopback 无相机"}

    async def wait(self, ticks: int = 1) -> None:
        end = time.time() + ticks * self.tick_seconds
        while time.time() < end:
            rclpy.spin_once(self.nav, timeout_sec=0.05)

    def shutdown(self):
        try:
            self.nav.lifecycleShutdown()
        except Exception:
            pass

    # ---- 内部工具 --------------------------------------------------------
    def _converge(self, goal_id: str) -> Optional[dict]:
        """任务完成时统一收敛:读一次 getResult 映射终态、置 terminal、清 _active。幂等。
        未完成返回 None。"""
        g = self._g[goal_id]
        if g["terminal"] is not None:
            return g
        if not self.nav.isTaskComplete():    # 内部 spin,刷新 feedback / 终态
            return None
        status, reason = self._map_result(self.nav.getResult())
        g["terminal"] = status
        g["reason"] = reason
        g["finish_t"] = g.get("finish_t") or time.time()
        if self._active == goal_id:
            self._active = None
        return g

    def _pose(self, x, y, yaw_w=1.0) -> PoseStamped:
        p = PoseStamped()
        p.header.frame_id = "map"
        p.pose.position.x = float(x)
        p.pose.position.y = float(y)
        p.pose.orientation.w = float(yaw_w)
        return p

    def _tf_xy(self, tries=20):
        for _ in range(tries):
            rclpy.spin_once(self.nav, timeout_sec=0.05)
            try:
                t = self.tf.lookup_transform("map", "base_link", rclpy.time.Time())
                return t.transform.translation.x, t.transform.translation.y
            except Exception:
                continue
        return None

    def _cur_node(self) -> Optional[str]:
        """滞回最近邻:仅当另一节点比当前缓存节点近【超过 margin】时才切换,
        消除等距中点处 current_node 抖动(否则污染 current_edge/edges_done)。"""
        xy = self._tf_xy()
        if xy is None:
            return self._cur or self._start_node
        x, y = xy

        def dist(n):
            return ((self.wp[n][0] - x) ** 2 + (self.wp[n][1] - y) ** 2) ** 0.5
        nearest = min(self.wp, key=dist)
        if self._cur is None:
            self._cur = nearest
        elif nearest != self._cur and dist(self._cur) - dist(nearest) > self.cur_switch_margin_m:
            self._cur = nearest
        return self._cur

    def _ek(self, a, b):
        return (a, b) if a <= b else (b, a)

    def _build_adj(self, edges):
        adj: dict[str, list[tuple[str, int]]] = {}
        for a, b, c in edges:
            adj.setdefault(a, []).append((b, c))
            adj.setdefault(b, []).append((a, c))
        return adj

    def _route_access(self, src, dst, *, restricted_ok=frozenset(),
                      allow_all_restricted=False, allow_forbidden_target=False,
                      avoid=frozenset()):
        """与 world.route 同规则的 Dijkstra(用于把度量进度翻译回拓扑边;导航本身由 Nav2
        costmap 规划)。transit 节点必 free;restricted 仅白名单/allow_all;forbidden 仅终点
        + allow_forbidden_target。这样进度路线与 Nav2 实际(靠 keepout 绕开 restricted)一致。"""
        if src is None or dst is None or src not in self.nodes or dst not in self.nodes:
            return None

        def ok(n, is_dst):
            acc = self.nodes.get(n, {}).get("access", "free")
            if acc == "free":
                return True
            if acc == "restricted":
                return allow_all_restricted or n in restricted_ok
            return is_dst and allow_forbidden_target
        if not ok(dst, True):
            return None
        if src == dst:
            return [src]
        dist = {src: 0}; prev = {}; pq = [(0, src)]
        while pq:
            d, u = heapq.heappop(pq)
            if u == dst:
                break
            if d > dist.get(u, 1 << 30):
                continue
            for v, c in self._adj.get(u, []):
                if self._ek(u, v) in avoid:
                    continue
                if not ok(v, v == dst):
                    continue
                nd = d + c
                if nd < dist.get(v, 1 << 30):
                    dist[v] = nd; prev[v] = u; heapq.heappush(pq, (nd, v))
        if dst not in dist:
            return None
        path = [dst]
        while path[-1] != src:
            path.append(prev[path[-1]])
        return list(reversed(path))

    def _map_result(self, tr):
        if tr == TaskResult.SUCCEEDED:
            return "succeeded", None
        if tr == TaskResult.CANCELED:
            return "canceled", "canceled_by_client"
        # ABORTED / UNKNOWN:裸 BT 下无自恢复,规划/跟踪失败即上抛 → 归为不可达
        return "aborted", "unreachable"

    def _terminal_fb(self, goal_id, g):
        route = g.get("route") or [g.get("start")]
        edges_total = max(1, len(route) - 1)
        done = edges_total if g["terminal"] == "succeeded" else g.get("max_edge_idx", 0)
        return {"goal_id": goal_id, "status": g["terminal"], "reason": g["reason"],
                "current_node": self._cur or g.get("start"), "current_edge": None,
                "edges_done": done, "edges_total": edges_total, "velocity": 0.0,
                "stall_ticks": g.get("stall", 0), "distance_remaining": None}
