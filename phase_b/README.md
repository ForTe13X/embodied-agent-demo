# Phase B 实施(ROS2/Nav2 接入)

设计见 [../docs/PHASE_B_DESIGN.md](../docs/PHASE_B_DESIGN.md)。本目录是可执行落地。

## Day 1:止损冒烟(先跑这个)

在 Windows PowerShell(不要用 Git Bash,会改容器路径):

```powershell
docker build -t phase-b-nav2 phase_b
docker run --rm phase-b-nav2 bash /work/smoke_day1.sh
```

**判定**:输出 `PASS: … Day1 止损点通过` = Nav2 + loopback + NavigateToPose 契约在本机跑通,
可继续 Phase B;输出 `FAIL: … 止损` = 停在这里,Phase A 已是完整可讲的 demo。

冒烟脚本做了什么:headless 起 `tb3_loopback_simulation`(map_server + loopback_simulator +
完整 Nav2,不起 rviz)→ 等 `/navigate_to_pose` action 出现 → 设 initialpose → 发一个
NavigateToPose 目标看 feedback。全程无 GPU、无物理仿真、无渲染。

## 后续(止损点通过后)

- Day 2:layout 表 → 占据栅格 PGM + `waypoints.yaml`;裸 BT xml;`RclpyAdapter` 实现契约;
- Day 2:keepout 滤镜接进 `FaultInjector`,验证受阻边实时绕行;
- Day 3:编排层容器内跑通受阻恢复全闭环 + foxglove + 录 rosbag。

镜像刻意精简(不装 rviz/gazebo),对齐 Nav2 官方 headless CI。
