# 项目定位:这是什么、不是什么、要长成什么

> 一句话:**这个项目是一个"具身智能体任务级编排 + 安全运行时"(Mission Executive /
> Agent Orchestration / Safety Runtime);它的价值不是"我训了个模型",而是"我把不稳定的
> LLM / VLA / 经典机器人栈,接到同一个可调用、可约束、可取消、可恢复、可审计的运行时里"。**
> VLA 是未来挂在这个运行时下面的**一类 learned skill**,不是整个机器人系统。

## 1. 三层不是替代关系

```text
任务级编排(本项目)     自然语言目标 → 意图 → skill 队列 · 安全门禁 · 故障恢复 · 审计
  Mission Executive      秒级、离散、确定性内核

learned skill(如 VLA)  图像 + 指令 + 本体状态 → 连续动作序列(action chunk)
                         5–50Hz,概率性,挂在编排层下面

底层控制器               动作序列 → 电机 / 关节 / 轨迹 / 力矩 的稳定执行
  Servo Controller       100–1000Hz+,硬实时,独立于模型
```

**关键判断:LangGraph 不应该每 20ms 跑一个节点。** 编排停在秒级任务层,VLA 在中频闭环,
控制器在高频硬实时。本项目从设计上就守住了这个分层——`wait(tick)` 是任务级时间原语,
`assert_no_velocity_interface()` 用结构断言把"编排层拿不到速度接口"钉死(见
[ADAPTER_CONTRACT.md](ADAPTER_CONTRACT.md))。

## 2. 当前 demo 到底证明了什么(实测,不吹)

1. **任务级编排与底盘执行可解耦。** 同一套 LangGraph 编排图,从 mock adapter 切到真实
   ROS 2 / Nav2 软件栈,编排代码一行未改(Phase B)。
2. **目标级安全门禁位于 adapter 之上、独立于底盘。** 未知工具 / 越拓扑 / 禁入区 / 受限区无
   token 等请求(针对**目标节点**的 access),换到真实 Nav2 后仍被 Tool Registry 拦截
   (Phase C gate_check:5/5 拦截)。**曾经的诚实边界(codex 评审 F-01)**:门禁只约束**目标**
   的 access,不约束**过境路径**;真实 Nav2 走几何 costmap,去自由目标的路线会经过受限区 `r1`
   附近——nav_blocked 里机器人的**最近-waypoint 标签**连续 13 采样落在 `r1`(`pose=="r1"`)。
   这是 TF→最近航点 + 滞回得到的标签,是**强风险信号**,不是 polygon geofence 的入侵证明。
   **已补(F-01 强制层)**:新增与 adapter 无关的运行期访问围栏 [`geofence.py`](../embodied_agent/geofence.py)
   —— `TransitGuard` 盯机器人**实际所在节点**的位置流,一旦踏入禁入区或未授权受限区即判 transit
   违规,控制环随即取消目标、安全停,并把 `transit_violation` 终态上浮编排层。它接进真实
   `RclpyAdapter.feedback`(盯 `_cur_node()`)与 mock server 两条控制环,不依赖规划器是否 access-aware。
   **验证范围(诚实标注)**:mock 端到端 + 纯逻辑单测已覆盖;真实 `RclpyAdapter` 为同源接入,但带围栏的
   真实 Nav2 端到端复验**需容器**(见 [`phase_b/smoke_transit_guard.py`](../phase_b/smoke_transit_guard.py)),
   本轮未在真实栈跑过。这是**强制/检测**层;彻底**预防**(轨迹根本不进 `r1`)仍需把访问级下推进 costmap
   keepout(§5 路线图)。**两个方向都不精确**:喂围栏的是 `_cur_node()` 最近邻+滞回标签,快速穿越可能
   *欠检*、掠过受限 waypoint 可能*误停*——安全优先于可用性,受限区违规可用 `geo_dwell_samples` 调 dwell,
   禁入区始终单采样即停。围栏在消融(gates_off)下关闭,故地面真值 SafetyMonitor 仍能如实测未拦截时的违规。
   注:loopback 真实 runtime 的 battery/sensor 型 SafetyMonitor 仍为 mock-only;transit 围栏是**独立**
   于它的、adapter 内置的强制层。
3. **上层能处理底层解决不了的任务语义问题。** Nav2 判 `a3` 不可达 → 编排层查预注册表替换
   `a3_alt`;而普通路径受阻被 Nav2 自身 BT 消化(见 [RECOVERY_OWNERSHIP.md](RECOVERY_OWNERSHIP.md))。

**恰当的称呼**:ROS 2 / Nav2 上的具身 Agent 任务级编排参考实现。
**暂不适合的称呼**:真实机器人系统 / VLA 系统 / 端到端具身智能 / 生产级机器人安全系统。

## 3. 这个岗位真正填的三条鸿沟(对应本项目的真实机制)

| 鸿沟 | 含义 | 本项目对应机制 |
|---|---|---|
| **Semantic Gap** | 人类目标 ↔ 机器人 API | Intent 解析 + skill 队列(`navigate_to` / `perceive`;`execute_vla_skill` 已并入正式 LangGraph graph(D2)) |
| **Reliability Gap** | 概率性模型输出 ↔ 可执行系统 | Tool Registry:schema / 白名单 / 幂等重试 / 熔断 / 审批 token / 电量闸 |
| **Iteration Gap** | 失败一次 ↔ 系统变好 | append-only 事件日志(每决策一条,可回放、可证伪)→ 失败数据飞轮的地基 |

