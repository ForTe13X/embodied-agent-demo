#!/usr/bin/env python3
"""Day3-A 冒烟:验证 keepout 滤镜真的接进了运行栈且【选择性】生效。
判据(对比法,决定性):
  · 导航到 f1(禁入区,keepout 掩码涂黑)→ 规划器找不到有效路径 → ABORTED(TaskResult.FAILED)
  · 导航到 c2(不在 keepout)→ SUCCEEDED
f1 在 map.pgm 里本是【自由】的(节点圆盘被刻成 FREE),若 keepout 没生效它会 SUCCEEDED;
所以 "f1 失败 ∧ c2 成功" 唯一解释就是 keepout 滤镜正确覆盖了 f1 而没误伤主走廊。
"""
import re
import sys
import time

import rclpy
from geometry_msgs.msg import PoseStamped
from nav2_simple_commander.robot_navigator import BasicNavigator, TaskResult

WP_PATH = "/hostpb/world/waypoints.yaml"
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
    p.pose.position.x = float(x)
    p.pose.position.y = float(y)
    p.pose.orientation.w = 1.0
    return p


def go(nav, name, x, y, timeout=70):
    p = pose(x, y)
    p.header.stamp = nav.get_clock().now().to_msg()
    if not nav.goToPose(p):
        return "REJECTED"
    t0 = time.time()
    while not nav.isTaskComplete():
        if time.time() - t0 > timeout:
            nav.cancelTask()
            return "TIMEOUT"
        time.sleep(0.2)
    return nav.getResult()


def main():
    wp = load_wp(WP_PATH)
    rclpy.init()
    nav = BasicNavigator()
    init = pose(*wp["dock"])
    init.header.stamp = nav.get_clock().now().to_msg()
    nav.setInitialPose(init)
    time.sleep(3)
    nav.waitUntilNav2Active(localizer="robot_localization")
    print("Nav2 active(含 keepout 滤镜)")

    print("[A] 导航到 f1(keepout 禁区)—— 期望 ABORTED")
    r_f1 = go(nav, "f1", *wp["f1"])
    print(f"    f1 result = {r_f1}")

    print("[B] 导航到 c2(非 keepout)—— 期望 SUCCEEDED")
    r_c2 = go(nav, "c2", *wp["c2"])
    print(f"    c2 result = {r_c2}")

    nav.lifecycleShutdown()

    ok = (r_f1 == TaskResult.FAILED and r_c2 == TaskResult.SUCCEEDED)
    if ok:
        print("PASS(Day3-A): keepout 滤镜选择性生效 —— f1(禁区)ABORTED、c2(自由)SUCCEEDED。"
              "f1 在底图本是自由格,只有 keepout 生效才会被拒。")
        return 0
    reasons = []
    if r_f1 != TaskResult.FAILED:
        reasons.append(f"f1 未被 keepout 拒(result={r_f1};keepout 可能没接上)")
    if r_c2 != TaskResult.SUCCEEDED:
        reasons.append(f"c2 未成功(result={r_c2};keepout 可能误伤主走廊)")
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
