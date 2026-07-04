# Phase B 设计:接入真实 ROS2 / Nav2

> 目标:把 mock 导航底盘替换为真实 Nav2 栈,**接口契约不变**,评测方法不变,
> 让"确定性编排层 + 预注册评测"在真实导航栈上复现。本文件是一份可执行的设计,
> 不是 PLAN 里那句"尽力而为"——研究阶段已查证 2026-07 的版本与机制(见文末来源)。

## 0. 一页速览(给赶时间的人)

| 决策 | 选型 | 一句话理由 |
|---|---|---|
| ROS2 发行版 | **Jazzy Jalisco**(2024 LTS,支持到 2029) | Nav2 有 apt 二进制,教程最完整;同时避开 12 月 EOL 的 Kilted |
| 仿真层 | **nav2_loopback_sim**(不用 Gazebo) | 无物理/无 GPU/无渲染,直接把 cmd_vel 回环成 odom;最确定、最省心,也正好匹配我们"规划器+行为树+action 契约"的评测层级 |
| 编排层位置 | **容器内**(rclpy ActionClient) | DDS 在同一 Linux 网络内可自动发现;避开 Windows↔容器的 DDS 跨界痛点 |
| 可视化 | **foxglove_bridge → Windows 浏览器** | headless,通过 websocket 跨界更干净,且不扰动评测 |
| 恢复归属 | **给 Nav2 换裸 BT**(无 RecoveryNode) | Nav2 不自救,把恢复完整交给我们的 exception_manager——这正是 demo 的核心主张 |
| 受阻边注入 | **keepout costmap 滤镜,运行时重发掩码** | 封掉某条走廊的栅格,规划器实时绕行;在 loopback 下同样生效 |
| 外部标尺 | **BARN**(ICRA 2026 仍活跃) | 唯一仍在活跃维护、有 ROS2 管线、有评分公式的受限导航 benchmark |
| 审计数据 | **rosbag2 / MCAP** | Iron 起默认,自包含 schema;可作为我们 JSONL 事件日志的地面真值交叉校验 |

**止损线不变**(PLAN §0):容器内 `ros2 action list` 若在约定时限内看不到
`navigate_to_pose`,放弃 Phase B——Phase A 已经是完整、可讲清楚的 demo。

## 1. ROS2 / Nav2 是什么(30 秒版)

- **ROS2**:机器人软件的进程间通信标准。程序叫 **node**,广播数据流叫 **topic**,
  一问一答叫 **service**,**长时任务的完整协议(发目标/收进度/可取消/有终态)叫 action**。
  底层通信用 DDS(默认 Fast DDS)。
- **Nav2**:ROS2 上的标准导航栈。核心是一个行为树总指挥(`bt_navigator`)调度
  全局规划(`planner_server`)、局部控制(`controller_server`)和恢复动作
  (`behavior_server`),并在两张代价地图(全局 + 局部滚动窗口)上工作。
- **对上我们**:我们 mock 的 `send_goal/feedback/cancel/result` 本来就是照着 Nav2 的
  `NavigateToPose` action 设计的——评审 B1 把阻塞接口改成异步 goal-handle,就是在为今天铺路。

## 2. 架构:两种连法,我们选容器内

```
┌─ WSL2 / Docker(一个 Linux 网络)──────────────────────────┐
│  Nav2(ros:jazzy + navigation2)                            │
│   bt_navigator · planner_server · controller_server        │
│   costmap(全局/局部)+ keepout 滤镜                        │
│  nav2_loopback_sim(cmd_vel→odom/tf,假激光,无物理)       │
│  map_server(占据栅格,由 layout 表生成)                   │
│        ▲ rclpy NavigateToPose ActionClient                 │
│  ┌─────┴───────────────────────────────────┐              │
│  │ 我们的编排层(容器内运行)               │              │
│  │ RclpyAdapter ← 实现 RobotAdapter 契约    │              │
│  │ LangGraph 图/registry/evaluation 原样跑  │              │
│  └──────────────────────────────────────────┘              │
│  foxglove_bridge :8765 ──ws──▶ Windows 浏览器 Foxglove     │
└────────────────────────────────────────────────────────────┘
```

