#!/bin/bash
# Day-4 编排整合:keepout 滤镜 + 主 nav2 起来后,用【同一套 LangGraph 编排图】驱动真实 Nav2
# 跑含故障恢复的任务(a3 被 keepout 隔离 → 编排替换 a3_alt)。
# 需在 orch 镜像(含 langgraph/pydantic)里跑,且挂载 repo(/repo=embodied_agent)+ phase_b(/hostpb)。
set -o pipefail
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=42
# 关键:追加而非覆盖 —— source ros 已把 rclpy 的 site-packages 放进 PYTHONPATH,不能丢
export PYTHONPATH=/repo:/hostpb:$PYTHONPATH

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

echo "=== [3/3] LangGraph 编排 × 真实 Nav2 ==="
cd /hostpb
timeout 420 python3 /hostpb/run_real_mission.py
RC=$?
echo "=== day4 rc=$RC ==="
pkill -f nav2 2>/dev/null; pkill -f costmap_filter 2>/dev/null; pkill -f filter_mask 2>/dev/null
exit $RC
