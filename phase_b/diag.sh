#!/bin/bash
# 诊断:loopback + Nav2 起来后,查 定位TF / 地图范围 / 规划器能否算路径。
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=42
ros2 launch nav2_bringup tb3_loopback_simulation.launch.py headless:=True use_rviz:=False >/tmp/l.log 2>&1 &
INITPOSE='{header: {frame_id: map}, pose: {pose: {position: {x: 0.0, y: 0.0, z: 0.0}, orientation: {w: 1.0}}}}'
( while true; do ros2 topic pub --once /initialpose geometry_msgs/msg/PoseWithCovarianceStamped "$INITPOSE" >/dev/null 2>&1; sleep 1; done ) &
IP=$!
for i in $(seq 1 60); do
  [[ "$(ros2 lifecycle get /bt_navigator 2>/dev/null)" == active* && "$(ros2 lifecycle get /planner_server 2>/dev/null)" == active* ]] && break
  sleep 2
done
kill $IP 2>/dev/null; sleep 3

echo "===== A. 机器人定位:map -> base_link TF ====="
timeout 6 ros2 run tf2_ros tf2_echo map base_link 2>&1 | head -12

echo "===== B. TF 树里有哪些帧 ====="
timeout 6 ros2 topic echo /tf --once 2>/dev/null | grep -E "frame_id|child_frame_id" | sort -u

echo "===== C. /map 元数据(原点/分辨率/尺寸) ====="
timeout 6 ros2 topic echo /map --once 2>/dev/null | grep -A8 "info:" | head -12

echo "===== D. 全局代价地图元数据 ====="
timeout 6 ros2 topic echo /global_costmap/costmap --once 2>/dev/null | grep -A8 "info:" | head -12

echo "===== E. 单独调规划器 compute_path_to_pose 到 (0.5,0.0) ====="
timeout 25 ros2 action send_goal /compute_path_to_pose nav2_msgs/action/ComputePathToPose \
  "{goal: {header: {frame_id: map}, pose: {position: {x: 0.5, y: 0.0, z: 0.0}, orientation: {w: 1.0}}}, use_start: false}" 2>&1 \
  | grep -E "error_code|error_msg|Result|poses|status|accepted|rejected" | head -15

echo "===== F. 到 (1.5,0.5) ====="
timeout 25 ros2 action send_goal /compute_path_to_pose nav2_msgs/action/ComputePathToPose \
  "{goal: {header: {frame_id: map}, pose: {position: {x: 1.5, y: 0.5, z: 0.0}, orientation: {w: 1.0}}}, use_start: false}" 2>&1 \
  | grep -E "error_code|error_msg|Result|poses|status" | head -10

echo "===== G. launch 日志里的 planner/costmap 报错 ====="
grep -iE "no valid path|planner|failed|unknown|collision|lethal" /tmp/l.log | grep -iv "info" | tail -12

pkill -f nav2 2>/dev/null
