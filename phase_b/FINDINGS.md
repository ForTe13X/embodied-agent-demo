# Phase B Day 1 结果与发现(实测,2026-07-04)

在本机(Windows 11 + WSL2 + Docker Desktop 29.3.1)真实跑通了 Day 1 止损冒烟。**止损点通过**。

## 通过了什么(契约层,决定性)

| 检查 | 结果 | 证据 |
|---|---|---|
| Nav2 Jazzy 容器构建 | ✅ 5.08GB(清华镜像源) | `docker build phase_b` |
| Nav2 全栈 headless 起来 | ✅ 所有 lifecycle 节点 active | bt_navigator/planner/controller/behavior/collision_monitor/route_server 等 |
| `/navigate_to_pose` action 契约 | ✅ 目标接受 → feedback → 终态 | goal accepted;feedback 字段 = 设计文档 §3(current_pose/distance_remaining/number_of_recoveries) |
| 可达目标终态 | ✅ SUCCEEDED,error_code=0 | 目标 (0.5,0) |
| **不可达目标错误码** | ✅ ABORTED,**error_code=208**(规划器码段) | 目标 (1.5,0.5) 落在 tb3 障碍里,`compute_path_to_pose` 返 "Failed to create plan" |

**结论**:我们 mock 的 `send_goal/feedback/cancel/result` 异步 goal-handle 契约与真实 Nav2
**1:1 对得上**;错误码 208 落在规划器码段,验证了设计文档 §4 的错误码上浮机制
(不可达故障 = 规划器码经 `error_code_name_prefixes` 上浮,而非 NavigateToPose 自身的码)。

## 踩到并解决的坑(留作复现记录)

1. **apt 装 nav2 网络失败**(IPv6 超时 + packages.ros.org 500 + deb-src 404):
   换清华 TUNA 镜像源 + 强制 IPv4 + 去掉 `deb-src`。见 Dockerfile。
2. **Nav2 bringup 中止**(`global_costmap` 激活失败,`Invalid frame ID "map"`):
   loopback_simulator 要先收到 `/initialpose` 才发布 map→odom TF,而 autostart 在此之前
   就激活代价地图、10s 等不到 map 帧即中止。修复:launch 一起来就后台循环发 `/initialpose`,
   让 TF 在代价地图激活窗口内建立。
3. **BasicNavigator 卡在 amcl 等待**:loopback 无 amcl,`waitUntilNav2Active(localizer='robot_localization')`
   跳过 amcl 等待。
4. **initialpose 循环干扰导航**:导航前必须停掉循环(否则每秒把机器人瞬移回原点)。

## 未通过 / 交给 Day-2 的项(诚实清单)

**机器人在 loopback 里几乎不平移**(TF 末位移 ≈ 0,却报 SUCCEEDED)。诊断链:

- `/cmd_vel` 12s 采样全零;`/cmd_vel_nav`(控制器直出)只有 **~0.004 m/s** 的微速度;
- 不是 collision_monitor 拦的(`/cmd_vel_nav` 就已经是微速度);
- 是 tb3 默认控制器配置 + turtlebot3_world 地图在原点附近的组合导致控制器几乎不前进,
  且 goal checker(容差 0.25m)在这种配置下判成功。

**为什么不在这里深追**:Phase B 真正的世界是**我们自己从 layout 表生成的占据栅格 + 航点**
(设计文档 §3/§10 Day-2),不会用 tb3_world 这张地图,也会为它配控制器参数。在一张即将丢弃的
tb3 地图上调控制器,越过了 Day-1 止损点问的问题(「契约能否对上真实 Nav2」——已 YES)。

**Day-2 待办**(移动性属这一档):
- layout 表 → 占据栅格 PGM + `waypoints.yaml`(货架=占据、走廊=自由);
- 裸 BT xml(无 RecoveryNode,恢复归编排层);
- `RclpyAdapter` 实现契约(`BasicNavigator` 打底,已在 smoke_day1.py 验证可用);
- 配控制器 + 验证机器人真平移;keepout 滤镜做受阻边故障。

## 复现

```powershell
docker build -t phase-b-nav2 phase_b
docker run --rm -v "${PWD}\phase_b:/hostpb" phase-b-nav2 bash /hostpb/smoke_day1.sh
# 诊断脚本:diag.sh(规划器)/ diag3.sh(cmd_vel)/ diag4.sh(cmd_vel 链路)
```
