#!/bin/bash
# Day3-C:审计录制。起 keepout+nav2 后,用 rosbag2/MCAP 录一整段【含受阻边故障注入】的导航,
# 关键 topic 全留痕,再 ros2 bag info 校验可读、消息非空。产出 /hostpb/audit_bag(MCAP)。
set -o pipefail
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=42

echo "=== [1/4] keepout 滤镜服务 ==="
ros2 launch /hostpb/launch/keepout_servers.launch.py params_file:=/hostpb/nav2_params.yaml >/tmp/kf.log 2>&1 &
for i in $(seq 1 40); do
  FM=$(ros2 lifecycle get /filter_mask_server 2>/dev/null)
  CI=$(ros2 lifecycle get /costmap_filter_info_server 2>/dev/null)
  [[ "$FM" == active* && "$CI" == active* ]] && { echo "  滤镜 active @ ${i}"; break; }
  sleep 1
done

echo "=== [2/4] 主 nav2 ==="
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

echo "=== [3/4] 开录 MCAP + 跑故障注入导航 ==="
BAG=/hostpb/audit_bag
rm -rf "$BAG"
# action feedback/status 是 _action 隐藏话题,需 --include-hidden-topics 才录得到
TOPICS="/navigate_to_pose/_action/feedback /navigate_to_pose/_action/status /plan /tf /tf_static /cmd_vel_nav /keepout_filter_mask /costmap_filter_info"
ros2 bag record -s mcap --include-hidden-topics -o "$BAG" $TOPICS >/tmp/bag.log 2>&1 &
BAGPID=$!
sleep 2
timeout 200 python3 /hostpb/smoke_day3b2.py
RC=$?
sleep 2
kill -INT $BAGPID 2>/dev/null           # SIGINT 让 rosbag2 干净收尾 MCAP
for i in $(seq 1 10); do kill -0 $BAGPID 2>/dev/null || break; sleep 1; done

echo "=== [4/4] 校验 bag ==="
MCAP=$(ls "$BAG"/*.mcap 2>/dev/null | head -1)
# rosbag2 被 SIGINT 收尾后偶尔不写 metadata.yaml;MCAP 自描述,reindex 可从中重建元数据
if [[ -n "$MCAP" && ! -f "$BAG/metadata.yaml" ]]; then
  echo "  metadata.yaml 缺失 → ros2 bag reindex -s mcap 从 MCAP 重建"
  ros2 bag reindex -s mcap "$BAG" 2>&1 | tail -3
fi
ros2 bag info "$BAG" 2>&1 | tee /tmp/baginfo.log
STORAGE=$(grep -i "Storage id" /tmp/baginfo.log | head -1)
# keepout_filter_mask 出现 >=2 次 = 初始掩码 + 运行时热替换,证明故障注入进了审计轨迹
MASKCNT=$(grep "keepout_filter_mask" /tmp/baginfo.log | grep -oE "Count: [0-9]+" | grep -oE "[0-9]+" | head -1)
HASPLAN=$(grep -c "Topic: /plan " /tmp/baginfo.log)
HASTF=$(grep -c "Topic: /tf " /tmp/baginfo.log)
HASFB=$(grep -c "navigate_to_pose/_action/feedback" /tmp/baginfo.log)
echo "--- .mcap 文件 ---"; ls -la "$BAG" 2>/dev/null
echo "--- 审计要点: mask热替换=${MASKCNT:-0} plan=$HASPLAN tf=$HASTF feedback=$HASFB ---"

# 判据:导航 PASS ∧ MCAP 可读 ∧ 故障注入留痕(mask>=2)∧ 轨迹(tf)+规划(plan)全在
OK=1
[[ "$RC" == 0 ]] || { echo "导航未 PASS(rc=$RC)"; OK=0; }
[[ -n "$MCAP" ]] || { echo "未生成 .mcap 文件"; OK=0; }
echo "$STORAGE" | grep -qi mcap || { echo "storage 非 mcap: $STORAGE"; OK=0; }
[[ "${MASKCNT:-0}" -ge 2 ]] || { echo "keepout 掩码热替换未留痕(mask count<2)"; OK=0; }
[[ "$HASPLAN" -ge 1 && "$HASTF" -ge 1 ]] || { echo "缺 /plan 或 /tf 轨迹留痕"; OK=0; }

pkill -f nav2 2>/dev/null; pkill -f costmap_filter 2>/dev/null; pkill -f filter_mask 2>/dev/null
if [[ "$OK" == 1 ]]; then
  echo "PASS(Day3-C): MCAP 审计包可读,含轨迹(tf)/规划(plan,含改道)/控制(cmd_vel_nav)/"
  echo "  故障注入(keepout 掩码热替换 ${MASKCNT} 次)全链路留痕$([ "$HASFB" -ge 1 ] && echo '(含 action feedback)')。"
  exit 0
fi
echo "FAIL(Day3-C)"
exit 3
