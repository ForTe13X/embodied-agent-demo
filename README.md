# Embodied Agent Task Planner (simulation demo)

[中文](#中文) | [English](#english)

## 中文

一个具身机器人 Agent 编排层仿真 demo。大模型只负责意图解析；导航、恢复、安全检查和评测都由确定性代码处理。

这个仓库关注机器人式 agent 周围的控制层：任务规划、工具门禁、故障恢复、回放和可复现评测。当前版本没有实机验证，也没有接入真实 Nav2；运行后端是 mock navigation server。未来接入真实底盘所需的 `RobotAdapter` 边界见 [docs/ADAPTER_CONTRACT.md](docs/ADAPTER_CONTRACT.md)。

### 包含内容

| 内容 | 位置 |
|---|---|
| 约 4 分钟演示录屏，含中英字幕 | [docs/recording/demo.mp4](docs/recording/demo.mp4) + [demo.srt](docs/recording/demo.srt) |
| Godot 4 POV 渲染管线，由地面真值轨迹驱动 | [povgen/](povgen/) + [scripts/export_traj.py](scripts/export_traj.py) |
| 本地 VLM 巡检帧标注实验与局限记录 | [scripts/vlm_annotate.py](scripts/vlm_annotate.py) -> [标注帧](docs/screenshots/vlm_live_annotated.png) |
| 同步回放 viewer 与 POV 面板 | [viewer/pov/](viewer/pov/) |
| 工具 API、错误码、时序与预期输出 | [docs/API.md](docs/API.md) |
| 带截图的用户手册 | [docs/USER_MANUAL.md](docs/USER_MANUAL.md) |
| 测试矩阵与手动检查 | [docs/TESTCASES.md](docs/TESTCASES.md) |
| 产品范围与路线图 | [docs/PRODUCT.md](docs/PRODUCT.md) |
| 评测协议、预注册与结果 | [EVAL_PREREG.md](EVAL_PREREG.md), [prereg.yaml](prereg.yaml), [RESULTS.md](RESULTS.md), [REVIEW.md](REVIEW.md) |

媒体生成脚本使用系统 Python：

```powershell
python scripts\capture_viewer.py
python scripts\capture_terminal.py
python scripts\recording\build_video.py --all
```

它们需要 Playwright、edge-tts、Pillow，以及 `PATH` 中的 `ffmpeg`。项目 venv 保持精简，只放运行和测试需要的依赖。

### 快速开始

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
$env:PYTHONUTF8 = 1

.\.venv\Scripts\python -m pytest tests -q
.\.venv\Scripts\python run_demo.py --scenario blocked
.\.venv\Scripts\python run_demo.py --scenario restricted --interactive
.\.venv\Scripts\python run_demo.py --nl "去A区巡检a1和a3" --llm
.\.venv\Scripts\python run_eval.py
.\.venv\Scripts\python viewer\serve.py
.\.venv\Scripts\python -m embodied_agent.replay runs\nav_blocked\seed_0.jsonl
```

viewer 默认运行在 `http://127.0.0.1:8777`。

### 架构

```text
自然语言 -> 意图解析 -> LangGraph 编排
                         planner -> executor <-> replanner
                                      |
                                      v
                                  observer
                                      |
                         故障信号 -> exception manager
                                      |
                                      v
                                  reporter

所有工具调用都经过 Tool Registry：
typed schema、白名单、幂等重试、熔断、HITL 审批 token、
电量闸和访问级检查。

Tool Registry -> RobotAdapter -> MockNavServer -> World
World = 拓扑、电量、传感器、故障注入、虚拟时钟

append-only event log 贯穿所有层并支持回放。
短期记忆在图状态里；每个 run 内记录受阻边和不可达点，run 结束后重置。
```

关键实现取舍见 [REVIEW.md](REVIEW.md)。当前图将 `exception_manager` 独立出来，使恢复策略与 replanning 保持隔离。工具层暴露小而 typed 的接口，包括导航反馈与 findings 报告，不让 planner 直接访问底盘内部状态。

### 故障检测与恢复

mock navigation server 不返回人为设计的 `blocked` 终态。路线受阻时，它只表现为 velocity/progress 停滞；observer 通过水位检测识别，再通过异步 goal-handle 契约取消、重试或重规划。这样故障检测留在编排层，而不是写进模拟器捷径。

| 故障 | 注入方式 | 检测信号 | 恢复链 |
|---|---|---|---|
| 路线受阻 | 在途边 tick 4-16 左右阻断 | feedback 停滞至少 6 tick | retry 一次 -> 避障重规划 -> 替代点 -> HITL |
| 点位不可达 | 隔离目标点 | `result=unreachable` | 闭集候选中选替代点 -> 降级报告 |
| 传感器异常 | `sensor_health=false` | perception error | 跳过/降级 -> 暂停并 HITL |
| 低电量 | 初始电量偏低且消耗加快 | 每 tick 电量水位 | 快照队列 -> 回坞充电 -> 继续队列 |
| 工具失败 | perception 前注入 timeout 或 malformed response | 校验失败或超时 | 幂等调用重试 -> 熔断 -> 降级失败报告 |
| 路线受阻 + 低电量 | 同时注入 | 同上 | 安全恢复优先于任务恢复 |

恢复策略是确定性的：先分类故障，再查表执行。可选 LLM 模式下，模型最多从闭集候选中选择一个 index，不能发明新的恢复动作。

### Tool Registry 规则

| 规则 | 行为 |
|---|---|
| 白名单 | 未知工具名被拒绝为 `UNKNOWN_TOOL` 并记日志 |
| typed schema | Pydantic 校验 `extra='forbid'`；未知参数变成 `SCHEMA_VIOLATION` |
| 重试策略 | 幂等读/感知类调用可重试一次；导航、报告与 HITL 不自动重试 |
| 熔断 | 连续 3 次失败后打开该工具熔断 |
| 高风险动作 | 受限区与低电量继续需要 scoped、single-use、time-limited HITL token |
| forbidden zone | token 也不能放行 forbidden target |
| 运动权限 | adapter 不暴露速度或力矩接口，planner 不能绕开导航契约 |
| 约束来源 | 电量阈值与访问级来自静态配置；解析出的意图只能收紧权限 |

### 评测

评测协议在 [EVAL_PREREG.md](EVAL_PREREG.md) 中预注册。机器可读预测放在 [prereg.yaml](prereg.yaml)，`run_eval.py` 会在运行前检查。指标从 append-only event log 生成，不读取 agent 内部记忆。

当前矩阵：

- 9 个条件 x 10 个 seed = 90 run。
- 条件包括 baseline、5 个单故障、1 个复合故障、开启门禁的对抗 planner stub，以及关闭门禁的消融。
- 安全违规由 registry 下方的 ground-truth monitor 记录，不由 agent 自报。
- 结果拆分检测、恢复、终态和 HITL 升级。
- seed 只控制 mock world；虚拟时钟保证运行快速且可复现。

### 目录

```text
embodied_agent/
  clock.py world.py events.py safety.py faults.py
  mock_server.py adapter.py
  registry.py hitl.py
  intent.py llm_intent.py planner_rules.py
  recovery.py memory.py graph.py runtime.py
  replay.py
  evaluation/
faults.yaml
prereg.yaml
EVAL_PREREG.md
tests/
run_demo.py
run_eval.py
docs/
```

### 边界

- 当前是 mock-only。battery、sensor 和 tool failure 注入是模拟器能力；导航 adapter 契约是最先计划迁移到真实底盘的部分。
- 在路线受阻与低电量叠加时，如果回坞路线也被拖住，仍可能耗尽电量。当前策略暴露这个风险，不把它隐藏成成功。
- route memory 在单个 run 内把受阻边当成持续不可用，因此临时障碍可能导致保守降级。
- 一次 navigation call 只携带一个审批 token。若目标同时需要受限区与低电量审批，当前策略保持阻断。
- 故障优先级由 observer 检查顺序实现；优先级表是审计记录，不是运行时策略表。
- 熔断在单个 run 内没有 half-open 恢复。
- 跨 run 长期记忆不属于当前注册评测。

## English

A simulation demo for an embodied-agent orchestration layer. The language model is limited to intent parsing; navigation, recovery, safety checks, and evaluation are handled by deterministic code.

This repository focuses on the control layer around a robot-like agent: task planning, tool gating, fault recovery, replay, and repeatable evaluation. It does not claim real-robot validation. The current backend is a mock navigation server, with a `RobotAdapter` boundary prepared for future Nav2/rclpy integration. See [docs/ADAPTER_CONTRACT.md](docs/ADAPTER_CONTRACT.md).

### Included Artifacts

| Artifact | Location |
|---|---|
| Demo recording, about 4 minutes, with bilingual subtitles | [docs/recording/demo.mp4](docs/recording/demo.mp4) + [demo.srt](docs/recording/demo.srt) |
| Godot 4 POV rendering pipeline driven by ground-truth trajectories | [povgen/](povgen/) + [scripts/export_traj.py](scripts/export_traj.py) |
| Local VLM frame annotation experiment and limitation notes | [scripts/vlm_annotate.py](scripts/vlm_annotate.py) -> [annotated frame](docs/screenshots/vlm_live_annotated.png) |
| Synchronized replay viewer with POV panels | [viewer/pov/](viewer/pov/) |
| Tool API, errors, sequences, and expected outputs | [docs/API.md](docs/API.md) |
| User manual with screenshots | [docs/USER_MANUAL.md](docs/USER_MANUAL.md) |
| Test matrix and manual checks | [docs/TESTCASES.md](docs/TESTCASES.md) |
| Product scope and roadmap | [docs/PRODUCT.md](docs/PRODUCT.md) |
| Evaluation protocol, predictions, and reports | [EVAL_PREREG.md](EVAL_PREREG.md), [prereg.yaml](prereg.yaml), [RESULTS.md](RESULTS.md), [REVIEW.md](REVIEW.md) |

### Quick Start

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
$env:PYTHONUTF8 = 1

.\.venv\Scripts\python -m pytest tests -q
.\.venv\Scripts\python run_demo.py --scenario blocked
.\.venv\Scripts\python run_demo.py --scenario restricted --interactive
.\.venv\Scripts\python run_demo.py --nl "去A区巡检a1和a3" --llm
.\.venv\Scripts\python run_eval.py
.\.venv\Scripts\python viewer\serve.py
.\.venv\Scripts\python -m embodied_agent.replay runs\nav_blocked\seed_0.jsonl
```

The viewer runs at `http://127.0.0.1:8777`.

### Architecture

```text
Natural language -> Intent parser -> LangGraph orchestration
                                      planner -> executor <-> replanner
                                                   |
                                                   v
                                               observer
                                                   |
                                  fault signal -> exception manager
                                                   |
                                                   v
                                               reporter

Every tool call passes through Tool Registry:
typed schema, allowlist, idempotent retry policy, circuit breaker,
HITL approval tokens, battery guard, and access-level checks.

Tool Registry -> RobotAdapter -> MockNavServer -> World
World = topology, battery, sensors, injected faults, virtual clock

Append-only event logs connect all layers and support replay.
Short-term memory lives in graph state; per-run route memory records blocked
edges and unreachable points, then resets on the next run.
```

### Fault Detection And Recovery

The mock navigation server does not return a synthetic `blocked` status. When movement stalls, feedback simply stops making progress. The observer detects this from velocity/progress watermarks, then cancels or replans through the same asynchronous goal-handle contract a real adapter would use.

| Fault | Injection | Detection | Recovery chain |
|---|---|---|---|
| Blocked route | Edge blocked around tick 4-16 | Feedback stagnation for at least 6 ticks | Retry once -> detour replan -> alternative point -> HITL |
| Unreachable point | Isolated target | `result=unreachable` | Alternative point from a closed candidate set -> degraded report |
| Sensor fault | `sensor_health=false` | Perception error | Skip/degrade -> pause + HITL |
| Low battery | Low initial charge with faster drain | Battery watermark on every tick | Snapshot queue -> dock and charge -> continue queued work |
| Tool failure | Timeout or malformed response before perception | Validation failure or timeout | Retry idempotent calls -> circuit break -> degraded failure report |
| Combined blocked route + low battery | Simultaneous faults | Same signals as above | Safety recovery preempts task recovery |

Recovery is deterministic: classify the fault, look up the chain, and choose only from enumerated candidates. In optional LLM mode, the model may choose an index from that closed set; it does not invent recovery actions.

### Tool Registry Rules

| Rule | Behavior |
|---|---|
| Allowlist | Unknown tool names are rejected as `UNKNOWN_TOOL` and logged |
| Typed schema | Pydantic validation with `extra='forbid'`; unknown arguments become `SCHEMA_VIOLATION` |
| Retry policy | Idempotent read/perception calls may retry once; navigation, reports, and HITL actions do not auto-retry |
| Circuit breaker | Three consecutive failures open the breaker for that tool |
| High-risk actions | Restricted zones and low-battery continuation require scoped, single-use, time-limited HITL tokens |
| Forbidden zones | Approval tokens cannot override a forbidden target |
| Motion authority | The adapter exposes no speed or torque commands; the planner cannot bypass the navigation contract |
| Constraint source | Battery thresholds and access levels come from static config; parsed intent can only narrow permissions |

### Evaluation

The evaluation protocol is pre-registered in [EVAL_PREREG.md](EVAL_PREREG.md). Machine-readable predictions live in [prereg.yaml](prereg.yaml) and are checked before `run_eval.py` runs. Metrics are generated from append-only event logs, not from agent memory.

Current matrix:

- 9 conditions x 10 seeds = 90 runs.
- Conditions include baseline, five single faults, one combined fault, an adversarial planner stub with the gate enabled, and an ablation with the gate disabled.
- Safety violations are recorded by a ground-truth monitor under the tool registry, so they are not self-reported by the agent.
- Results separate detection, recovery, terminal state, and escalation to HITL.
- Seeds control only the mock world; the virtual clock keeps runs reproducible and fast.

### Scope

- The project is mock-only. Battery, sensor, and tool-failure injection are simulator features.
- Combined blocked-route and low-battery cases can still drain the battery if the return path is blocked long enough.
- Route memory treats blocked edges as blocked for the rest of the run.
- A single navigation call carries one approval token, so combined approval cases stay conservative.
- Fault priority is implemented by observer check order.
- Circuit breakers do not have half-open recovery inside a run.
- Cross-run long-term memory is not part of the registered evaluation.
