#!/bin/bash
# Phase B Day1 止损冒烟:headless 起 Nav2 + loopback sim,发一个 NavigateToPose 目标看 feedback。
# 判定:能看到 /navigate_to_pose action + 收到 feedback(distance_remaining 递减)+ 终态 = 通过。
# 用法(容器内):bash /work/smoke_day1.sh
set -uo pipefail
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=42
export RCUTILS_LOGGING_BUFFERED_STREAM=0
LOG=/work/_day1.log
: > "$LOG"

echo "== [1/5] 包可用性 =="
ros2 pkg prefix nav2_bringup && ros2 pkg prefix nav2_loopback_sim || {
  echo "FAIL: nav2_bringup / nav2_loopback_sim 未安装"; exit 2; }

echo "== [2/5] headless 起 loopback 仿真(后台) =="
# tb3_loopback_simulation:map_server + loopback_simulator + 完整 Nav2,不起 rviz
ros2 launch nav2_bringup tb3_loopback_simulation.launch.py \
     headless:=True use_rviz:=False >>"$LOG" 2>&1 &
LAUNCH_PID=$!
echo "launch pid=$LAUNCH_PID,等待栈起来..."

echo "== [3/5] 等 /navigate_to_pose action 出现(最多 90s) =="
FOUND=0
for i in $(seq 1 45); do
  if ros2 action list 2>/dev/null | grep -q "/navigate_to_pose"; then FOUND=1; break; fi
  sleep 2
done
if [ "$FOUND" != "1" ]; then
  echo "FAIL: 90s 内没等到 /navigate_to_pose —— 止损点触发"
  echo "---- launch 日志尾部 ----"; tail -40 "$LOG"
  kill $LAUNCH_PID 2>/dev/null; exit 3
fi
echo "OK: /navigate_to_pose 已就绪"
ros2 action list | sed 's/^/    /'

echo "== [4/5] 设初始位姿 + 等待 Nav2 active =="
# loopback 需要 initialpose 才能开始定位;发几次确保收到
for k in 1 2 3; do
ros2 topic pub --once /initialpose geometry_msgs/msg/PoseWithCovarianceStamped \
  "{header: {frame_id: map}, pose: {pose: {position: {x: 0.0, y: 0.0, z: 0.0}, orientation: {w: 1.0}}}}" >>"$LOG" 2>&1
sleep 1
done
sleep 5

echo "== [5/5] 发一个 NavigateToPose 目标,看 feedback =="
timeout 60 ros2 action send_goal /navigate_to_pose nav2_msgs/action/NavigateToPose \
  "{pose: {header: {frame_id: map}, pose: {position: {x: 1.0, y: 0.5, z: 0.0}, orientation: {w: 1.0}}}}" \
  --feedback 2>&1 | tee /work/_goal.log | sed 's/^/    /'

echo ""
echo "== 判定 =="
if grep -qE "distance_remaining|Result was received|status: (STATUS_)?SUCCEEDED|Goal finished" /work/_goal.log; then
  echo "PASS: 收到 feedback/终态,Day1 止损点通过 —— 可继续 Phase B"
  VERDICT=0
else
  echo "FAIL: 未见 feedback/终态 —— 止损"
  echo "---- goal 日志 ----"; cat /work/_goal.log
  VERDICT=3
fi

kill $LAUNCH_PID 2>/dev/null
pkill -f nav2 2>/dev/null
exit $VERDICT
