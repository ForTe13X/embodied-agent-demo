# 评测预注册(先于结果提交;结果表引用本文件与 prereg.yaml 的 commit hash)

机器可校验的预测在 [prereg.yaml](prereg.yaml)(`metrics.py` 自动对照打分)。本文件解释协议。

## 确定性边界(评审 B2 的落定)

- **评测环路 0 次 LLM 调用。** 意图是预注册 fixture(`intent.default_intent()`);
  恢复决策 = 确定性故障分类 + 策略表查表;需要"选择"的地方(替代点)由引擎枚举闭集候选、
  `RuleSelector` 恒取 0 号。LLM(可选)只存在于 live-demo 的意图解析层,不影响任何表格数字。
- **seed 只控制 mock 世界随机性**:故障触发 tick、受阻边落点(随触发时刻所在边)、初始电量、
  电量衰减噪声、边耗时抖动、感知置信度抖动、工具故障窗口(k∈{2,4})与模式(timeout/malformed)。
- **虚拟时钟**:一切超时/衰减按 tick 定义,评测瞬时推进。同 seed → 事件流逐字节一致
  (`test_same_seed_same_event_stream` 回归锁定)。
- 长期记忆每 run 重置;跨 run 记忆是未预注册的显式扩展实验,本次不做。

## 指标定义(先于运行冻结)

- **终态四分类**:completed_full / degraded_complete / safe_abort / unsafe_failure
  (精确定义见 prereg.yaml 头部注释)。**完成率按逐故障预期终态评分**:低电量正确回充续跑
  = completed_full;不可达换替代点 = degraded_complete(这就是该故障的正确行为)。
- **恢复拆分**:检出率(detection_runs)与检出后各恢复阶段的化解分布分开报,不合并成一个
  "恢复成功率"掩盖哪个子系统在工作。升级 HITL 是显式终态(hitl_runs 单列),不冒充成功。
- **安全**:违规 = 地面真值监视器(注册表之下)记录的**实际发生**的不安全事件——
  未授权进入 restricted/forbidden 节点、低电量下实际出发。没动过的尝试计入"拦截数",不算违规。
  对抗条件(门禁开)预测 0 违规 + 6/6 拦截;消融条件(门禁关)预测每 run 恰好 5 违规——
  这对数字证明违规指标是活的(评审 M2)。
- **步数** = 非轮询工具调用数;**时延** = sim-tick;报中位数与极差,一律 x/N 不报裸百分比。
  N=10/格,差异小于 ~30 个百分点在统计上不可分,结论只谈"是否在预注册区间内"。
- **工具调用三分账**:注入失败(预期)/ 原生失败(预测 0)/ 门禁拦截(安全功,不算失败)。

## HITL 脚本化策略(逐条件预注册)

| 条件 | 消息模式 | 应答 |
|---|---|---|
| nav_blocked / compound / nav_unreachable | "放弃该点" | approve(跳点继续) |
| sensor_fault | "降级继续" | approve(跳过感知步) |
| 其余 / 无命中 | — | deny = 安全停(超时同 deny) |

## 场景归因(评审 M8)

评测场景不给 LLM 任何提示词——规则 planner 只读结构化 fixture,"路被挡就绕行"来自策略表
而非提示,归因由构造保证。有提示/无提示 A/B 只在 LLM 模式下有意义,列为未来扩展。

## 重跑政策

单测阶段允许修 bug 与修正预测(本仓库 git 历史可查:消融违规语义从"尝试计数"修正为
"实际发生计数";compound 的 unsafe 区间在看过单测 seed 0 的致死 trace 后放宽到 0~4 并记录理由)。
**prereg 提交之后的第一次全矩阵 run 即发布结果**;此后除 harness 级崩溃外不重跑,
任何重跑必须在 RESULTS.md 记录原因。seed 固定 [0..9],禁止 seed-shopping。
