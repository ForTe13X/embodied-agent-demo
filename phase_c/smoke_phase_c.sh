#!/bin/bash
# Phase C 启动器:keepout 滤镜 + 主 nav2 起来后,跑真实评测矩阵(run_real_eval.py)。
# orch 镜像(含 langgraph/pydantic);挂载 repo(/repo)+ phase_b(/hostpb)。
set -o pipefail
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=${ROS_DOMAIN_ID:-45}
export PYTHONPATH=/repo:/hostpb:$PYTHONPATH
lg() { timeout 5 ros2 lifecycle get "$1" 2>/dev/null; }

echo "=== [1/3] keepout 滤镜服务 ==="
ros2 launch /hostpb/launch/keepout_servers.launch.py params_file:=/hostpb/nav2_params.yaml >/tmp/kf.log 2>&1 &
for i in $(seq 1 40); do
  [[ "$(lg /filter_mask_server)" == active* && "$(lg /costmap_filter_info_server)" == active* ]] \
    && { echo "  滤镜 active @ ${i}"; break; }
  sleep 1
done

echo "=== [2/3] 主 nav2 ==="
ros2 launch nav2_bringup tb3_loopback_simulation.launch.py headless:=True use_rviz:=False \
  map:=/hostpb/world/map.yaml params_file:=/hostpb/nav2_params.yaml >/tmp/l.log 2>&1 &
INITPOSE="{header: {frame_id: map}, pose: {pose: {position: {x: 1.2, y: 6.0, z: 0.0}, orientation: {w: 1.0}}}}"
( while true; do ros2 topic pub --once /initialpose geometry_msgs/msg/PoseWithCovarianceStamped "$INITPOSE" >/dev/null 2>&1; sleep 1; done ) &
IP=$!
for i in $(seq 1 90); do
  [[ "$(lg /bt_navigator)" == active* && "$(lg /planner_server)" == active* ]] \
    && { echo "  nav2 active @ ${i}"; break; }
  sleep 2
done
kill $IP 2>/dev/null; sleep 3

echo "=== [3/3] 真实评测矩阵 ==="
timeout 2400 python3 /repo/phase_c/run_real_eval.py
RC=$?
echo "=== phase_c eval rc=$RC ==="
pkill -f nav2 2>/dev/null; pkill -f costmap_filter 2>/dev/null; pkill -f filter_mask 2>/dev/null
exit $RC
