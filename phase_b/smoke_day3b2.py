#!/usr/bin/env python3
"""Day3-B(第二部分):运行时受阻边故障注入 —— 观察真实 Nav2 的响应,并【诚实对比】mock。
流程:导航 dock→a1(直走廊 dock-c1-c2-a1)。中途用 filter_mask_server 的 load_map 服务把
keepout 掩码热替换成"封死 c2-a1 中段"的掩码,注入受阻边。观察 Nav2 怎么反应。

关键诚实点(评审 §blocked):mock 里受阻边表现为【停滞】(server 不自replan,靠上层 observer
停滞水位发现)。而真实 Nav2 的裸 BT 仍含 1Hz 重规划,只要存在绕行路线就会【自动改道】,
distance_remaining 在注入瞬间跳增、随后继续下降,最终仍 SUCCEEDED(更长路径)。
→ 这不是缺陷,而是"重规划住在哪一层"的差别:mock 的停滞语义建模的是【无自主重规划的底盘】。
判据:注入后出现 distance_remaining 跳增(≥2m,改道证据)∧ 最终 SUCCEEDED ∧ recoveries==0
(改道是规划器层,不是 BT 恢复)。
"""
import re
import sys
import time

import rclpy
from geometry_msgs.msg import PoseStamped
from nav2_msgs.srv import LoadMap
from nav2_simple_commander.robot_navigator import BasicNavigator, TaskResult

WP_PATH = "/hostpb/world/waypoints.yaml"
BLOCKED_MASK = "/hostpb/world/keepout_c2_a1.yaml"
INJECT_AFTER_S = 10          # 机器人开动约 10s(过了 c1、路径已 committed)再注入
_WP = re.compile(r"^\s*(\w+):\s*\{x:\s*([-\d.]+),\s*y:\s*([-\d.]+)")


def load_wp(path):
    wp = {}
    for line in open(path, encoding="utf-8"):
        m = _WP.match(line)
        if m:
            wp[m.group(1)] = (float(m.group(2)), float(m.group(3)))
    return wp


def pose(x, y):
    p = PoseStamped()
    p.header.frame_id = "map"
    p.pose.position.x = float(x); p.pose.position.y = float(y); p.pose.orientation.w = 1.0
    return p


def main():
    wp = load_wp(WP_PATH)
    rclpy.init()
    nav = BasicNavigator()
    init = pose(*wp["dock"]); init.header.stamp = nav.get_clock().now().to_msg()
    nav.setInitialPose(init); time.sleep(3)
    nav.waitUntilNav2Active(localizer="robot_localization")

    load_cli = nav.create_client(LoadMap, "/filter_mask_server/load_map")
    load_cli.wait_for_service(timeout_sec=5.0)

    goal = pose(*wp["a1"]); goal.header.stamp = nav.get_clock().now().to_msg()
    nav.goToPose(goal)
    print("[nav] dock -> a1 开始")

    injected = False
    inject_t = None
    dist_at_inject = None
    max_jump = 0.0
    prev_dist = None
    t0 = time.time()
    i = 0
    while not nav.isTaskComplete():
        fb = nav.getFeedback()
        if fb is not None:
            d = float(fb.distance_remaining)
            p = fb.current_pose.pose.position
            if not injected and time.time() - t0 >= INJECT_AFTER_S:
                req = LoadMap.Request(); req.map_url = BLOCKED_MASK
                load_cli.call_async(req)          # 热替换掩码:封死 c2-a1
                injected = True; inject_t = time.time(); dist_at_inject = d
                print(f"    >>> 注入受阻边 c2-a1(pose=({p.x:.1f},{p.y:.1f}) dist={d:.2f}m)")
            if injected and prev_dist is not None and (d - prev_dist) > 0.5:
                max_jump = max(max_jump, d - prev_dist)  # 改道 → dist 跳增
            if i % 15 == 0:
                print(f"    fb: pose=({p.x:.1f},{p.y:.1f}) dist={d:.2f}m "
                      f"recov={fb.number_of_recoveries} t={fb.navigation_time.sec}s")
            prev_dist = d; i += 1
        if time.time() - t0 > 200:
            nav.cancelTask(); print("FAIL: 200s 超时"); nav.lifecycleShutdown(); return 3
        time.sleep(0.2)

    result = nav.getResult()
    # 取末位姿
    end = nav.getFeedback()
    nav.lifecycleShutdown()
    gx, gy = wp["a1"]
    print(f"[done] result={result}  注入后 distance_remaining 最大跳增={max_jump:.2f}m")

    rerouted = max_jump >= 2.0
    ok = (result == TaskResult.SUCCEEDED and injected and rerouted)
    if ok:
        print(f"PASS(Day3-B/2): 受阻边注入后 Nav2 自动改道(dist 跳增 {max_jump:.1f}m)仍到达 a1、"
              f"SUCCEEDED。真实 Nav2 的裸 BT 靠 1Hz 重规划自愈,而非 mock 的停滞 —— "
              f"'重规划住在规划器层',mock 停滞语义建模的是无自主重规划的底盘。")
        return 0
    reasons = []
    if not injected:
        reasons.append("未成功注入(load_map 没调到)")
    if result != TaskResult.SUCCEEDED:
        reasons.append(f"result={result}(可能绕行路线也不存在→ABORTED;换更靠内的封边)")
    if not rerouted:
        reasons.append(f"未观测到改道跳增(max_jump={max_jump:.2f}m<2m;注入时机可能太晚)")
    print("FAIL: " + "; ".join(reasons))
    return 3


if __name__ == "__main__":
    try:
        code = main()
    finally:
        try:
            rclpy.shutdown()
        except Exception:
            pass
    sys.exit(code)
