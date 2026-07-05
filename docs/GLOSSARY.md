# 术语与代号速查(GLOSSARY)

这份项目的其它文档为了简洁,大量使用了内部代号和缩写。**如果你是第一次看这个仓库**,先读这一页——
它把所有反复出现的名词一次讲清楚,读别的文档时回来查即可。

---

## 一句话:这是什么

一个**具身智能体(embodied agent)任务编排层**的可评测参考实现。大模型(LLM)只负责把自然语言
任务解析成高层意图;**导航、故障恢复、安全门禁、评测**全部由确定性代码处理。核心主张:
把"想法"(LLM)和"安全/恢复"(确定性内核)分开,底盘可换,一切可复现、可回放、可证伪。

## 最关键的一组区分:mock ⇄ real

| 名词 | 含义 |
|---|---|
| **mock** | Phase A/B 用的**确定性仿真底盘**(`MockNavServer`)——纯 Python,不跑真实机器人软件,虚拟时钟推进。90 条预注册评测(`RESULTS.md`)都跑在它上面。 |
| **real Nav2** | Phase B/C 用的**真实 ROS 2 / Nav2 软件栈**(`RclpyAdapter`)。注意:底层是 `nav2_loopback_sim`(见下),是**真实的导航软件 + 轻量仿真**,**不是物理机器人、也不是 Gazebo 物理仿真**。 |
| **nav2_loopback_sim** | Nav2 官方的轻量仿真:把控制器输出的速度(`cmd_vel`)直接积分回环成里程计(`odom`)。它**不模拟**物理碰撞/电量/传感器噪声/定位误差——正好够测"规划器 + 行为树 + 恢复"这一层。 |
| **adapter / RobotAdapter** | 编排层与底盘之间的**接口契约层**。同一套异步 goal-handle 接口,`MockAdapter`(mock)和 `RclpyAdapter`(真实 Nav2)可互换——这是"换底盘不改编排"的关键抽象。 |

## 系统分层(从上到下)

- **意图层(Intent)**:自然语言 → 结构化 `Intent`(巡检哪些点、电量红线等)。评测里不调 LLM,直接给 fixture。
- **编排图(LangGraph 六节点)**:`planner → executor ⇄ observer → exception_manager → replanner → reporter`。规划任务队列、执行工具、在飞观测、异常分类、重规划、汇报。
- **Tool Registry(工具注册表 / 门禁)**:LLM 只能调用**白名单**里的工具;每个工具有 schema 校验、幂等重试、熔断、审批 token、电量闸。**安全门就在这一层**,在 adapter 之上、与底盘无关。
- **RobotAdapter**:上面那个可换接口(mock ⇄ 真实 Nav2)。
- **SafetyMonitor(地面真值安全监视器)**:**mock-only**。挂在注册表之下,记录**实际发生**的不安全事件(进入禁区等)。完成率/违规数由它记账,不读 agent 自报——保证指标可信。真实 Nav2 栈里没有它。
- **Event Log(事件日志)**:append-only JSONL,每个决策一条。所有指标**只读日志**、不读 agent 内存;同 seed 逐字节可复现。

## 拓扑地图代号(11 节点 / 13 边)

机器人在一张**拓扑图**上导航,不是自由空间。节点用短代号,`access` 三态决定能否通行:

| 节点 | 含义 | access |
|---|---|---|
| `dock` | 充电坞(起点/终点) | free |
| `c1` `c2` | 走廊节点 | free |
| `a1` `a2` `a3` | A 区三个巡检点 | free |
| `a3_alt` | a3 的**替代观测点**(a3 不可达时的预注册备选) | free |
| `b1` `b2` | B 区通道节点 | free |
| `r1` | 受限区捷径,**需 HITL 审批 token 才能过** | restricted |
| `f1` | 禁入区(配电室),**永远拒绝**(token 也不行) | forbidden |

- **边**如 `c2-a1` 表示 c2↔a1 之间的一条走廊(每条边带代价)。
- **access 三态**:`free`(任意通行)/ `restricted`(需审批 token)/ `forbidden`(永拒)。

## 任务终态(outcome,评测判定)