**为什么编排层放容器内**:Windows↔容器的 DDS 发现**不可靠**(多播 UDP 不跨 Docker
边界,`network_mode: host` 只看到内部 VM)。放在同一个 Linux 网络内,DDS 可以自动发现;
我们的 action 契约也能 1:1 映射到真实 action,审计日志还能直接 tap 同一个 ROS 图。
Windows 那侧只保留一件真正适合跨界的事:**可视化**(foxglove,走 websocket)。

> 备选(不推荐但可行):编排层留在 Windows,用 **roslibpy 2.1.0**(2026-06 发布,
> 新增 ROS2 `ActionClient`)经 **rosbridge websocket** 驱动 Nav2。契约也能对上
> (`send_goal/wait_goal/cancel_goal`),但会多一层 JSON 序列化延迟,而且 roslibpy 的
> ROS2 支持文档仍标着"in progress"——仅作为"必须在 Windows 侧跑"时的逃生门。

## 3. RobotAdapter → Nav2 逐项映射(契约不变)

我们的 [ADAPTER_CONTRACT.md](ADAPTER_CONTRACT.md) 契约,换成 `RclpyAdapter` 后:

| 契约方法 | Nav2 实现 |
|---|---|
| `send_goal(node_id)` | 查 `waypoints.yaml` 得 pose → `ActionClient.send_goal_async(NavigateToPose.Goal(pose, behavior_tree=裸BT))`,`feedback_callback` 收流 |
| `feedback(goal_id)` | 缓存最近一帧:`distance_remaining`(沿路径积分)、`number_of_recoveries`、`navigation_time` |
| `feedback.stall_ticks`(受阻信号) | adapter 侧看 `distance_remaining` 是否停滞——**受阻仍不是 Nav2 状态,由编排层水位检测**(契约核心约定,一行不改) |
| `result: succeeded` | `GoalStatus.SUCCEEDED`,`error_code=0` |
| `result: unreachable` | `ABORTED` + 传播上来的规划器码(见 §4) |
| `cancel(goal_id)` | `goal_handle.cancel_goal_async()` |
| `get_state()`(pose/battery/sensor) | pose 从 `/odom` 或 `/amcl_pose`;battery/sensor **仍是 mock 注入**(loopback 无这些) |

**高层脚本可选简化**:`nav2_simple_commander` 的 `BasicNavigator`
(`goToPose` / `isTaskComplete` / `getFeedback` / `getResult`)封装了 action 样板,
可作 adapter 内部实现,少写几十行 rclpy。

## 4. ⚠️ 错误码映射(研究纠正的一处硬伤)

**我们旧契约写"error code ∈ {NO_VALID_PATH, GOAL_UNREACHABLE}"是错的。**
真实情况(已查证 api.nav2.org,Jazzy/Kilted):

- `NavigateToPose.result` **自身只定义**:`NONE=0`、`UNKNOWN=9000`、
  `FAILED_TO_LOAD_BEHAVIOR_TREE=9001`、`TF_ERROR=9002`、`TIMEOUT=9003`。
- `NO_VALID_PATH` 是**规划器**(`ComputePathToPose`)的错误码(100 段),
  由 `bt_navigator` 经 `error_code_name_prefixes` **上浮**进 `NavigateToPose.error_code`;
  `GOAL_UNREACHABLE` 这个名字根本不存在。
- 错误码机制自 **Iron(2023)** 引入;我们目标的 Jazzy 在 Iron 之后,因此安全。

**落地动作**:`RclpyAdapter` 把"不可达"判定为
`error_code` 属于规划器码段(需在 Nav2 配置里设 `error_code_name_prefixes` 让规划器码上浮),
而不是去匹配不存在的常量名。这条如果不查证,Phase B 第一天就会踩坑。

## 5. 五类故障怎么在真实栈上注入

