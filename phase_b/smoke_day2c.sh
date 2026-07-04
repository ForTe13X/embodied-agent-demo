#!/bin/bash
# Day2-C 启动器:起 Nav2(我们的地图 + 裸 BT params)后,跑 RclpyAdapter 契约冒烟。
# adapter 自己做 bootstrap(setInitialPose + waitUntilNav2Active),这里只负责:
#   起栈 → 后台喂 /initialpose 直到 bt_navigator active(让 loopback 建 map->odom TF)→ 停喂 → 跑冒烟。
set -o pipefail
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=42

ros2 launch nav2_bringup tb3_loopback_simulation.launch.py \
  headless:=True use_rviz:=False \
  map:=/hostpb/world/map.yaml params_file:=/hostpb/nav2_params.yaml >/tmp/l.log 2>&1 &

INITPOSE="{header: {frame_id: map}, pose: {pose: {position: {x: 1.2, y: 6.0, z: 0.0}, orientation: {w: 1.0}}}}"
( while true; do ros2 topic pub --once /initialpose geometry_msgs/msg/PoseWithCovarianceStamped "$INITPOSE" >/dev/null 2>&1; sleep 1; done ) &
IP=$!

for i in $(seq 1 90); do
  BT=$(ros2 lifecycle get /bt_navigator 2>/dev/null)
  CT=$(ros2 lifecycle get /controller_server 2>/dev/null)
  [[ "$BT" == active* && "$CT" == active* ]] && { echo "nav2 active @ ${i}"; break; }
  sleep 2
done
kill $IP 2>/dev/null; sleep 3

cd /hostpb
timeout 260 python3 smoke_day2c.py
RC=$?
echo "=== day2c rc=$RC ==="
pkill -f nav2 2>/dev/null; pkill -f loopback 2>/dev/null
exit $RC
