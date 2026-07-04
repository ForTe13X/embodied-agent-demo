# Phase B:真实 Nav2 栈(Jazzy)+ loopback 仿真,headless。
# 目标层级:规划器/行为树/action 契约 —— 不装 rviz/gazebo(见 PHASE_B_DESIGN.md)。
FROM ros:jazzy-ros-base

SHELL ["/bin/bash", "-c"]

# navigation2 + bringup + loopback 仿真 + tb3 最小仿真资产(地图/URDF)
# 分组安装,便于定位缺包;失败的组会在构建日志里直接报出来
RUN apt-get update && apt-get install -y --no-install-recommends \
      ros-jazzy-navigation2 \
      ros-jazzy-nav2-bringup \
    && rm -rf /var/lib/apt/lists/*

RUN apt-get update && apt-get install -y --no-install-recommends \
      ros-jazzy-nav2-loopback-sim \
      ros-jazzy-nav2-minimal-tb3-sim \
    && rm -rf /var/lib/apt/lists/*

# 我们编排层需要的 Python(容器内直接跑 RclpyAdapter,后续步骤用)
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3-pip \
    && rm -rf /var/lib/apt/lists/*

# 自动 source ROS 环境
RUN echo "source /opt/ros/jazzy/setup.bash" >> /root/.bashrc

WORKDIR /work
COPY smoke_day1.sh /work/smoke_day1.sh
RUN chmod +x /work/smoke_day1.sh

CMD ["bash"]
