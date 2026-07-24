# 参考文档与当前实现对齐矩阵

## 判定口径

- **已实现**：代码路径、测试和文档证据能够共同支持该能力。
- **部分实现**：关键形态存在，但边界、语义或证据不足以支持参考文档中的完整 claim。
- **未实现**：仓库没有相应运行路径；架构图或未来计划不算实现。
- **过时**：参考文档描述的仓库现状已被后续 Phase D 改变。

## 端到端链路

| 参考架构环节 | 状态 | 当前证据 | 缺口 / 正确解读 |
|---|---|---|---|
| Mission Executive 负责任务分解、工具选择、恢复与审计 | **已实现（demo 范围）** | `embodied_agent/` 的 graph、Registry、exception/recovery 与事件日志；Phase A-C 结果 | 仍是 demo/loopback 证据，不能外推为生产 autonomy stack |
| VLA 是编排层下面的一类 skill | **已实现（接口形态）** | [`register_vla_skill()`](../../phase_d/vla_skill_tool.py#L58-L85) 将 `execute_vla_skill` 注册为单一 tool | 具体 policy 是 stub；skill 还不是 ROS2 Action 或独立服务 |
| `ExecuteVLASkill` ROS2 Action：goal/feedback/cancel/result | **未实现** | 无 `.action` 定义或 Action Server | Registry handler 阻塞到终态；Mission Executive 无法观察进度或外部取消 |
| Policy Server / 独立推理进程 | **未实现** | [`_infer()`](../../phase_d/vla_skill_runtime.py#L83-L86) 同进程同步调用 mock policy | 需要 async/RPC client、deadline、request ID、model version、late-result invalidation |
| 图像 + 指令 + 本体状态的 observation contract | **未实现** | [`Observation`](../../phase_d/action_types.py#L38-L44) 只有 seq/time/EEF state | 无 image/frame/calibration、joint state、timestamp bundle、normalization version |
| action chunk 生成与队列 | **部分实现** | [`MockVLAPolicy`](../../phase_d/mock_vla_policy.py) 生成 chunk；runtime 有 queue | 真实策略、bounded queue、backpressure、chunk replace/fusion 未实现 |
| action horizon / execution horizon 闭环 | **部分实现且语义偏差** | `execution_horizon` 控制低水位触发推理 | 当前会继续执行整个已接收 chunk；需拆成 action horizon、execution horizon、queue low-water |
| observation freshness / stale chunk | **部分实现** | 序号落后超过阈值就丢弃 | seq 每 loop 自增，不对应同步 sensor frame；缺墙钟 deadline、frame bundle 和 out-of-order request 校验 |
| queue 空时 hold，取消后停止 | **部分实现** | runtime 的 hold/cancel 路径有测试 | `hold_position()` 在 toy sim 中为空操作；外部任务层无 cancel handle，异常 cleanup 也未完全封闭 |
| 独立 Safety Shield | **部分实现** | 非有限值、workspace、translation/rotation/gripper 投影 | 同进程、令牌可导入；无 collision、joint、velocity/accel、force/torque、deadman、controller fault |
| Controller 只接收安全动作 | **部分实现（toy invariant）** | [`TabletopSim.send()`](../../phase_d/tabletop_sim.py) 只接收 `SafeAction` | Python 类型检查不是系统权限边界；真实 controller/driver 未实现 |
| Skill Supervisor 的恢复所有权 | **部分实现** | unsafe 不重试；no-progress/timeout 有限重试 | 尚未与正式 graph/exception manager 形成统一 fault envelope 和竞态测试 |
| 独立 postcondition verification | **未实现** | composite 写入 `verified=True` | verification 复用 skill success，没有重新观测 object/world state |
| Nav + VLA 共用编排壳子 | **部分实现** | 共用 Registry/runtime/event log | composite 是 procedural sequence，不是主 LangGraph；Nav 与 tabletop sim 也不是同一物理 world |
| 审计与复现 | **部分实现** | 结构化事件、确定性 fault modes、Phase D tests | Phase D-2 每条件一次；缺 manifest、代码/模型/标定版本、多 seed、CI 和外部复现 |
| 遥操作数据、训练、HIL 介入飞轮 | **未实现** | 无相应代码或数据集 | 参考文档中的数据与部署链路仍全部属于硬件依赖阶段 |

## 参考文档中需要更新的时间性判断

### 已过时

1. **“当前项目没有 action chunk / policy inference。”** Phase D 已有 mock chunk producer、异步 queue 和 stale/cancel 路径；正确说法应改为“已有 VLA-shaped runtime，不含真实视觉策略或独立 inference server”。
2. **“VLA skill 还是下一步。”** skill 的 registry/runtime 形态已经存在；下一步应是把内部 vertical slice 升级为外部可管理的 contract。
3. **“没有 manipulation/VLA skill。”** mock runtime 这一层已部分补齐；真实 perception-policy-controller chain 仍为空。

### 仍然成立

1. loopback Nav2 不是物理机器人。
2. 没有真实视觉感知、机械臂、接触动力学或实时 controller 链。
3. VLA 应作为 skill 挂在 Mission Executive 下面，而不是替代任务编排、安全和恢复层。
4. 模型输出只能作为 proposal；最终安全必须由独立、确定性的 runtime/controller 约束。
5. 应先做单一桌面技能和清晰 Robot Contract，再谈长任务、移动底盘 + 双臂或大规模预训练。

## 关键 findings

### C0-1：不可绕过的安全 claim 不成立

[`action_types.py`](../../phase_d/action_types.py#L57-L77) 的 `_SHIELD_TOKEN` 与 `_mint_safe` 只是命名约定。Python 调用者可以导入它们并构造 `SafeAction`；[`test_safety_shield.py`](../../phase_d/test_safety_shield.py#L12) 也直接导入 `_SHIELD_TOKEN`。

**影响：** “policy 结构上绕不过安全投影”不能作为系统安全结论。若 policy 与 runtime 同进程且共享同一解释器，这一层没有安全隔离。

**建议：** controller gateway 独占 action capability；policy 置于独立进程，只通过 schema 化 proposal transport 通信。保留 `SafeAction` 类型可以提高代码质量，但不要把它描述为 security/safety boundary。

### C0-2：当前 skill 边界不是 Mission Executive 可管理的异步 Action

[`vla_skill_tool.py`](../../phase_d/vla_skill_tool.py#L64-L81) 在 Registry handler 内等待整个 runtime 完成，外部只会看到最终 dict。

**影响：** Phase D 只证明了 skill 内部异步 action queue，没有证明任务层可查询 progress、抢占、取消或处理 late result。

**建议：** 先实现纯 Python `SkillHandle` contract，再一一映射到 ROS2 Action：`start -> goal_id`、`feedback`、`cancel`、`result`、`terminal_reason`。

### P1-1：推理异步语义不足以承载真实模型

[`_infer()`](../../phase_d/vla_skill_runtime.py#L83-L86) 虽是 `async def`，内部直接执行同步 predictor。真实推理、网络或 GPU 调用会阻塞 event loop。runtime 也没有 `try/finally` 保证所有异常都产生唯一 terminal event 与 hold/cancel cleanup。

**建议：** `PolicyClient` 使用真正的 async transport 或受控 worker；请求必须包含 `mission_id/request_id/observation_timestamp/model_version`，响应晚到或版本失配时不可进入 queue。

### P1-2：闭环 horizon 与 freshness 模型需要重写

[`execution_horizon`](../../phase_d/vla_skill_runtime.py#L39) 被用作 queue 低水位；[`queue.extend`](../../phase_d/vla_skill_runtime.py#L133-L134) 接受完整 chunk。Observation seq 则每 10 ms loop 自增。

**影响：** “执行 N 步后重新观测”的执行语义没有被实现；stale threshold 也不对应真实 sensor frame。

**建议：** 明确三个参数：`action_horizon`（模型输出长度）、`execution_horizon`（最多实际执行多少步）、`queue_low_watermark`（何时预取）；freshness 绑定同步 frame bundle 的 monotonic ID + timestamp。

### P1-3：postcheck 与 shared world 不成立

[`composite_mission.py`](../../phase_d/composite_mission.py#L54-L86) 的 Nav、VLA 和 verify 没有共享可观测物理状态；`verified=True` 来自 skill outcome。

**建议：** simulator/world state 由独立 environment service 持有；postcheck 通过新的 observation/evidence 判断目标是否满足，而不是复用 skill result。

### P1-4：clamp storm 没有升级策略

当前 jitter 可以被多次 clamp 后继续执行。频繁投影本身应被视为 distribution shift、标定错误或 policy/controller contract 不匹配的异常信号。

**建议：** 记录连续/滑窗 projection ratio；超过预算时 hold，并上浮 `SAFETY_PROJECTION_STORM`，禁止无限“修正后继续”。

### P1-5：产品文档未形成单一事实源

[`PRODUCT.md`](../../docs/PRODUCT.md#L58) 与 [`POSITIONING.md`](../../docs/POSITIONING.md#L65-L85)、[`RECOVERY_OWNERSHIP.md`](../../docs/RECOVERY_OWNERSHIP.md#L44-L50) 对 Phase D 的状态互相矛盾。

**建议：** 后续单独做 narrative truth-sync PR；保留根 README 首屏素材，只同步 claim、Phase D 入口和软件栈/物理边界。

## 最合适的岗位展示证据链

1. **先讲问题：** 模型只提出 proposal，runtime 决定是否执行。
2. **再讲任务层迁移：** 同一任务级接口从 mock 迁到 ROS2/Nav2 loopback，并区分 Nav2 局部恢复与 Mission Executive 恢复。
3. **展示可证伪评测：** gates-on 对抗、gates-off 消融、ground-truth monitor，而不是只展示 happy path。
4. **展示动作层 supervision：** 越界、NaN、stale、cancel、queue-empty hold 与重复 projection/clamp；同时说明 storm escalation 尚未实现。
5. **讲清恢复归属：** unsafe 不重试；局部 no-progress 有限重试；超出 skill 能力再上浮。
6. **主动陈述边界：** mock policy、运动学 sim、无视觉/训练/机械臂/物理安全。

这条证据链更适合 Mission Autonomy、Robot Policy Integration 或 Embodied Agent Runtime 岗位，而不是把项目包装成模型训练项目。
