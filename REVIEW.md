# PLAN.md 评审记录(实施前)

> 评审方式:4 个独立视角(架构 / 评测方法学 / 可行性 / LLM·安全·确定性)并行评审 + 汇总。
> 结论:**proceed-with-changes(4/4 一致)** —— 方向正确,但有 3 个设计级问题必须在写代码前修掉,否则核心卖点(评测优先)站不住。

## 一、优点(保留不动)

1. **mock-first 排序正确**:Phase A 自成完整 demo,Phase B(WSL2/Nav2)显式允许止损,关键路径不依赖最大风险项。
2. **adapter 切面选在 action 语义层**(goal/feedback/result/cancel),而不是"一个会导航的函数"——这是 mock→rclpy 可换的真正前提,Phase B 本身就是抽象隔离的活演示。
3. **LLM 能力边界清晰可辩护**:白名单制、perceive 只返回观测不返回动作、LLM 永远拿不到速度/力矩接口——面试最强的一条主线。
4. **异常表的三元组形态正确**(注入方式×检测信号×恢复链),正是预注册评测需要的 schema;多数 demo 只有恢复那一半。
5. **诚实报告姿态预先承诺**(未恢复 case 原样报 + event log 片段 + "仿真 demo 无实机"边界声明)。
6. **基线无故障对照组**没漏——很多 agent demo 连这个都没有。

## 二、问题(按严重度)

### Blocker(不修,核心卖点不成立)

| # | 问题 | 修法(已采纳) |
|---|---|---|
| B1 | `navigate_to()` 若是阻塞式单调用,与恢复表 3/5 行矛盾:feedback 停滞检测、`cancel_navigation`、低电量中途打断都无法发生;且 rclpy ActionClient 是异步句柄式,阻塞接口换不过去 | adapter 改为**异步 goal-handle 契约**:`send_goal → goal_id`,`feedback(goal_id)`,`cancel(goal_id)`,`result(goal_id)`;executor 发起目标后由 observer 循环 tick 监视(停滞/电量水位可中途触发 cancel) |
| B2 | LLM 在 60 次评测循环里 ⇒ "确定性 seed"不可兑现(temperature=0 也不可复现;网速/费用/面试现场 Wi-Fi 全是风险);且 seed 到底控制什么从未定义 | **评测默认零 LLM 调用**:规则式 planner 在"引擎枚举合法候选、只选 index"的闭集上决策(PLAN §6 自己已有此模式,拿来用于自身);LLM 是 live-demo 可选层(record/replay)。**seed 只控制 mock 世界随机性**:故障触发时机/位置、电量衰减噪声、perceive 置信度抖动、导航耗时抖动 |
| B3 | 墙钟计时("N 秒后转 blocked"、真实 sleep)⇒ 同 seed 不同 trace,评测既慢又 flaky,"可回放"承诺反成负债 | **虚拟时钟(tick 制)**:全部超时/衰减/故障触发按 tick 定义;eval 模式瞬时推进(60 run 秒级跑完,可现场重跑),demo 模式 tick 映射真实 sleep |

### Major(显著削弱 demo 或造成返工)

