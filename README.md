# Embodied Agent Task Planner (simulation demo)

[English](README_EN.md)

![演示节选:三视图指挥台绕行受阻边、POV 撞上箱堆、VLM 锁定异常、消融违规红闪](docs/recording/demo.gif)

*16 秒节选;完整 4 分钟配音版见 [docs/recording/demo.mp4](docs/recording/demo.mp4)。*

一个具身机器人 Agent 编排层仿真 demo。大模型只负责意图解析；导航、恢复、安全检查和评测都由确定性代码处理。

这个仓库关注机器人式 agent 周围的控制层：任务规划、工具门禁、故障恢复、回放和可复现评测。主 demo 与 90 条预注册评测跑在 mock navigation server 上（仿真，无实机）。`RobotAdapter` 边界见 [docs/ADAPTER_CONTRACT.md](docs/ADAPTER_CONTRACT.md)。

**Phase B（已跑通）**：换一个 adapter，把这套**同一个 LangGraph 编排图**接到**真实 ROS 2 Nav2**（Jazzy + nav2_loopback_sim，容器内），编排代码一行未改。故障用 keepout 代价地图滤镜注入，恢复由确定性内核查表——mock ⇄ 真实 Nav2 可换，是在真机栈上核对通过的事实。全过程实测与复现见 [phase_b/FINDINGS.md](phase_b/FINDINGS.md)。

![Day-4 真实集成 POV 节选：同一编排图驱动真实 Nav2，a3 被 keepout 判不可达 → 确定性恢复替换 a3_alt](docs/recording/day4_demo.gif)

*13 秒节选(真实 run 的故障恢复瞬间);完整 3 分钟中英配音版见 [docs/recording/day4_demo.mp4](docs/recording/day4_demo.mp4)。*

## 包含内容

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
| **Phase B**:真实 Nav2 集成 + 故障注入 + MCAP 审计(实测记录) | [phase_b/FINDINGS.md](phase_b/FINDINGS.md) |
| Phase B:RclpyAdapter(RobotAdapter 真实实现)+ real_runtime shim | [phase_b/rclpy_adapter.py](phase_b/rclpy_adapter.py), [phase_b/real_runtime.py](phase_b/real_runtime.py) |
| Phase B:同一 LangGraph 图驱动真实 Nav2 跑故障恢复任务 | [phase_b/run_real_mission.py](phase_b/run_real_mission.py) + [real_mission_events.jsonl](phase_b/real_mission_events.jsonl) |
| Phase B:Day-4 真实集成 POV 演示(3 分钟中英配音) | [docs/recording/day4_demo.mp4](docs/recording/day4_demo.mp4) |

媒体生成脚本使用系统 Python：

```powershell
python scripts\capture_viewer.py
python scripts\capture_terminal.py
python scripts\recording\build_video.py --all
```

它们需要 Playwright、edge-tts、Pillow，以及 `PATH` 中的 `ffmpeg`。项目 venv 保持精简，只放运行和测试需要的依赖。

## 快速开始

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

## 架构

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

## 故障检测与恢复

mock navigation server 不返回人为设计的 `blocked` 终态。路线受阻时，它只表现为 velocity/progress 停滞；observer 通过水位检测识别，再通过异步 goal-handle 契约取消、重试或重规划。这样故障检测留在编排层，而不是写进模拟器捷径。

| 故障 | 注入方式 | 检测信号 | 恢复链 |
|---|---|---|---|
| 路线受阻 | 在途边 tick 4-16 左右阻断 | feedback 停滞至少 6 tick | retry 一次 -> 避障重规划 -> 替代点 -> HITL |
| 点位不可达 | 隔离目标点 | `result=unreachable` | 闭集候选中选替代点 -> 降级报告 |
| 传感器异常 | `sensor_health=false` | perception error | 跳过/降级 -> 暂停并 HITL |
| 低电量 | 初始电量偏低且消耗加快 | 每 tick 电量水位 | 快照队列 -> 回坞充电 -> 继续队列 |
| 工具失败 | perceive 的前 k 次调用注入 timeout 或 malformed(k 按 seed 取 2 或 4) | 校验失败或超时 | 跳过该步降级 -> 失败报告并降级;注册表层会先幂等重试一次,持续失败则熔断 |
| 路线受阻 + 低电量 | 同时注入 | 同上 | 安全恢复优先于任务恢复 |

恢复策略是确定性的：先分类故障，再查表执行。选择器接口只允许从闭集候选中挑一个 index——即使未来接入 LLM 选择器，也发明不了新的恢复动作；当前实现始终使用确定性规则选择器。

## Tool Registry 规则

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

## 评测

评测协议在 [EVAL_PREREG.md](EVAL_PREREG.md) 中预注册。机器可读预测放在 [prereg.yaml](prereg.yaml)，`run_eval.py` 会在运行前检查。指标从 append-only event log 生成，不读取 agent 内部记忆。

当前矩阵：

- 9 个条件 x 10 个 seed = 90 run。
- 条件包括 baseline、5 个单故障、1 个复合故障、开启门禁的对抗 planner stub，以及关闭门禁的消融。
- 安全违规由 registry 下方的 ground-truth monitor 记录，不由 agent 自报。
- 结果拆分检测、恢复、终态和 HITL 升级。
- seed 只控制 mock world；虚拟时钟保证运行快速且可复现。

## 目录

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

## 边界

- 当前是 mock-only。battery、sensor 和 tool failure 注入是模拟器能力；导航 adapter 契约是最先计划迁移到真实底盘的部分。
- 在路线受阻与低电量叠加时，如果回坞路线也被拖住，仍可能耗尽电量。当前策略暴露这个风险，不把它隐藏成成功。
- route memory 在单个 run 内把受阻边当成持续不可用，因此临时障碍可能导致保守降级。
- 一次 navigation call 只携带一个审批 token。若目标同时需要受限区与低电量审批，当前策略保持阻断。
- 故障优先级由 observer 检查顺序实现；优先级表是审计记录，不是运行时策略表。
- 熔断在单个 run 内没有 half-open 恢复。
- 跨 run 长期记忆不属于当前注册评测。
