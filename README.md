# Embodied Agent Task Planner(仿真 demo)

> LLM 只做高层意图,**确定性 Tool + 状态机 + 异常恢复兜底**的具身 Agent 编排层。
> 差异化 = **评测优先**:预注册故障注入 × 10 seed × 指标表,未恢复 case 原样报。
>
> **诚实边界声明:这是 mock adapter 上的仿真 demo,无实机、无真实 Nav2。**
> 结果表只代表 mock 世界;Phase B(rclpy/Nav2)的接口契约已写好但未执行,见
> [docs/ADAPTER_CONTRACT.md](docs/ADAPTER_CONTRACT.md)。

## 快速开始

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
$env:PYTHONUTF8 = 1                                  # Windows GBK 陷阱防线

.\.venv\Scripts\python -m pytest tests -q            # 25 个测试,约 1 秒
.\.venv\Scripts\python run_demo.py --scenario blocked      # 观感演示(真实节奏)
.\.venv\Scripts\python run_demo.py --scenario restricted --interactive  # HITL 审批
.\.venv\Scripts\python run_eval.py                   # 90 run 全矩阵 → RESULTS.md(秒级)
.\.venv\Scripts\python -m embodied_agent.replay runs\nav_blocked\seed_0.jsonl  # 回放
```

## 架构(面试第一张图)

```
自然语言 ──▶ Intent 解析 ──▶ ┌────────────── LangGraph 编排图 ──────────────┐
   (评测=fixture;demo=规则; │  planner ─▶ executor ◀─────────── replanner  │
    LLM 可选,失败回退规则)  │               │  ▲                    ▲      │
                             │               ▼  │(下一步)            │      │
                             │            observer ──(故障)─▶ exception_mgr │
                             │           (tick 循环+水位检测)  (确定性查表) │
                             │               │全部完成                      │
                             │               ▼                              │
                             └────────── reporter ──────────────────────────┘
                                             │ 所有工具调用
                                             ▼
      ┌──────────── Tool Registry(唯一通道)────────────┐
      │ typed schema(extra=forbid)· 白名单 · 幂等重试   │◀── ask_human_confirmation
      │ 熔断 · 审批 token(一次一用/限 scope/限时效)     │      (HITL 唯一高危通道)
      │ 电量红线闸(静态配置,LLM 只能收紧)              │
      └──────────────────┬───────────────────────────────┘
                         ▼
      RobotAdapter(异步 goal-handle 契约,mock ⇄ rclpy 可换)
                         ▼
      MockNavServer(NavigateToPose 语义)+ 地面真值 SafetyMonitor
                         ▼
      World(拓扑图 · 电量 · 传感器 · 故障注入 · 虚拟时钟)

 旁路:Event Log(append-only,每个决策/调用/恢复,可回放)贯穿所有层
 记忆:短期=图状态通道;run 内长期=受阻边/不可达点(每 run 重置)
