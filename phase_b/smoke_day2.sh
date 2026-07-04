#!/bin/bash
# Phase B Day2 冒烟启动器:用【我们自建的开阔走廊地图】+ 默认 nav2 参数(隔离实验:
# 只换地图,不动参数,验证 Day1 的不平移是否由 tb3_world 杂物导致)。
# 初始位姿发在 dock(1.2, 6.0),不是原点(原点在我们地图里是墙)。
set -o pipefail
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=42

MAP=/hostpb/world/map.yaml
PARAMS=${PARAMS:-}        # 空=用默认参数;可传 /hostpb/nav2_params.yaml 覆盖(Day2-B 裸BT)
START_X=${START_X:-1.2}
START_Y=${START_Y:-6.0}

LAUNCH_ARGS="headless:=True use_rviz:=False map:=$MAP"
[[ -n "$PARAMS" ]] && LAUNCH_ARGS="$LAUNCH_ARGS params_file:=$PARAMS"
echo "=== launch: $LAUNCH_ARGS ==="
ros2 launch nav2_bringup tb3_loopback_simulation.launch.py $LAUNCH_ARGS >/tmp/l.log 2>&1 &

# loopback 要先收到 /initialpose 才发布 map->odom TF;autostart 在此之前就激活代价地图,
# 等不到 map 帧会中止 → 一起来就后台循环发初始位姿(发在 dock)。
INITPOSE="{header: {frame_id: map}, pose: {pose: {position: {x: $START_X, y: $START_Y, z: 0.0}, orientation: {w: 1.0}}}}"
( while true; do ros2 topic pub --once /initialpose geometry_msgs/msg/PoseWithCovarianceStamped "$INITPOSE" >/dev/null 2>&1; sleep 1; done ) &
IP=$!

echo "=== 等 bt_navigator + planner_server + controller_server active ==="
for i in $(seq 1 90); do
  BT=$(ros2 lifecycle get /bt_navigator 2>/dev/null)
  PL=$(ros2 lifecycle get /planner_server 2>/dev/null)
  CT=$(ros2 lifecycle get /controller_server 2>/dev/null)
  [[ "$BT" == active* && "$PL" == active* && "$CT" == active* ]] && { echo "  all active @ ${i}"; break; }
  sleep 2
done

kill $IP 2>/dev/null; sleep 3   # 停掉 initialpose 循环,否则每秒把机器人瞬移回 dock

echo "=== 跑 smoke_day2.py ==="
timeout 220 python3 /hostpb/smoke_day2.py
RC=$?
echo "=== smoke rc=$RC ==="
pkill -f nav2 2>/dev/null
pkill -f loopback 2>/dev/null
exit $RC
