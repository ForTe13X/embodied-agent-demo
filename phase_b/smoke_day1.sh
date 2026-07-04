#!/bin/bash
# Phase B Day1 止损冒烟:headless 起 Nav2 + loopback sim,再用 BasicNavigator 真跑一次导航。
# 关键时序修复:loopback_simulator 要收到 /initialpose 才发布 map->odom->base_link TF;
# autostart 会在此之前就激活代价地图,等不到 map 帧就超时中止整个 bringup。
# 因此 launch 一起来就在后台循环发 /initialpose,让 TF 在代价地图激活窗口内建立。
# 判定见 smoke_day1.py(严格:必须真移动 + 终态 SUCCEEDED)。
set -o pipefail
source /opt/ros/jazzy/setup.bash  # 含未绑定变量,勿开 set -u
export ROS_DOMAIN_ID=42
HERE="$(cd "$(dirname "$0")" && pwd)"
LOG=/tmp/_day1_launch.log
: > "$LOG"

echo "== [1/5] 包可用性 =="
ros2 pkg prefix nav2_bringup >/dev/null && ros2 pkg prefix nav2_loopback_sim >/dev/null || {
  echo "FAIL: nav2_bringup / nav2_loopback_sim 未安装"; exit 2; }
echo "OK"

echo "== [2/5] headless 起 tb3 loopback 仿真(后台) =="
ros2 launch nav2_bringup tb3_loopback_simulation.launch.py \
     headless:=True use_rviz:=False >>"$LOG" 2>&1 &
LAUNCH_PID=$!

echo "== [3/5] 后台循环发 /initialpose,让 loopback 建立 map TF(bringup 期间,active 后立即停) =="
INITPOSE='{header: {frame_id: map}, pose: {pose: {position: {x: 0.0, y: 0.0, z: 0.0}, orientation: {w: 1.0}}}}'
( while true; do
    ros2 topic pub --once /initialpose geometry_msgs/msg/PoseWithCovarianceStamped "$INITPOSE" >/dev/null 2>&1
    sleep 1
  done ) &
INITPOSE_PID=$!

echo "== [4/5] 等 Nav2 lifecycle active(bt_navigator+planner,最多 120s) =="
ACTIVE=0
for i in $(seq 1 60); do
  BT=$(ros2 lifecycle get /bt_navigator 2>/dev/null)
  PL=$(ros2 lifecycle get /planner_server 2>/dev/null)
  if [[ "$BT" == active* && "$PL" == active* ]]; then ACTIVE=1; break; fi
  sleep 2
done
# 关键:导航前必须停掉 initialpose 循环,否则它每秒把机器人瞬移回原点,导航永远走不了
kill $INITPOSE_PID 2>/dev/null
if [ "$ACTIVE" != "1" ]; then
  echo "FAIL: 120s 内 Nav2 未 active(bt=$BT planner=$PL)—— 止损点触发"
  echo "---- launch 日志尾部 ----"; tail -30 "$LOG" | grep -iE 'error|fail|abort|timeout' | tail -15
  kill $LAUNCH_PID 2>/dev/null; exit 3
fi
echo "OK: bt_navigator+planner_server active;已停 initialpose 循环"
sleep 2

echo "== [5/5] BasicNavigator 真跑一次导航(硬超时 180s) =="
timeout 180 python3 "$HERE/smoke_day1.py"
VERDICT=$?
[ "$VERDICT" == "124" ] && echo "FAIL: python 冒烟 180s 超时 —— 止损"

kill $LAUNCH_PID $INITPOSE_PID 2>/dev/null
pkill -f nav2 2>/dev/null
sleep 1
exit $VERDICT