```

与原 PLAN 的差异均有评审记录(见 [REVIEW.md](REVIEW.md)):六节点而非五节点
(exception_manager 独立——恢复决策必须与未来可能接 LLM 的 replanner 隔离)、
工具 10 个而非 8 个(新增 `get_nav_feedback`、`report_finding`)、受限区三态访问级 +
审批 token、虚拟时钟、地面真值安全监视器。

## 为什么受阻检测在编排层而不是 server 自报?

mock server 被阻断时**只表现为 feedback 停滞**(velocity=0、progress 不动),绝不返回
"blocked" 终态——真实 Nav2 也是这样。受阻由 observer 的停滞水位检测发现,才谈得上
"编排层的异常恢复能力";server 直接自报等于评测作弊(REVIEW.md B1)。这是 goal-handle
异步契约买来的能力:导航在飞时 observer 每 tick 轮询 feedback,停滞/低电量水位都能
**中途**触发 cancel。

## Tool Registry 规则(面试必问)

| 规则 | 实现 |
|---|---|
| 白名单制 | 未知工具名 → `UNKNOWN_TOOL` 拒绝并记日志 |
| typed schema | pydantic `extra='forbid'`,未知超参 → `SCHEMA_VIOLATION` |
| 幂等才重试 | get_*/perceive 等自动重试 1 次;navigate/report/HITL 绝不自动重试 |
| 熔断 | 连续失败 ≥3 → 熔断该工具;门禁拒绝不计入(那是调用方错误) |
| 高危动作 | 受限区/低电量继续 → 必须 HITL 审批 token:一次一用、限 scope、限时效,由注册表铸造核销,planner 不能自证 |
| forbidden | token 也不放行 |
| 速度/力矩 | adapter 上根本不存在这类接口——结构性保证,不是提示词约定 |
| 约束来源 | 电量红线/访问级是静态配置;LLM 解析的意图只能收紧,不能放宽 |

## 异常恢复表(预注册,面试第二张图)

| 故障 | 注入(faults.yaml,seed 采样) | 检测信号 | 恢复链(确定性查表) |
|---|---|---|---|
| 导航受阻 | 在途边 tick 4~16 阻断 | feedback 停滞水位(≥6 tick) | retry ×1 → 避障重规划 → 替代点 → HITL |
| 点位不可达 | a3 隔离 | result=unreachable | 替代点(枚举闭集,选择器挑 index)→ 降级报告 |
| 传感器异常 | tick 2~8 起 sensor_health=false | perceive 错误 | 跳步降级 → 暂停+HITL(降级继续?) |
| 低电量 | 初始 24~30%、衰减 ×2 | 每 tick 电量水位(在飞可抢占) | 队列快照 → 回坞充电 → 断点续跑原队列 |
| 工具失败 | perceive 前 k∈{2,4} 次注入 timeout/malformed | 校验失败/超时 | 幂等重试 → 熔断 → 降级+失败报告 |
| **复合**(受阻+低电量) | 同时注入 | 同上 | **安全类抢占任务类**(优先级裁决,预注册) |

链尾升级 HITL 是显式终态(不冒充成功也不算失败,指标单列)。恢复决策 =
确定性分类器 + 查表;需要"选"的地方由引擎枚举闭集候选、选择器只挑 index
(LLM 模式下也一样——这正是评测环路可以 0 次 LLM 调用的原因)。

## 评测(差异化核心)

协议见 [EVAL_PREREG.md](EVAL_PREREG.md),机器可校验预测见 [prereg.yaml](prereg.yaml)
(**先 commit 预测,后跑评测**,`run_eval.py` 会强制检查;结果表头引用 commit hash)。
结果:[RESULTS.md](RESULTS.md)(`metrics.py` 只读事件日志生成,不读 agent 内存)。

- 9 条件 × 10 seed = 90 run:基线 + 5 单故障 + 复合 + **对抗**(恶意 planner stub ×
  门禁开,预测 6/6 拦截、0 违规)+ **消融**(门禁关,预测每 run 恰好 5 真实违规)——
  对照证明安全违规指标是活的,"安全来自确定性层,不是 LLM 的自觉";
- 违规由 mock server 内的**地面真值监视器**记录(注册表之下),不是 agent 自报;
- 终态四分类 + 检出率与恢复分开报 + x/N 不报裸百分比;
- seed 只控制 mock 世界随机性;虚拟时钟让 90 run 秒级跑完、同 seed 逐字节可复现
  (确定性回归测试锁定)。

## 项目结构

```
embodied_agent/
├── clock.py world.py events.py safety.py faults.py   # 世界与地基
├── mock_server.py adapter.py                          # goal-handle 契约 + mock 底盘
├── registry.py hitl.py                                # 安全门禁层
├── intent.py llm_intent.py planner_rules.py           # 意图与确定性规划内核
├── recovery.py memory.py graph.py runtime.py          # 恢复表 + LangGraph 编排
├── replay.py                                          # 被动回放
└── evaluation/ (scenarios harness metrics)            # 评测 harness
faults.yaml prereg.yaml EVAL_PREREG.md                 # 预注册工件
tests/  run_demo.py  run_eval.py  docs/ADAPTER_CONTRACT.md
```

## 复审与修复记录

实现完成后跑了一轮多 agent 复审(4 维度并行找缺陷 → 每个发现由独立 agent 对抗验证,
只留证实项):**35 项证实 / 2 项被反驳**,含 1 个 critical(电量闸拒绝被误分类,
恢复链零 tick 死循环,复审 agent 实证复现)。全部 critical/major 已修复并配回归测试,
评测按重跑政策重跑(v2,原因与度量口径变化记录在 [EVAL_PREREG.md](EVAL_PREREG.md) 重跑记录)。

## 已知限制(诚实清单)

- **mock-only**:battery/sensor/tool 三类故障注入无法移植到真实 Nav2(nav 类可以,
  见 adapter 契约 §5);
- 复合故障存在预注册的真实致死面:受阻若砸在回坞链路上,重试烧掉的停滞时间 +
  绕行路程可能超过剩余电量 → battery_dead(unsafe_failure)。这是设计暴露的风险,
  不掩盖;改进方向(未实现):电量应急上下文里跳过 retry 直接重规划;
- replan 记住的受阻边**在 run 内永不遗忘**:临时障碍被当成永久障碍,割点边
  (如 dock–c1)被记入后整图不可达,任务只能降级(复审证实,保留为已知限制);
- 一次 navigate 只带一个审批 token:目标同时命中受限区+低电量双闸时无法双授权
  (token 不会被白烧,但也过不去)——按"更保守"接受;
- 故障优先级由 observer 检查顺序实现,PRIORITY 表是声明式审计记录不是运行时查表;
- 熔断无半开恢复(run 内熔断即到底);
- 跨 run 长期记忆是未预注册的扩展实验,当前每 run 重置。
