#!/usr/bin/env python3
"""Phase B Day2 严格冒烟:在我们自建的开阔走廊地图上真正把机器人从 START 开到 GOAL。
与 Day1 的区别 —— 判据升级为「必须真平移」:
  PASS 需同时满足:① result==SUCCEEDED ② 末位姿距起点 > MOTION_MIN(真的走了)
                   ③ 末位姿距目标 < ARRIVE_TOL(真的到了)
任一不满足即 FAIL。这验证 Day1 遗留的「loopback 里几乎不平移」是否被开阔地图修好。
坐标从 waypoints.yaml 读(单一真值,gen_world.py 生成)。
环境变量:START(默认 dock) GOAL(默认 c2) WAYPOINTS(默认 /hostpb/world/waypoints.yaml)
"""
import os
import re
import sys
import time

import rclpy
import rclpy.time
from geometry_msgs.msg import PoseStamped
from nav2_simple_commander.robot_navigator import BasicNavigator, TaskResult
from tf2_ros import Buffer, TransformListener

MOTION_MIN = 3.0        # 末位姿距起点至少 3m,才算"真的平移过"(Day1 是 ~0m)
ARRIVE_TOL = 0.6        # 距目标 < 0.6m 算到达(goal checker 容差 0.25m + 余量)
FEEDBACK_TIMEOUT_S = 180


def load_waypoints(path):
    """极简解析 '  id: {x: N, y: N, yaw: N, access: s}'——不依赖 pyyaml。"""
    wp = {}
    pat = re.compile(r"^\s*(\w+):\s*\{x:\s*([-\d.]+),\s*y:\s*([-\d.]+)")
    for line in open(path, encoding="utf-8"):
        m = pat.match(line)
        if m:
            wp[m.group(1)] = (float(m.group(2)), float(m.group(3)))
    return wp


def pose(x, y, yaw_w=1.0):
    p = PoseStamped()
    p.header.frame_id = "map"
    p.pose.position.x = float(x)
    p.pose.position.y = float(y)
    p.pose.orientation.w = float(yaw_w)
    return p


def tf_xy(buf, nav, tries=30):
    for _ in range(tries):
        rclpy.spin_once(nav, timeout_sec=0.1)
        try:
            t = buf.lookup_transform("map", "base_link", rclpy.time.Time())
            return t.transform.translation.x, t.transform.translation.y
        except Exception:
            continue
    return None


def main():
    wp_path = os.environ.get("WAYPOINTS", "/hostpb/world/waypoints.yaml")
    start_id = os.environ.get("START", "dock")
    goal_id = os.environ.get("GOAL", "c2")
    wp = load_waypoints(wp_path)
    sx, sy = wp[start_id]
    gx, gy = wp[goal_id]
    print(f"[cfg] {start_id}({sx},{sy}) -> {goal_id}({gx},{gy})  "
          f"直线距离 {((gx-sx)**2+(gy-sy)**2)**0.5:.1f}m")

    rclpy.init()
    nav = BasicNavigator()
    buf = Buffer()
    TransformListener(buf, nav)

    print(f"[1] setInitialPose({start_id})")
    init = pose(sx, sy)
    init.header.stamp = nav.get_clock().now().to_msg()
    nav.setInitialPose(init)
    time.sleep(3)

    print("[2] waitUntilNav2Active(loopback 无 amcl)...")
    nav.waitUntilNav2Active(localizer="robot_localization")
    start_xy = tf_xy(buf, nav)
    print(f"    Nav2 active. 起点实测 TF={start_xy}")

    print(f"[3] goToPose({goal_id})")
    goal = pose(gx, gy)
    goal.header.stamp = nav.get_clock().now().to_msg()
    if not nav.goToPose(goal):
        print("FAIL: goToPose 未被接受"); nav.lifecycleShutdown(); return 3

    t0 = time.time()
    i = 0
    max_disp = 0.0
    max_recov = 0
    while not nav.isTaskComplete():
        fb = nav.getFeedback()
        if fb is not None:
            p = fb.current_pose.pose.position
            disp = ((p.x - sx) ** 2 + (p.y - sy) ** 2) ** 0.5
            max_disp = max(max_disp, disp)
            max_recov = max(max_recov, fb.number_of_recoveries)
            if i % 15 == 0:
                print(f"    fb: pose=({p.x:.2f},{p.y:.2f}) 走了{disp:.2f}m "
                      f"剩{fb.distance_remaining:.2f}m recov={fb.number_of_recoveries} "
                      f"t={fb.navigation_time.sec}s")
            i += 1
        if time.time() - t0 > FEEDBACK_TIMEOUT_S:
            nav.cancelTask()
            print(f"FAIL: {FEEDBACK_TIMEOUT_S}s 超时,已取消"); nav.lifecycleShutdown(); return 3
        time.sleep(0.1)

    result = nav.getResult()
    end_xy = tf_xy(buf, nav)
    nav.lifecycleShutdown()

    if end_xy is None:
        print("FAIL: 取不到末位姿 TF"); return 3
    ex, ey = end_xy
    moved = ((ex - sx) ** 2 + (ey - sy) ** 2) ** 0.5
    to_goal = ((ex - gx) ** 2 + (ey - gy) ** 2) ** 0.5
    print(f"[4] result={result}  末位姿TF=({ex:.2f},{ey:.2f})  "
          f"走了{moved:.2f}m(feedback峰值{max_disp:.2f}m)  距目标{to_goal:.2f}m  "
          f"recoveries峰值={max_recov}")

    # recoveries==0 固化"无自恢复"为回归断言:裸 BT 里没有恢复节点,一旦 >0 说明用错了 BT。
    ok = (result == TaskResult.SUCCEEDED and moved > MOTION_MIN
          and to_goal < ARRIVE_TOL and max_recov == 0)
    if ok:
        print(f"PASS(严格): SUCCEEDED 且真平移{moved:.1f}m 且到达目标(距{to_goal:.2f}m<{ARRIVE_TOL}m)"
              f" 且 recoveries=0(无自恢复) —— Day1 遗留的不平移问题已被开阔地图修复。")
        return 0
    reasons = []
    if result != TaskResult.SUCCEEDED:
        reasons.append(f"result={result}")
    if moved <= MOTION_MIN:
        reasons.append(f"位移{moved:.2f}m≤{MOTION_MIN}m(仍几乎不动)")
    if to_goal >= ARRIVE_TOL:
        reasons.append(f"距目标{to_goal:.2f}m≥{ARRIVE_TOL}m(没到)")
    if max_recov != 0:
        reasons.append(f"recoveries={max_recov}≠0(发生了自恢复,BT 配置可能不是裸 BT)")
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
