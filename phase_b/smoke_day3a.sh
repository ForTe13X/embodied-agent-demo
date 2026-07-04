#!/bin/bash
# Day3-A 启动器:先起 keepout 滤镜服务栈并等它 active,再起主 nav2(其 global_costmap 含
# keepout_filter),然后跑 smoke_day3a.py 验证 keepout 选择性生效。
set -o pipefail
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=42

echo "=== [1/3] 起 keepout 滤镜服务(filter_mask_server + costmap_filter_info_server)==="
ros2 launch /hostpb/launch/keepout_servers.launch.py params_file:=/hostpb/nav2_params.yaml >/tmp/kf.log 2>&1 &
for i in $(seq 1 40); do
  FM=$(ros2 lifecycle get /filter_mask_server 2>/dev/null)
  CI=$(ros2 lifecycle get /costmap_filter_info_server 2>/dev/null)
  [[ "$FM" == active* && "$CI" == active* ]] && { echo "  滤镜服务 active @ ${i}"; break; }
  sleep 1
done

echo "=== [2/3] 起主 nav2(我们的地图 + 裸BT + keepout_filter)==="
ros2 launch nav2_bringup tb3_loopback_simulation.launch.py \
  headless:=True use_rviz:=False \
  map:=/hostpb/world/map.yaml params_file:=/hostpb/nav2_params.yaml >/tmp/l.log 2>&1 &
INITPOSE="{header: {frame_id: map}, pose: {pose: {position: {x: 1.2, y: 6.0, z: 0.0}, orientation: {w: 1.0}}}}"
( while true; do ros2 topic pub --once /initialpose geometry_msgs/msg/PoseWithCovarianceStamped "$INITPOSE" >/dev/null 2>&1; sleep 1; done ) &
IP=$!
for i in $(seq 1 90); do
  BT=$(ros2 lifecycle get /bt_navigator 2>/dev/null)
  PL=$(ros2 lifecycle get /planner_server 2>/dev/null)
  [[ "$BT" == active* && "$PL" == active* ]] && { echo "  nav2 active @ ${i}"; break; }
  sleep 2
done
kill $IP 2>/dev/null; sleep 3

echo "=== [3/3] 跑 smoke_day3a.py ==="
timeout 200 python3 /hostpb/smoke_day3a.py
RC=$?
echo "=== day3a rc=$RC ==="
# 诊断:确认 keepout 相关话题在
echo "--- keepout 话题 ---"; ros2 topic list 2>/dev/null | grep -iE "keepout|filter_info" || true
pkill -f nav2 2>/dev/null; pkill -f costmap_filter 2>/dev/null; pkill -f filter_mask 2>/dev/null
exit $RC
