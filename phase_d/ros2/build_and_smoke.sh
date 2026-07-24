#!/usr/bin/env bash
# 容器内一键:colcon 构建 ExecuteVLASkill 接口包 → source → 端到端 smoke。
# 跑法(宿主 PowerShell;Git Bash 会毁掉挂载路径):
#   docker run --rm -v "E:\Documents\Dev\embodied-agent-demo:/host" phase-b-nav2:latest bash /host/phase_d/ros2/build_and_smoke.sh
# 构建产物落在容器内 /tmp(不写回挂载的仓库树)。
set -e
source /opt/ros/jazzy/setup.bash
mkdir -p /tmp/vla_ws && cd /tmp/vla_ws
colcon build --base-paths /host/phase_d/ros2 --packages-select vla_skill_interfaces
source install/setup.bash
exec python3 /host/phase_d/ros2/smoke_ros2_action.py
