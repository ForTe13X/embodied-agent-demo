# Phase C:真实 Nav2 评测结果(缩减版)

- adapter: **RclpyAdapter(真实 ROS 2 Nav2,Jazzy + nav2_loopback_sim,容器内)**
- 巡检缩减为 a2/a3(墙钟成本;每 run ~3min);tick = **墙钟秒**(非 mock 虚拟 tick)
- 只跑【可移植到真实 Nav2】的条件:nav 类故障(keepout 注入)+ 注册表门禁(在 adapter 之上)
- **不测** battery/sensor/tool/compound/ablation —— loopback 无对应模型 / 无地面真值 SafetyMonitor,mock-only
- 指标只读 `runs_real/**.jsonl`,不读 agent 内存;violations 恒 0(真实栈无 SafetyMonitor 构造)

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

- **baseline**:真实 Nav2 满速导航,巡检两点后归坞。
- **nav_unreachable**:keepout 隔离 a3 → 真实 Nav2 判不可达 → 编排确定性替换 a3_alt(与 mock 同机制、同结果)。
- **nav_blocked**:keepout 封 c2-a1 → 真实 Nav2 的重规划 BT【自动改道】,编排层的恢复未被触发(检出=0)——诚实差异:重规划住在 nav 层,不像 mock 靠编排 avoid_edge。
- **gate_check**:恶意 planner 直打注册表:5/5 可移植门禁(未知工具/越拓扑/禁入 f1/受限 r1 无 token/伪造 token)全拦截、0 违规。第 6 条(电量红线)依赖 mock 电量模型,loopback 恒 100 不适用,故未计入。

## 结论

同一套编排在真实 Nav2 上:**baseline 完成、nav_unreachable 的确定性恢复与 mock 同机制同结果、门禁在 adapter 之上照旧全拦**。唯一实质差异是 **nav_blocked**:真实 Nav2 的重规划 BT 把受阻边在 nav 层就地改道解决了(编排恢复未触发),而 mock 的底盘不自 replan、靠编排 avoid_edge——这不是缺陷,是'重规划住在哪一层'的差别(Day3-B/2 已实测两种行为并存)。battery/sensor/tool/ablation 仍 mock-only,如实不跑。