一句话:**模型给 proposal,runtime 决定能不能执行。** 这就是岗位价值,也是本项目已经在做的事。

## 4. 诚实边界(是范围,不是失败)

本项目**刻意不做**的,如实列清(详见 [PRODUCT.md](PRODUCT.md) / [phase_c/PHASE_C_RESULTS.md](../phase_c/PHASE_C_RESULTS.md)):

- **不是物理机器人 / 高保真物理仿真**:用 `nav2_loopback_sim`(cmd_vel 直接积分成 odom),
  验证 ROS 2 Action / planner / BT / adapter 语义,但不模拟碰撞、打滑、电机饱和、里程计漂移、
  传感器噪声、定位丢失、网络抖动、惯性。
- **没有真实闭环感知**:`perceive` 主路径是结构化 mock;VLM 标注只是 live-demo 辅助实验,
  未进正式控制闭环;不测误检 / 漏检 / 遮挡 / 低置信度,不做相机内外参与时间同步。
- **action space 只是拓扑目标**,不是机器人连续动作:没有操作臂 / 抓取 / 接触 / 力控 /
  action chunk / policy inference。**当前项目没有 VLA**——它是未来接 VLA 的上层壳子。
- **没有独立物理安全 supervisor**:Tool Registry 能证明"上层发不出非法工具调用",但证明不了
  "机器人不会撞人 / 机械臂不夹手 / 关节不超限"。模型和 LangGraph 都不能是最后安全边界。
- **真实评测规模小**:Phase C 只有缩减地图 × 两个目标点 × N=3/1,无物理随机化、无动态人类、
  无真实视觉、无长期运行。证明的是"接口语义 + 若干关键终态可迁移",不是"真实环境中鲁棒"。

## 5. 路线图:把 VLA 接成一类 skill(不是重做系统)

硬约束:**没有真机械臂 / GPU 集群 / 相机**,所以做不了"真机 + 遥操作采集 + 微调 SmolVLA"的
标准路线。但岗位价值的核心是**运行时**(把 learned policy 变得可调用 / 可约束 / 可取消 / 可恢复 /
可审计),这部分能在仿真里完整证明——和当初用 mock nav server 证明 adapter 契约是同一个动作。

- **Phase D(✅ 已完成,纯仿真;D1/D2 集成契约已闭合)**:`execute_vla_skill` 一个 skill(不是几十个低层动作)+
  异步 action-chunk runtime(inference/execution 并行、stale-chunk 丢弃、queue 空→hold、
  cancel 使旧结果失效)+ **独立 Safety Shield**(确定性 action projection:workspace /
  velocity / magnitude limit;沿 runtime 执行路径 policy 绕不过它)+ mock VLA policy(桌面 pick 玩具)。
  挂在**现有** Tool Registry / exception_manager / event log 下。**证明"安全监管一个 learned
  policy"这条主张,不需要真机。**
  **集成契约:D1/D2 已闭合**(PR #16/#17)——异步 goal-handle 四工具(在飞可取消)、
   独立后置校验(回读末态、记 `agrees_with_skill`)、composite 并入**正式 LangGraph graph**、
   版本化 Policy Contract(action/execution horizon + 逐动作新鲜度)、ROS 2 `ExecuteVLASkill`
   Action(容器内 colcon 构建 + smoke 实测)。**仍然成立的边界**:shield 令牌加固只关掉了
   "一行 import 就能伪造"的洞,同进程 Python 仍非不可绕过(类型级约定,非进程隔离);
   宿主 registry 接的仍是 in-process SkillServer;**skill 执行期间不推进虚拟世界时钟**
   (mock 无操作能耗模型 ⇒ 操作中不耗电、不触发故障注入/低电量抢占)。
- **Phase E+(需要硬件才做)**:真臂 + 遥操作数据 + SmolVLA/ACT 微调 + 真实闭环 eval +
  intervention 数据飞轮。属未来,不在当前范围。

**正确接法**:LangGraph 只启动一个 VLA skill、不逐帧调 VLA;VLA 只负责接近 / 抓取 / 搬运 /
放置 / 局部视觉纠正,不负责选工作台 / 权限 / 电量决策 / 全局导航 / 人工确认 / 审计。
恢复归属见 [RECOVERY_OWNERSHIP.md](RECOVERY_OWNERSHIP.md)。

## 6. 最终形态(愿景,标注为愿景)

```text
自然语言目标 → Mission Planner / LangGraph → 确定性 Skill & Policy Registry
   ├─ Nav2(全局导航)  ├─ MoveIt(几何规划)  ├─ VLA skill(接触操作)  ├─ VLM(语义观测)
                              ↓
                  独立 Safety Supervisor(确定性,最后边界)
                              ↓
                  ROS 2 Controllers / Hardware
                              ↓
                  Observer / PostCheck / Replay → Failure Data Flywheel
```

真正的主张不是"我训了个 VLA",而是:**我把经典机器人栈和 learned policy 接到同一个可控
runtime 中,明确了各层恢复职责,并让 learned skill 能被安全取消、异步执行、事后验证、失败再训练。**