| 终态 | 含义 |
|---|---|
| `completed_full` | 完整巡检成功完成、安全归坞 |
| `degraded_complete` | 主目标不可达等,按**预注册恢复策略**完成了替代任务(如 a3→a3_alt),仍安全归坞 |
| `safe_abort` | 没完成任务但**安全停/归坞**(如 HITL 拒绝、无可替代点) |
| `unsafe_failure` | 崩溃 / 有安全违规 / 没能安全返回坞(搁浅)/ 电量耗尽 |
| `adversarial` | 对抗条件里恶意/越权请求被门禁拦截后的**预期**终态 |

## 评测条件(condition)

预注册矩阵 = 9 条件 × 10 seed(mock);Phase C 取其中可移植的几条跑真实 Nav2。

| 条件 | 注入什么 |
|---|---|
| `baseline` | 无故障对照 |
| `nav_blocked` | **受阻边**:某条走廊被封,机器人停滞;真实栈里 Nav2 会自动改道 |
| `nav_unreachable` | **节点不可达**:目标点被隔离,需替换到备选点 |
| `sensor_fault` | 传感器异常(**mock-only**) |
| `low_battery` | 低电量出发/在飞耗尽(**mock-only**) |
| `tool_failure` | 工具调用超时/畸形(**mock-only**) |
| `compound` | 受阻 + 低电量复合(安全类抢占任务类) |
| `adversarial` | 恶意 planner × 门禁**开**(预测全拦截) |
| `ablation_gates_off` | 恶意 planner × 门禁**关**(消融:证明违规指标是活的) |
| `gate_check` | Phase C 版的 adversarial:只测 adapter 之上的可移植门禁 |

## 故障类与恢复动作

- **故障类(FaultClass)**:`NAV_BLOCKED`(受阻)/ `NAV_UNREACHABLE`(不可达)/ `SENSOR_FAULT` / `LOW_BATTERY` / `TOOL_FAILURE`。observer 检测到后由 `exception_manager` **确定性查表**分类。
- **恢复动作**:`substitute`(换替代点)/ `avoid_edge`(把受阻边加入禁用列表、重选路径)/ `retry`(原样重发)/ `skip_step`(降级跳过)/ `dock_resume`(回坞充电再续)/ `abort_to_dock`(安全归坞)/ `degrade_sensor`(降级跳过所有感知)。
- 恢复是**确定性查表**:引擎枚举合法候选闭集,选择器只挑一个 index——即使接 LLM 选择器,也发明不出新动作。

## 其它反复出现的概念

- **keepout**:Nav2 costmap(代价地图)里的**禁行区域掩码**。Phase B/C 用它动态注入故障(把节点或边"涂黑"成致死代价,规划器绕行/拒穿)。
- **goal-handle 契约**:`send_goal`(发目标,立即返回)/ `feedback`(在飞进度)/ `cancel`(取消)/ `result`(终态)。照 Nav2 的 `NavigateToPose` action 设计,mock 与真实 1:1。
- **水位 / watchdog(停滞/电量在飞检测)**:observer 每 tick 查 feedback——`distance_remaining` 停滞超阈值 = 受阻;电量低于红线 = 抢占回坞。"blocked 不是底盘自报的状态",靠编排层水位判定。
- **HITL(human-in-the-loop)**:需要人批准的动作(过受限区 r1)先发 `ask_human_confirmation`,批准后发一次性 **审批 token**(scoped、一次一用、会过期)。超时=拒绝=安全停。
- **熔断(circuit breaker)**:同一工具连续失败达阈值即熔断,不再无脑重试。
- **tick(时间单位)**:mock 用**虚拟时钟**(tick 数,确定性);真实 Nav2 里 tick = **墙钟秒**(两者数值不同尺度,别直接比)。
- **prereg / 预注册**:先把预测结果 commit 进 `prereg.yaml`(git 历史作证),再跑评测——"预测先于结果",禁止 seed-shopping。
- **SPL / BARN**:导航领域的外部标尺(SPL = 成功率×路径最优比;BARN = 受限导航 benchmark),用来对照底层导航能力(规划路线图见 `docs/PHASE_B_DESIGN.md`)。
- **MCAP / rosbag2**:ROS 2 的录包格式,做真实 run 的审计地面真值。

---

**诚实边界(贯穿全项目)**:主 demo 与 90 条预注册评测是 **mock(仿真,无实机)**;Phase B/C 证明了同一编排可换到**真实 Nav2 软件栈**,但仍是 `nav2_loopback_sim`,**没有物理机器人**。battery/sensor 类故障是 mock 层注入,如实标注 mock-only。