| 故障 | Phase A(mock) | Phase B(Nav2)实现 | 可移植? |
|---|---|---|---|
| 受阻边 | `block_active_edge` | **keepout 滤镜**:运行时往掩码 topic 重发 `OccupancyGrid`,把该走廊栅格标占据,规划器实时绕行(掩码 sub 是 transient_local+reliable,带重处理回调) | ✅ 真实机制 |
| 点位不可达 | `isolate_node` | keepout 掩码把目标点四周封死 → 规划器返 `NO_VALID_PATH`(上浮) | ✅ |
| 低电量 | 线性衰减 | **mock-only**:loopback 无电池;继续用注入 `/battery_state` 或 adapter 侧模型 | ⚠️ mock 层 |
| 传感器异常 | `sensor_down` | **mock-only**:loopback 假激光;注入层伪造 `/scan` 失效 | ⚠️ mock 层 |
| 工具失败 | schema 抖动/超时 | adapter 层注入,与底座无关 | ✅ 层内 |

诚实标注(延续 Phase A 作风):**battery/sensor 是 mock 层注入,不是物理仿真**。
loopback_sim 明确不模拟:物理/摩擦/碰撞/传感器噪声/定位误差——机器人会穿过障碍
(除非喂真地图/掩码)。这对"规划器+行为树+恢复"这一评测层级来说**完全够用**,也正是
我们要评测的层级;真正的动力学/碰撞留给未来的物理仿真。

## 6. 让 Nav2 不自救(demo 核心配置)

Nav2 默认 BT(`navigate_to_pose_w_replanning_and_recovery.xml`)顶层是
`RecoveryNode(6 次重试)`,卡住会自己清代价地图 → 原地转 → 等待 → 后退。
**这会抢走我们编排层的恢复职责。** 标准做法(已确认是文档化的标准路径):

写一棵约 10 行的**裸 BT**——只保留 `PipelineSequence` 里的 `ComputePathToPose` +
`FollowPath`,**没有顶层 RecoveryNode、没有 RoundRobin 恢复子树**——再经
`default_nav_to_pose_bt_xml` 或每个目标的 `behavior_tree` 字段传入。
这样 Nav2 在图内失败时会如实返回错误码,恢复则完全交给我们的 `exception_manager` 查表处理。

> 面试点:不是不会用 Nav2 自带恢复,而是**把恢复上移到可预注册、可审计的层**——
> 这正是 demo 想证明的东西。`behavior_server` 可以照跑,只是 BT 不 tick 它。

## 7. 意外之喜:route_server 就是我们的拓扑图

Nav2 现在有 **`route_server`(`nav2_route` 包)**:"在预定义导航图上算路径,
而不是自由空间规划……对每条边用插件式打分函数施加任意语义信息。"
**这正好就是我们 11 节点/13 边、每条边带代价的数据模型。** 两条路可选:

- **A(先做)**:freespace 规划 + 我们自己的拓扑层(node→pose,水位检测受阻)——
  最贴近现有 mock 语义,迁移也最直接;
- **B(可选加分)**:直接用 `route_server` 把我们的拓扑图喂进去,让 Nav2 在图上导航,
  受阻边 = 抬高边代价/禁用边——这是一个"我们的抽象恰好是 Nav2 一等公民"的强论据。

## 8. 评测:预注册照旧 + BARN 做外部标尺

我们的评测方法**一字不改**(预注册故障注入 × 10 seed,指标只读事件日志),
Phase B 只**加一个外部标尺**,用来证明底层能力不虚:

- **BARN**(GMU,ICRA 2026 第 5 届仍活跃):300 个程序生成的受限世界 + 评分公式
  `成功 × 最优时间/clip(实际时间, 2×, 8×)`。官方提供 ROS2 评测管线。
  预注册取固定子集(如按难度分层的 50 个世界)× 我们的 10 seed,报 BARN 分。
  **注意先查**:BARN 世界是 Gazebo Classic 格式,需确认 2026 ROS2 管线能在我们的
  Nav2/Gazebo 版本跑通(退路:转成 gz-sim SDF)。
