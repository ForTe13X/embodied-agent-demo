#!/bin/bash
# 通用 BT 冒烟:BT 环境变量指定要用的树(默认 retry 变体),sed 换进 params 后跑 smoke_day2.py
# (dock→c2,严格判据含 recoveries==0)。既验证该 BT 能加载+导航,也回归"局部 keepout 不误伤正常导航"。
set -o pipefail
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=${ROS_DOMAIN_ID:-43}   # 独立域,避开可能 wedge 的 daemon
BT=${BT:-bare_nav_to_pose_retry.xml}
PARAMS=/tmp/params_bt.yaml
sed "s#bare_nav_to_pose\.xml#$BT#" /hostpb/nav2_params.yaml > "$PARAMS"
echo "BT = $BT"
lg() { timeout 5 ros2 lifecycle get "$1" 2>/dev/null; }   # 守卫:lifecycle get 不许无限挂

ros2 launch /hostpb/launch/keepout_servers.launch.py params_file:=$PARAMS >/tmp/kf.log 2>&1 &
for i in $(seq 1 40); do
  [[ "$(lg /filter_mask_server)" == active* && "$(lg /costmap_filter_info_server)" == active* ]] \
    && { echo "  滤镜 active @ ${i}"; break; }
  sleep 1
done
ros2 launch nav2_bringup tb3_loopback_simulation.launch.py headless:=True use_rviz:=False \
  map:=/hostpb/world/map.yaml params_file:=$PARAMS >/tmp/l.log 2>&1 &
INITPOSE="{header: {frame_id: map}, pose: {pose: {position: {x: 1.2, y: 6.0, z: 0.0}, orientation: {w: 1.0}}}}"
( while true; do ros2 topic pub --once /initialpose geometry_msgs/msg/PoseWithCovarianceStamped "$INITPOSE" >/dev/null 2>&1; sleep 1; done ) &
IP=$!
ACTIVE=0
for i in $(seq 1 60); do
  [[ "$(lg /bt_navigator)" == active* && "$(lg /planner_server)" == active* ]] \
    && { echo "  nav2 active @ ${i}(BT 已加载)"; ACTIVE=1; break; }
  sleep 2
done
kill $IP 2>/dev/null; sleep 3
if [[ "$ACTIVE" != 1 ]]; then
  echo "  !! nav2 未在时限内 active —— bt_navigator 日志(可能 BT 加载失败):"
  grep -iE "bt_navigator|behavior_tree|error|fail|exception" /tmp/l.log | tail -15
  pkill -f nav2 2>/dev/null; pkill -f filter_mask 2>/dev/null; pkill -f costmap_filter 2>/dev/null
  exit 3
fi
timeout 120 python3 /hostpb/smoke_day2.py
RC=$?
echo "=== bt_check($BT) rc=$RC ==="
pkill -f nav2 2>/dev/null; pkill -f filter_mask 2>/dev/null; pkill -f costmap_filter 2>/dev/null
exit $RC