| # | 问题 | 修法(已采纳) |
|---|---|---|
| M1 | 受限区规则自相矛盾:注册表"非 allowed 一律拒" vs "进受限区须 HITL 确认"——照写 HITL 是死代码,**安全违规指标恒为 0(空指标)** | 节点访问级改三态 `free/restricted/forbidden`;`ask_human_confirmation → {approved, approval_token}`,受限区导航需注册表校验一次性 token(planner 不能自证);mock server 内置**地面真值安全监视器**(在注册表之下独立记录每次进入受限区及 token 有效性),违规指标由它出数,不由 agent 自报 |
| M2 | 安全指标恒 0 还需要"被挑战"才有意义 | 新增两个预注册评测条件:**对抗条件**(恶意 planner stub 发未知工具/图外节点/forbidden 节点/无 token 进受限区/低电量强行继续,断言 100% 拦截、违规=0)+ **门禁关闭消融**(同种子关掉白名单/token 校验,预测违规>0)。两者对照 = "安全来自确定性层而非 LLM"的因果证据 |
| M3 | Exception Manager 与 replanner 职责不清(恢复若走 LLM 就既不确定也不可预注册) | 明确:Exception Manager = **确定性故障分类器 + 策略表查表**(observer→replanner 边上的路由函数);replanner 只在闭集候选(拓扑邻居等)里按确定性规则选(LLM 模式下也只选 index);逐故障注明是否咨询 LLM(5 类里基本都不需要) |
| M4 | 低电量行"恢复队列"无支撑原语:无暂停/队列持久化、dock 无充电模型(回坞后电量仍 <20% 会死循环) | 待办队列作为图状态命名通道,低电量处理前快照;mock 增加 dock 充电动力学;resume = 携快照队列重入 planner;评测断言**恢复后完成的是原队列剩余步骤** |
| M5 | HITL 在无人值守评测里未定义(自动同意→虚高,自动拒绝→压低),且三条恢复链终点都是 HITL | **脚本化 HITL 策略**随每个评测条件预注册(消息模式→approve/deny/timeout,超时=deny+安全停);指标增加第三类终态 **escalated(升级人工)**,与成功/失败并列 |
| M6 | 指标定义缺失:链式恢复算谁的?低电量正确弃任务被记"未完成"?分母是注入数还是检出数? | 预注册**四分类终态**:autonomous-recovered / degraded-complete / safe-escalation / unsafe-failure;完成率对照**逐故障预期终态**评分;恢复率拆成 检出率 × 检出后恢复率;记录每次由链上第几级化解;一律报 x/N 不报裸百分比 |
| M7 | "预注册"无执行机制(无 git 仓库、README 又排在跑分之后 3 天) | `git init`;`faults.yaml`(声明式故障注入清单)+ `EVAL_PREREG.md`(指标定义/逐故障预测区间/seed 列表/重跑政策)在**第一次跑评测之前**单独 commit,结果表头引用该 commit hash |
| M8 | 场景提示词泄露恢复策略("路被挡就绕 B 区"),无法归因是策略表起效还是 LLM 照抄提示 | 确定性 harness 下规则 planner 不读提示文本,归因由构造保证并在 README 说明;LLM 模式下保留 有提示/无提示 A/B 作为扩展 |
| M9 | 时延/步数不可测:LLM API 抖动主导、"步"未定义、超时 run 污染均值 | 时延用 sim-tick 计;步 = 一次工具调用(observer 的 feedback 轮询单列不计入);报中位数+极差;超时 run 单独计数不进均值 |
| M10 | 本机已证实 GBK 编码陷阱(stdout=gbk, cp936),event log/中文表格/录屏必炸 | 所有 `open()` 强制 `encoding='utf-8'`;入口设 `PYTHONUTF8=1`;JSONL 用 utf-8 |
| M11 | 全局 Python 的 langgraph 1.2.0 与 langchain-core 0.2.43 不配套,**import 已实测炸** | 项目 `.venv` 隔离(已建好:langgraph 1.2.7 + langchain-core 1.4.8 冒烟通过),`requirements.txt` 锁版本 |

### Minor(顺手修)

- 场景要"拍照上报"但无上报工具 → 新增 `report_finding(image_id, label, node_id)`(第 9 个工具,给评测一个可断言的成功判据);
- "暂停"原语未定义 → 定义为:cancel 在飞目标 + 图停在等待态直到 HITL 应答;
- event log 需要关联 ID 才可回放 → schema 定为 `{tick, run_id, seed, condition, goal_id, actor, event_type, payload}`,指标由**独立脚本只读日志**计算(不读 agent 内存,回答"数字不是自评的");
- 复合故障(受阻+低电量同时)是必问题 → 预注册 1 个复合 case,预测电量/安全优先抢占;
- 安全约束来源规则:受限区/电量红线/白名单是静态配置,LLM 解析的意图约束只能**单调收紧**;
- mock server 用**进程内 asyncio 对象**而非 socket 服务(避免 Windows 防火墙弹窗、便于注入时钟和 seed);
- 长期记忆每 run 重置(否则跨 run 污染,seed 不独立);
- 图节点为 6 个(exception_manager 独立成节点),与 PLAN"五节点"差一,如实标注。

## 三、结论

**通过,按上表修订后实施。** Phase A 全部落地;Phase B(WSL2/Nav2)不在本次实施范围——按 M-系修法把 adapter 契约写成半页规范 + rclpy 适配器桩,留待第 4 天人工执行(止损线也预先写死:day 4 13:00 容器内 `ros2 action list` 看不到 `/navigate_to_pose` 即放弃,禁止碰 GPU/渲染调试)。
