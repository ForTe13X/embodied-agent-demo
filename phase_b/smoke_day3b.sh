#!/bin/bash
# Day3-B 启动器:与 3-A 同样先起 keepout 滤镜服务 → 主 nav2,然后跑 RclpyAdapter 契约测试
# (unreachable 分支)。TEST 可用环境变量覆盖脚本名(默认 smoke_day3b.py)。
set -o pipefail
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=42
TEST=${TEST:-smoke_day3b.py}

echo "=== [1/3] keepout 滤镜服务 ==="
ros2 launch /hostpb/launch/keepout_servers.launch.py params_file:=/hostpb/nav2_params.yaml >/tmp/kf.log 2>&1 &
for i in $(seq 1 40); do
  FM=$(ros2 lifecycle get /filter_mask_server 2>/dev/null)
  CI=$(ros2 lifecycle get /costmap_filter_info_server 2>/dev/null)
  [[ "$FM" == active* && "$CI" == active* ]] && { echo "  滤镜 active @ ${i}"; break; }
  sleep 1
done

echo "=== [2/3] 主 nav2(地图 + 裸BT + keepout_filter)==="
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

echo "=== [3/3] 跑 $TEST ==="
cd /hostpb
timeout 220 python3 "$TEST"
RC=$?
echo "=== day3b rc=$RC ==="
pkill -f nav2 2>/dev/null; pkill -f costmap_filter 2>/dev/null; pkill -f filter_mask 2>/dev/null
exit $RC
