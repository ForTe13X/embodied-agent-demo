# Embodied Agent Task Planner — demo 脚手架方案

> 目标:7 天内做出一个**能在面试里讲 5 分钟并现场跑**的具身 Agent 编排层 demo。
> 定位(与简历叙事一致):**不是**底盘控制,而是「LLM 只做高层意图/规划,确定性 Tool + 状态机 + 异常恢复兜底」的编排层。
> 差异化 = **评测优先**(预注册故障注入 × 多 seed × 指标表,诚实报未恢复 case)——这是别人的 demo 没有的。

## 0. 关键决策:mock-first,Nav2 second

你的机器是 Windows 11(ROS2 要走 WSL2/Docker,而 WSL GPU 有旧坑——小镇有灵 docs §10.5 记录 RADV 不可用;Nav2 仿真 CPU 够但环境搭建时间不可控)。所以:

- **Phase A(第 1–2 天,必须完成)**:纯 Python `asyncio` 写一个 **mock ROS2 action server**,精确复刻 NavigateToPose 的语义(goal/feedback/result/cancel + 状态机 running/blocked/unreachable/succeeded + 可注入故障)。编排层在它之上跑通全闭环。**就算后面 Nav2 翻车,这个 demo 已经完整可讲**(如实标注 adapter 是 mock)。
- **Phase B(第 3–4 天,尽力)**:WSL2 + Docker 跑 TurtleBot3 + Nav2 仿真(headless,Foxglove/RViz 看轨迹),把 mock adapter 换成 `rclpy` 真 adapter。接口不变——这正是"Tool 抽象隔离底座"的活演示。

## 1. 架构(面试第一张图)

```
用户自然语言
  → Intent Parser(LLM,输出结构化意图:目标/约束/优先级/禁止动作)
  → Task Planner(LangGraph 状态图:planner → executor → observer → replanner → reporter)
  → Tool Registry(typed schema + 白名单 + 幂等 + timeout/retry/熔断)
  → ROS2 Adapter(mock ⇄ rclpy 可换,同一接口)
  → Nav2 / BT / Controller(底座,Agent 只调用不替代)
  → Observer(action feedback + 传感器状态 → 结构化 observation)
  → Exception Manager(故障分类 → 恢复策略表)
  → Memory(短期:任务状态/已试路径/失败原因;长期:点位别名/历史不可达区)
  → Event Log(每个决策/工具调用/恢复动作 append-only,可回放)   ← 小镇有灵 DNA
  → HITL 闸(高危动作人工确认)                                   ← SPI browser-agent DNA
```

## 2. Tool Registry(8 个,全部 typed schema)

```python
get_robot_state() -> {pose, battery_pct, nav_status, sensor_health}
get_topological_map() -> {nodes: [{id, name, neighbors, allowed}]}
navigate_to(node_id) -> {status: succeeded|blocked|unreachable|timeout, reason, eta_s}
cancel_navigation() -> {status}
perceive(query) -> {objects: [{label, confidence}], image_id}     # VLM mock:返回结构化 observation,绝不返回动作
capture_image() -> {image_id, ts}
return_to_dock() -> {status}
ask_human_confirmation(message) -> {approved: bool}               # 高危动作唯一通道
```

规则(每条都要在 README 写明,面试必问):
- 每个 Tool:输入/输出 JSON schema 校验 + 参数合法性(node_id 必须在拓扑图内且 `allowed`)+ timeout + 重试(幂等的才重试)+ 错误码;
- **白名单制**:LLM 只能调 registry 里的 Tool;未知工具名/超参 = 拒绝并记日志;
- 高危动作(进入受限区、低电量下继续任务)必须过 `ask_human_confirmation`;
- **LLM 永远拿不到速度/力矩接口**——高频控制属于底座。

## 3. 异常恢复表(预注册,面试第二张图)

| 故障 | 注入方式 | 检测信号 | 恢复链 |
|---|---|---|---|
| 导航受阻 | mock: N 秒后转 blocked | feedback 停滞/速度≈0/超时 | retry ×1 → replan 邻接点 → 换目标 → HITL |
| 点位不可达 | mock: 直接返回 unreachable | result=unreachable | 查拓扑邻居 → 替代点 → 报告降级 |
| 传感器异常 | perceive 返回 timeout/sensor_health=false | health 检查 | 降级执行(跳过感知步)→ 暂停 → HITL |
| 低电量 | battery 线性衰减 + 阈值 20% | 每步前置检查 | 中断当前任务 → return_to_dock → 恢复队列 |
| Tool 调用失败 | schema 抖动/超时注入 | 校验失败/timeout | 重试 → 熔断该工具 → 失败报告 |

## 4. 评测 harness(差异化核心)

- 场景:「去 A 区巡检,路被挡就绕 B 区;见异常物体拍照上报;电量 <20% 先回充。」×  基线无故障 + 上表 5 类故障注入;
- **N seed × 每类故障**(建议 N=10,确定性 seed);
- 指标:任务完成率 / 恢复成功率 / Tool 调用成功率 / 安全违规数(未经确认进入受限区=违规)/ 平均步数与时延;
- **预注册**预期结果(README 先写预测再跑),没恢复成功的 case **原样报**并附 event log 片段;
- 产出:一张 markdown 结果表 + 6 个测试 case + 故障注入日志 + 2 分钟录屏。

## 5. 修订版 7 天

| 天 | 交付 |
|---|---|
| 1 | mock action server + Tool Registry(schema/白名单/timeout)跑通 happy path |
| 2 | LangGraph 五节点状态图 + 异常表前 3 类 + event log |
| 3 | 剩余故障 + 评测 harness + N-seed 跑分表 |
| 4 | WSL2/Docker + TurtleBot3/Nav2 仿真,真 adapter 替换(翻车则止损,mock 版已完整) |
| 5 | VLM mock 接入(perceive→结构化 observation→planner)+ HITL 演示路径 |
| 6 | README(架构图/评测表/诚实边界声明「仿真 demo,无实机」)+ 录屏 |
| 7 | 面试材料:30s/2min 稿、两张图、异常表、反问清单(评测指标那条收尾) |

## 6. 面试挂钩(demo ↔ 你的真实战绩)

- Tool 白名单 + 双闸 + 回放 ⇒ 「SPI browser-agent 工作台我上线过同款:写操作必须 仿真闸+critic+人工确认,留痕回放」;
- 引擎枚举合法候选、LLM 只选 index、超时降级规则 ⇒ 「小镇有灵已实现,OpenGo(arXiv 2604.01708)同款设计」;
- 评测 harness ⇒ 「深度用过 GameCraft-Bench(Luo et al.)的 headless 验证范式,自建过 Godot 录制/评测流水线」——**注意:引用别人论文,不是自己的**;
- 33 条不变量 soak 回归 ⇒ 「机器人任务的安全不变量我会用同样方式锁:每条恢复都可溯源到触发事件」。
