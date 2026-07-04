#!/bin/bash
# 查:导航时控制器有没有发 cmd_vel(判断是"控制器不动"还是"loopback 不应用速度")。
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=42
ros2 launch nav2_bringup tb3_loopback_simulation.launch.py headless:=True use_rviz:=False >/tmp/l.log 2>&1 &
INITPOSE='{header: {frame_id: map}, pose: {pose: {position: {x: 0.0, y: 0.0, z: 0.0}, orientation: {w: 1.0}}}}'
( while true; do ros2 topic pub --once /initialpose geometry_msgs/msg/PoseWithCovarianceStamped "$INITPOSE" >/dev/null 2>&1; sleep 1; done ) &
IP=$!
for i in $(seq 1 60); do
  [[ "$(ros2 lifecycle get /bt_navigator 2>/dev/null)" == active* ]] && break; sleep 2
done
kill $IP 2>/dev/null; sleep 3

echo "===== cmd_vel 话题类型 ====="
ros2 topic info /cmd_vel 2>&1 | head -3
echo "===== 有哪些 *cmd_vel* 话题 ====="
ros2 topic list | grep -i cmd_vel

echo "===== 后台抓 cmd_vel 10s,同时发目标 ====="
( timeout 12 ros2 topic echo /cmd_vel 2>/dev/null > /tmp/cmdvel.log ) &
sleep 1
ros2 action send_goal /navigate_to_pose nav2_msgs/action/NavigateToPose \
  "{pose: {header: {frame_id: map}, pose: {position: {x: 0.5, y: 0.0, z: 0.0}, orientation: {w: 1.0}}}}" >/dev/null 2>&1 &
sleep 12
echo "cmd_vel 采样行数: $(wc -l </tmp/cmdvel.log)"
echo "非零 linear.x 出现次数: $(grep -A2 'linear:' /tmp/cmdvel.log | grep -E 'x: [^0]' | wc -l)"
echo "--- cmd_vel 头 20 行 ---"; head -20 /tmp/cmdvel.log

echo "===== controller_server 参数:goal checker / base 帧 ====="
ros2 param get /controller_server general_goal_checker.xy_goal_tolerance 2>&1 | head -1
ros2 param get /loopback_simulator base_frame_id 2>&1 | head -1
ros2 param get /loopback_simulator update_duration 2>&1 | head -1

echo "===== loopback 相关日志 ====="
grep -iE "loopback|base_footprint|base_link|cmd_vel" /tmp/l.log | tail -10

pkill -f nav2 2>/dev/null
