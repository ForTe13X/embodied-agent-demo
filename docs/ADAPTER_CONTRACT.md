# RobotAdapter 契约(mock ⇄ rclpy 可换的依据)

评审 M5(Phase B 语义映射)要求把"同一接口"写成可核对的规范,而不是口号。本文件即该规范。

## 1. 接口(异步 goal-handle 式,与 rclpy ActionClient 同构)

```python
send_goal(target, *, authorized, avoid_edges, allow_restricted, allow_forbidden_target) -> {goal_id}
feedback(goal_id) -> {status, current_node, current_edge, edges_done, edges_total, velocity, stall_ticks}
result(goal_id)  -> None(在飞)| {status, reason, ticks}
cancel(goal_id)  -> bool
get_state() -> {pose, battery_pct, nav_status, sensor_health, docked}
get_map()   -> {nodes:[{id,name,access,neighbors}]}
sense(query) / capture() / wait(ticks)
```

`wait(ticks)` 是时间推进原语:mock 下推进虚拟时钟并驱动世界;rclpy 下为真实 sleep(tick=秒)。

## 2. 状态推导规则(两个 adapter 必须各自实现同一语义)

| 契约状态 | mock 的来源 | rclpy/Nav2 的推导 |
|---|---|---|
| `succeeded` | 到达目标节点 | `GoalStatus.SUCCEEDED` |
| `aborted` + `reason=unreachable` | 节点被隔离/无路由 | `ABORTED`;`NavigateToPose.result.error_code` 为**传播上来的规划器码**(planner `ComputePathToPose` 的 `NO_VALID_PATH` 等,经 bt_navigator 的 `error_code_name_prefixes` 上浮)。注意:`NavigateToPose` 自身只定义 `NONE=0 / UNKNOWN=9000 / FAILED_TO_LOAD_BEHAVIOR_TREE=9001 / TF_ERROR=9002 / TIMEOUT=9003`,不含 `NO_VALID_PATH`(见 PHASE_B_DESIGN.md §错误码映射) |
| `canceled` | cancel() | `CANCELED` |
| **受阻(无独立状态!)** | 边被阻断 → progress 停滞、velocity=0 | feedback 中 `distance_remaining` 停滞、速度≈0 |

**关键约定:`blocked` 不是 adapter 状态。** 底盘(mock 或 Nav2)都只表现为 feedback 停滞;
受阻由编排层 observer 的停滞水位(`stall_ticks >= STAGNATION_THRESHOLD_TICKS`)检测。
这保证 mock 语义不泄漏进契约,Phase B 换 adapter 后检测逻辑一行不改。

## 3. 拓扑节点 → 度量位姿

真实机器人没有拓扑图。node→pose 映射表是 **adapter 层拥有的静态 YAML**(Phase B 交付物
`waypoints.yaml`:`{node_id: {x, y, yaw}}`),两个 adapter 共享同一份节点 id 空间。
编排层永远只见 node_id。

## 4. 阈值归 adapter 配置

停滞水位、单目标超时、tick 时长都是 adapter/运行时配置(`runtime.py` 常量,Phase B 移入
adapter 配置),不是注册表常量——mock 时间尺度和 sim-time Nav2 不同,阈值必须随 adapter 走。

## 5. 故障注入能力

`FaultInjector` 挂在 adapter 之下。Phase B 可移植的故障:`block_active_edge`(在仿真里
放障碍物)、`isolate_node`(移除可达位姿)。**诚实声明:battery / sensor / tool 故障是
mock-only**,README 的结果表只代表 mock adapter。

## 6. rclpy 适配器(Phase B Day-2,**已实现并在真实 Nav2 上冒烟通过**)

见 `phase_b/rclpy_adapter.py`。要点:

```python
class RclpyAdapter:
    """运动接口 = BasicNavigator 内部的 NavigateToPose ActionClient(唯一下达运动的通道)。
    结构性断言 assert_no_velocity_interface():枚举本 adapter 节点的 publishers,出现
    cmd_vel/velocity/torque/effort 类 topic 即 fail —— 'LLM 拿不到速度接口'是可核对的结构事实。
    诚实修正:节点确实有 /initialpose 发布器(定位引导,非运动指令)+ /parameter_events /rosout;
    断言只针对速度/力矩类 topic,故通过。此即"不暴露速度接口",而非"节点零 publisher"。"""
```

已在真实 Nav2 核对(`smoke_day2c.py`):succeeded / canceled / unknown_node 三终态 + 非阻塞
send_goal + feedback 拓扑推进 + 结构性无速度接口。**尚未覆盖**:`aborted+unreachable`
(隔离节点/受阻边)与 `blocked` 停滞水位 —— 需先接入 keepout 掩码(costmap_filter),列 Day-3。

阈值(tick 时长、停滞水位)归 adapter 配置(§4):rclpy 下 `ticks = 墙钟秒 / tick_seconds`,
与 mock 的虚拟 tick 数值不同尺度,**上层不得跨 adapter 复用同一超时常量**。

## 7. Phase B 止损线(预先承诺,防沉没成本)

第 4 天 13:00 前,容器内 `ros2 action list` 看不到 `/navigate_to_pose`(headless TB3+Nav2
bringup)即放弃 Phase B。禁止调试 GPU/渲染(WSL RADV 旧坑):headless + Foxglove websocket,
永不开 RViz/WSLg。第 3 天晚上只允许做一件 Phase B 的事:后台 `docker pull`。
