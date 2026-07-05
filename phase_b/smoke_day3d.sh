#!/bin/bash
# Day3-D 启动器:用【no-replan 裸 BT】(sed 换 BT 路径)+ 局部代价地图 keepout,
# 在真实栈复现 mock 的 blocked/停滞语义。keepout 滤镜先起、再起主 nav2。
set -o pipefail
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=42

# 用 no-replan BT 覆盖默认裸 BT(只改这一处路径),其余参数(含局部/全局 keepout)不变
PARAMS=/tmp/params_noreplan.yaml
sed 's#bare_nav_to_pose\.xml#bare_nav_to_pose_noreplan.xml#' /hostpb/nav2_params.yaml > "$PARAMS"
grep -q "bare_nav_to_pose_noreplan.xml" "$PARAMS" && echo "  已切到 no-replan BT" || echo "  警告:BT 未切换"

echo "=== [1/3] keepout 滤镜服务 ==="
ros2 launch /hostpb/launch/keepout_servers.launch.py params_file:=$PARAMS >/tmp/kf.log 2>&1 &
for i in $(seq 1 40); do
  FM=$(ros2 lifecycle get /filter_mask_server 2>/dev/null)
  CI=$(ros2 lifecycle get /costmap_filter_info_server 2>/dev/null)
  [[ "$FM" == active* && "$CI" == active* ]] && { echo "  滤镜 active @ ${i}"; break; }
  sleep 1
done

echo "=== [2/3] 主 nav2(no-replan BT + 双图 keepout)==="
ros2 launch nav2_bringup tb3_loopback_simulation.launch.py \
  headless:=True use_rviz:=False \
  map:=/hostpb/world/map.yaml params_file:=$PARAMS >/tmp/l.log 2>&1 &
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

echo "=== [3/3] 跑 smoke_day3d.py ==="
cd /hostpb
timeout 200 python3 /hostpb/smoke_day3d.py
RC=$?
echo "=== day3d rc=$RC ==="
pkill -f nav2 2>/dev/null; pkill -f costmap_filter 2>/dev/null; pkill -f filter_mask 2>/dev/null
exit $RC
