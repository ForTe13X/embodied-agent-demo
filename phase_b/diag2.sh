#!/bin/bash
# 权威确认:导航到可达目标 (0.5,0) 后,机器人在 map 帧到底动没动。
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

echo "===== 导航前:map->base_link ====="
timeout 5 ros2 run tf2_ros tf2_echo map base_link 2>&1 | grep -A1 "Translation" | head -2

echo "===== 发 NavigateToPose (0.5, 0.0) ====="
timeout 60 ros2 action send_goal /navigate_to_pose nav2_msgs/action/NavigateToPose \
  "{pose: {header: {frame_id: map}, pose: {position: {x: 0.5, y: 0.0, z: 0.0}, orientation: {w: 1.0}}}}" 2>&1 \
  | grep -E "status|error_code|result" | tail -6

echo "===== 导航后:map->base_link(动没动?) ====="
timeout 5 ros2 run tf2_ros tf2_echo map base_link 2>&1 | grep -A1 "Translation" | tail -2
echo "===== 导航后:/odom 位置 ====="
timeout 5 ros2 topic echo /odom --once 2>/dev/null | grep -A3 "position:" | head -4

pkill -f nav2 2>/dev/null