- **互补性**(面试关键句):BARN 压的是"受限空间规划器/控制器强不强",我们的预注册故障
  注入压的是"编排层恢复能力"——**没有任何外部 benchmark 覆盖后者**,这就是我们值得发表的点。
- **指标**:成功率、到达时间(也报 BARN 归一化)、路径长度比、碰撞数、
  恢复次数(取 Nav2 feedback 的 `number_of_recoveries` + `/behavior_tree_log`)、
  顺手报 **SPL**(有成功+长度就是白送,让 embodied-AI 读者一眼看懂)。

## 9. 数据:rosbag2 / MCAP 做审计地面真值

- **格式**:rosbag2 + **MCAP**(ROS2 Iron 起默认,自包含 schema,换工作区多年后仍可回放)。
- **每 run 录一包**,topic 集:`/tf` `/tf_static` `/odom` `/cmd_vel` `/scan`
  `/map` `/plan` 全局+局部代价地图(降频)`/behavior_tree_log`(恢复计数关键信号)
  NavigateToPose 的 goal/feedback/result `/battery_state` `/diagnostics`。
- **分工**:JSONL 事件日志仍是**指标主源**(延续"指标不读 agent 内存"),
  rosbag 则作为**地面真值交叉校验**;每包存 seed/world/fault-spec/version 元数据。

## 10. 落地清单(按天,带止损)

1. **Day 1**:`ros:jazzy-ros-base` + `ros-jazzy-navigation2` + `ros-jazzy-nav2-bringup`
   起容器;`ros2 launch nav2_bringup tb3_loopback_simulation.launch.py`(headless,
   不起 rviz);`ros2 action send_goal navigate_to_pose ... --feedback` 手动发一个目标
   看 feedback。**止损点:这步跑不通就停,Phase A 已完整。**
2. **Day 1 晚**:后台 `docker pull`(避免占用调试窗口)。
3. **Day 2**:layout 表生成占据栅格 PGM + `waypoints.yaml`;写裸 BT xml;
   `RclpyAdapter` 实现契约(`nav2_simple_commander` 打底);跑通单点导航。
4. **Day 2**:keepout 滤镜接进 `FaultInjector`,验证受阻边实时绕行。
5. **Day 3**:编排层容器内跑通一个受阻恢复全闭环;foxglove 看轨迹;录第一个 rosbag。
6. **可选**:BARN 子集接入 / route_server 变体 / 物理仿真(仅当某故障需要真碰撞)。

**禁区**(PLAN §0 记的旧坑):不碰 GPU/渲染调试(WSL RADV 历史问题);
headless + foxglove websocket,永不开 RViz/WSLg。

---

## 来源(2026-07 查证)

- 发行版/EOL:endoflife.date/ros-2;Nav2 二进制覆盖 index.ros.org/nav2_core。
- rclpy ActionClient:ros2/examples minimal_action_client;api.nav2.org NavigateToPose。
- 错误码:api.nav2.org/actions/kilted/navigatetopose.html(NONE/UNKNOWN/FAILED_TO_LOAD_BEHAVIOR_TREE/TF_ERROR/TIMEOUT);Humble→Iron 迁移"BT Uses Error Codes"。
- 裸 BT / keepout / loopback_sim:github.com/ros-navigation/navigation2(behavior_trees、costmap_filters、nav2_loopback_sim);Macenski 2024-08 Discourse loopback 公告。
- route_server:nav2_route 包文档。
- websocket 路径:RobotWebTools/rosbridge_suite(ros2 分支,action 支持);roslibpy 2.1.0(pypi,2026-06-30,新增 ROS2 ActionClient)。
- WSL2/DDS/Foxglove:Nav2 "Docker for Development"(Win11+WSL2 为主平台);foxglove_bridge 3.3.0(Jazzy,2026-05)。
- BARN:people.cs.gmu.edu/~xiao/Research/BARN_Challenge/BARN_Challenge26.html。
- 数据:rosbag2_storage_mcap(Iron 起默认);Foxglove/mcap CLI。
