#!/bin/bash
# 分辨:控制器本身发零,还是 collision_monitor 把速度清零。查 /cmd_vel_nav(控制器直出)。
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=42
ros2 launch nav2_bringup tb3_loopback_simulation.launch.py headless:=True use_rviz:=False >/tmp/l.log 2>&1 &
INITPOSE='{header: {frame_id: map}, pose: {pose: {position: {x: 0.0, y: 0.0, z: 0.0}, orientation: {w: 1.0}}}}'
( while true; do ros2 topic pub --once /initialpose geometry_msgs/msg/PoseWithCovarianceStamped "$INITPOSE" >/dev/null 2>&1; sleep 1; done ) &
IP=$!
for i in $(seq 1 60); do [[ "$(ros2 lifecycle get /bt_navigator 2>/dev/null)" == active* ]] && break; sleep 2; done
kill $IP 2>/dev/null; sleep 3

for T in /cmd_vel_nav /cmd_vel_smoothed /cmd_vel; do
  ( timeout 12 ros2 topic echo "$T" 2>/dev/null > "/tmp$(echo $T | tr / _).log" ) &
done
sleep 1
ros2 action send_goal /navigate_to_pose nav2_msgs/action/NavigateToPose \
  "{pose: {header: {frame_id: map}, pose: {position: {x: 0.5, y: 0.0, z: 0.0}, orientation: {w: 1.0}}}}" >/tmp/goal.log 2>&1 &
sleep 13
for T in /cmd_vel_nav /cmd_vel_smoothed /cmd_vel; do
  F="/tmp$(echo $T | tr / _).log"
  NZ=$(grep -E 'x: [^0 ]' "$F" 2>/dev/null | grep -v 'e-' | wc -l)
  echo "$T : 非零分量出现 $NZ 次 / 总行 $(wc -l <"$F" 2>/dev/null)"
done
echo "--- /cmd_vel_nav 头12行 ---"; head -12 /tmp_cmd_vel_nav.log
echo "--- goal 结果 ---"; grep -E "status|error_code" /tmp/goal.log | tail -3
echo "--- collision_monitor 日志 ---"; grep -iE "collision|slow|stop|zeroing" /tmp/l.log | tail -6
pkill -f nav2 2>/dev/null
