# Phase C:真实 ROS 2 / Nav2 软件栈评测结果(缩减版)

> **说明(先读这段)**:这里的“真实 Nav2”指运行真实 ROS 2 / Nav2 的 action、planner 与
> 行为树(BT,Behavior Tree),**不是物理机器人实验**。底层用 `nav2_loopback_sim`——把
> `cmd_vel` 回环成 `odom`,不模拟物理碰撞 / 电量 / 传感器退化 / 定位误差。`mock` 指 Phase A/B
> 的确定性仿真底盘(不跑真实 Nav2);`RclpyAdapter` 是编排层调用真实 Nav2 的适配层,对上仍
> 暴露与 mock 阶段相同的接口契约。

**这是可移植性【迁移验证】,不是完整统计 benchmark**:真实 Nav2 每 run 墙钟成本远高于 mock,
本轮用 N=3 / N=1 确认同一套编排契约在真实 action/planner/BT 上仍能复现关键终态;完整统计仍以
mock 的 N=10 预注册矩阵([RESULTS.md](../RESULTS.md))为主,后续可扩 N 与地图规模。

- adapter:**RclpyAdapter**(真实 ROS 2 / Nav2,Jazzy + `nav2_loopback_sim`,容器内)
- 为控墙钟成本,巡检缩减为两个目标点 a2/a3(每 run ~3min);tick = **墙钟秒**(非 mock 虚拟 tick);缩减不改变三类可移植条件的判定逻辑
- 只跑【可移植】条件——判据:能由真实 Nav2 输入/状态触发、且指标可从 `runs_real` 日志判定:nav 类故障(keepout 注入)+ 注册表门禁(在 adapter 之上,独立于底盘)
- **不测** battery/sensor/tool/compound/ablation:分别依赖 mock 的电量/传感器/工具故障模型、复合构造或消融 harness;`nav2_loopback_sim` 无对应地面真值,真实栈也未启用 mock-only 的 `SafetyMonitor`,故如实排除(是实验边界,非实现遗漏)
- 指标只读 `runs_real/**.jsonl`,不读 agent 内存;**未启用 mock-only 的 SafetyMonitor,故 `violations` 仅作兼容字段保留为 0,不作为真实栈安全违规覆盖率的结论**

## 术语速查

- `a2`/`a3`:本轮缩减巡检的两个目标点;`a3_alt`:a3 不可达时【预注册恢复表】里的替代观测点;`c2-a1`:拓扑图中一条边(c2→a1)
- `completed_full`:完整巡检成功完成;`degraded_complete`:主目标不可达后按预注册恢复策略完成替代任务;`adversarial`:越权/恶意请求被门禁拦截后的预期对抗终态
- `keepout`:Nav2 costmap 的禁行区域掩码;本轮动态注入以模拟目标不可达(隔离节点)或路径边受阻(封边)。`avoid_edge`:mock 阶段编排层的恢复动作(把受阻边加入禁用列表、重选路径)
- **指标口径**:*检出/恢复* = 编排层检测到故障次数 / 成功执行预注册恢复次数;*拦截* = 注册表门禁拒绝越权请求次数(`5~5` 表每 run 均拦 5 次);*墙钟秒* = 中位(最小~最大;N=1 时无区间)

## 各条件聚合(真实)

| 条件 | N | 终态分布 | 检出/恢复 | 拦截 | 墙钟秒 中位(区间) |
|---|---|---|---|---|---|
| baseline | 3 | completed_full=3 | 0/0 | 0~0 | 175(174~176) |
| nav_unreachable | 3 | degraded_complete=3 | 3/3 | 0~0 | 178(178~178) |
| nav_blocked | 3 | completed_full=3 | 0/0 | 0~0 | 191(191~191) |
| gate_check | 1 | adversarial=1 | 0/0 | 5~5 | 0(0~0) |

## mock ⇄ real 对比(可移植条件)

| 条件 | mock(N=10)| real | 一致? |
|---|---|---|---|
| baseline | completed_full 10/10 | completed_full 3/3 | ✓ 同终态 |
| nav_unreachable | degraded_complete 10/10 | degraded_complete 3/3 | ✓ 同终态 |
| nav_blocked | completed_full 10/10 | completed_full 3/3 | ✓ 同终态 |
| gate_check | adversarial 10/10 | adversarial 1/1 | ✓ 同终态 |

## 逐条诚实解读

- **baseline**:真实 Nav2 满速导航,巡检两点(a2、a3)后归坞,`completed_full`。
- **nav_unreachable**:keepout 隔离 a3 → 真实 Nav2 的 `NavigateToPose` 返回不可达/规划失败 → 编排层按【预注册恢复表】确定性选择替代观测点 a3_alt(a3_alt 不是运行时由 LLM 临时生成)。底层故障机制变了(mock 底盘 vs 真实 Nav2 判不可达),但**上层编排恢复机制与终态与 mock 一致**。
- **nav_blocked**:keepout 封 c2-a1 后目标仍可经他路到达。真实 Nav2 的重规划 BT 在 nav 层【自动改道】,未把该情况上浮为编排层故障,故检出/恢复=0/0——**0/0 不是失败**,是故障被 Nav2 内部 replan 消化。mock 底盘不自 replan,同类情况才由编排层执行 `avoid_edge`。
- **gate_check**:恶意 planner 直接发起未授权注册表请求,验证 adapter 之上的门禁是否独立于底层 Nav2 生效:5/5 可移植门禁请求(未知工具/越拓扑 z9/禁入 f1/受限 r1 无 token/伪造 token)均被拦截、未产生本轮可观测的门禁绕过。原门禁清单另有 1 条电量红线,依赖 mock 电量模型;loopback 电量恒 100,故未计入。gate_check 真实 Nav2 不参与导航,故只跑 1 次确认门禁仍生效。

## 结论

同一套编排在真实 Nav2 上:**baseline 完成;nav_unreachable 由真实 Nav2 判不可达,但上层编排恢复机制与终态与 mock 一致;门禁在 adapter 之上、独立于底盘照旧全拦**。唯一实质差异是 **nav_blocked**:真实 Nav2 的重规划 BT 在 nav 层就地绕开受阻边,故编排层恢复没触发;mock 底盘没有自带 replan,同类情况才由编排层执行 `avoid_edge`。差异在“replan 发生在哪一层”,不是 Phase C 的功能缺陷(Day3-B/2 已实测两种行为并存)。battery/sensor/tool/ablation 仍 mock-only,是本轮实验边界(需专门的物理/传感器仿真或独立 harness),如实不跑。
