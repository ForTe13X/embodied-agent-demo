# 实施方案与验收门槛

## 总原则

实施顺序应是：**先让 skill contract 可管理、可停止、可审计，再换真实模型；先 open-loop/shadow，再进入硬件闭环。**

```text
Mission Executive
    │  goal / feedback / cancel / result
    ▼
ExecuteVLASkill boundary
    ├── SensorSnapshotProvider ── synchronized observation
    ├── PolicyClient ──────────── action proposal / chunk
    ├── Runtime queue ─────────── freshness / horizon / lifecycle
    ├── Safety gateway ────────── deterministic projection / stop
    ├── ControllerClient ──────── ack / tracking / hold
    └── PostcheckProvider ─────── independent outcome evidence
```

任何阶段都保持三条不变量：

1. policy 只能产生 proposal，不能直接持有 controller capability；
2. cancel、mission switch 或 generation 变化后，晚到 chunk 永不复活；
3. 每个 goal 恰好一个 terminal event，异常和进程退出也必须进入 hold/stop 与可审计终态。

## Stage D0：claim 与事实单一化（约 2–3 天，无硬件）

### Deliverables

- 统一 `PRODUCT.md`、`POSITIONING.md`、`RECOVERY_OWNERSHIP.md` 与 Phase D 的完成状态。
- 将 “learned policy” 收敛为 “VLA-shaped/mock action-chunk policy”。
- 将 “policy 绕不过 SafetyShield” 收敛为 “正常 API 路径要求投影”；明确其不是独立物理安全层。
- 将 “同一编排壳子”精确表述为 “same Registry/runtime boundary and shared event log”；正式 graph 接入另列里程碑。
- 将 Phase D-2 的 “prereg” 改为 deterministic expected-outcome matrix，或用独立、先于结果的 protocol commit 重做证据。
- 根 README 若需同步，只改 claim/入口，不改变首屏 `demo.gif` 展示块。

### Go gate

- 所有入口对 Phase D、Nav2 loopback 和物理边界的描述一致。
- 搜索不到“真实 VLA 已接入”“真实机器人 1:1 等价”“生产级安全”等超出证据的 claim。

## Stage D1：版本化 Robot/Policy Contract（约 1 周，无硬件）

### Deliverables

- 将 `phase_d` 变为正式可导入 package，不再依赖 `sys.path` 注入。
- 定义 Protocol：`PolicyClient`、`SensorSnapshotProvider`、`ControllerClient`、`SafetyProjector`、`PostcheckProvider`。
- 版本化 `ObservationBundle`：
  - `mission_id`、`goal_id`、`request_id`、`frame_id`；
  - monotonic timestamp + source timestamps；
  - image references / proprio / joint state / EEF pose；
  - calibration、embodiment、normalization、schema version。
- 版本化 `ActionChunk`：单位、frame、action space、chunk timestamp、model/checkpoint version。
- 拆分参数：
  - `action_horizon`：模型输出长度；
  - `execution_horizon`：本轮最多实际执行长度；
  - `queue_low_watermark`：预取阈值；
  - `max_action_age_ms`：动作时效预算。
- 先支持 relative-EEF contract；硬件选择后再冻结 joint/EEF mapping，不把模型动作直接解释为 driver command。

### Tests / Go gate

- wrong dimension、NaN/Inf、未知 skill、错 frame/unit/version、非单调 timestamp 全部产生 typed rejection。
- 所有 contract rejection 的 controller 调用次数为 0。
- schema/normalization/calibration version 进入事件日志与最终 result。

## Stage D2：真正的异步 Skill Runtime（约 1–2 周，无硬件）

### Deliverables

- 对 Mission Executive 暴露非阻塞生命周期：

```text
start_skill(...) -> goal_id
get_feedback(goal_id)
cancel_skill(goal_id)
get_result(goal_id)
```

- `PolicyClient.predict_chunk()` 使用真正 async transport 或受控 worker，不能在 event loop 内同步跑 GPU/网络推理。
- bounded deque + backpressure；明确新 chunk 是 replace、merge 还是 append，禁止无界 `extend`。
- request/generation 校验：late、out-of-order、wrong mission/model response 直接丢弃。
- inference deadline、sensor freshness deadline、controller ack deadline 分开计时。
- 外围 `try/finally`：policy exception、OOM、timeout、cancel race 均执行 hold/cleanup 并生成唯一 terminal event。
- retry 不再新建完全独立、同 seed 的 world；保存 attempt context、side effects 和最新 observation。

### Tests / Go gate

- hung/crashed predictor 不阻塞 mission loop。
- queue 永不超过配置上限。
- cancel 后 controller 收到的新动作数为 0；cancel-to-hold 不超过 2 个控制周期。
- mission switch 后旧 generation 的 chunk 100% 被拒绝。
- 每个 goal 恰好一个 start 和一个 terminal；没有 orphan inference task。

## Stage D3：安全 gateway、独立 postcheck 与审计（约 1–2 周，无硬件）

### Deliverables

- policy 移至独立进程，只能访问 proposal transport；controller capability 只由 safety/controller gateway 持有。
- Safety projection 增加基于 `dt` 的 velocity/acceleration、state freshness 和 controller tracking error。
- 增加 projection budget：连续 clamp 或滑窗比例超阈值时 hold，并上浮 `SAFETY_PROJECTION_STORM`。
- 独立 `PostcheckProvider` 重新读取 environment state；不得复用 skill 的 success bool。
- retry 前必须有新 observation、不同 attempt context 或显式恢复动作；否则停止盲目重复。
- 标准 fault envelope：`domain_failure / policy_failure / infrastructure_failure / safety_stop / canceled`。
- 日志关联 `run_id / mission_id / goal_id / attempt_id / request_id / model / calibration / normalization`。

### Tests / Go gate

- 直接构造/导入内部 Python 类型也无法获得 controller transport capability。
- skill 自报成功但 object 未到目标位置时，独立 postcheck 必须判失败。
- unsafe 始终不自动重试；transient retry 必须基于新证据。
- stale state、ack timeout、tracking error、projection storm 默认 hold/stop。

## Stage E0：ROS2 Action 与 model-in-loop（约 2–4 周，可无真机）

### Deliverables

- 定义真正的 `ExecuteVLASkill.action`：goal、feedback、cancel、result 与 typed terminal reason。
- ROS2 Action Server/Client + 外部 mock Policy Server；先使用现有 tabletop/loopback 环境验证 transport。
- 将 skill dispatcher 接入正式 LangGraph/executor state，而不是单独 procedural script。
- Nav2 与 manipulation 使用同一 ROS runtime、mission lifecycle、event correlation IDs。
- 接入一个实际 policy 的 **open-loop 或 shadow mode**：只记录 proposal、延迟、stale 与投影结果，不控制硬件。

### 模型选择建议

- 窄而精确的单任务先用 ACT/Diffusion/specialist baseline；OpenVLA 官方结果也显示 narrow precise task 上 Diffusion Policy 可能更强，而 OpenVLA 的优势更偏多物体、多任务和语言 grounding。
- 资源受限时可优先评估 SmolVLA 一类轻量模型；其官方资料强调 450M 参数、continuous action chunks 与异步推理，但仍必须通过本项目自己的 contract/latency gates。
- OpenVLA-OFT 的 parallel decoding 和 action chunking 可作为高吞吐 VLA 路线参考；不要据此跳过硬件、数据与安全验证。

### Go gate

- accept/reject/feedback/cancel/result 语义在 server restart、feedback loss、DDS delay 下保持确定。
- 杀掉 Policy Server 或 Action Server 后，controller 默认 hold，旧 generation 不得恢复。
- 实际模型换入时不改 Mission Executive；P95 inference 与 action age 满足预注册 budget。
- shadow mode 中 malformed/stale/late proposal 的 controller 调用次数为 0。

## Stage H0：硬件 readiness（硬件依赖）

### 前置选择

- 单臂、单夹爪、单桌面任务；不要一开始做移动底盘 + 双臂 + VLM + 长任务。
- 固定 ROS2 driver、`ros2_control`、相机、GPU 与一个仿真栈。
- 先写 Robot Contract，再采数据：action space、frame、rate、单位、gripper 语义、状态字段、归一化和时间同步。

### Deliverables / Gate

- E-stop、deadman、joint/workspace/velocity limits、通信丢失 watchdog、controller tracking monitor。
- 相机内外参、手眼标定、robot/world frame 与统一时间基准。
- 100/100 cancel/watchdog/E-stop 验证无失效；任何 safety supervisor 故障默认停机。
- 时间同步误差、控制周期和 inference/action-age budget 预注册并达标。

未通过 H0，不允许模型输出进入 closed-loop hardware control。

## Stage H1：数据与经典基线（约 2–4 周，硬件依赖）

### Deliverables

- 先实现 scripted/传统视觉 + MoveIt 或 specialist policy baseline。
- 采集约 50–100 episodes 只作为 pilot 假设，不作为“数据已足够”的保证。
- 数据按日期、相机、操作者、物体实例和空间 cell 拆分；禁止随机拆 frame 造成泄漏。
- 数据集保存 raw sensor、动作、timestamps、calibration、normalization、success/partial/failure 和 intervention。
- 使用 LeRobotDataset v3 或同等级版本化格式；明确 finalize、增量写入与版本追踪。

### Go / No-Go

- 如果经典方案稳定满足目标，不为“使用 VLA”而强行引入 VLA。
- 只有任务确实需要多物体、多摆放、视觉泛化或语言 grounding 时，才进入 VLA fine-tune。

## Stage H2：learned policy 与 guarded closed loop（约 4–8 周，硬件/GPU 依赖）

### 顺序

1. offline replay 与 held-out evaluation；
2. shadow mode；
3. 低速、受限 workspace、人在环监督；
4. guarded closed loop；
5. HIL takeover/correction 数据回灌与再训练。

### Go gate

- held-out cells 中至少不劣于 specialist baseline，并在语言/对象泛化上提供明确增益。
- collision、joint/workspace 越限、cancel 后动作数均为 0。
- 按场景报告 success、partial success、intervention rate、recovery success、P95 inference、stale-drop、projection rate 和 postcheck false positive/negative。
- takeover 后可平滑回到 policy；恢复数据与普通示范可以区分和追溯。

## 统一测试矩阵

| 类别 | 必测故障 | 核心不变量 |
|---|---|---|
| Contract | wrong dim、NaN/Inf、错单位/frame/version、timestamp 倒退 | typed reject；controller 0 调用 |
| Timing | jitter、out-of-order、hung inference、queue overflow/underflow、stale sensor | action age 不超预算；队列有界；空队列 hold |
| Lifecycle | infer 前/中/后 cancel、mission switch、重复 goal、server restart | cancel 后 0 action；旧 generation 永不复活 |
| Safety | workspace、连续 clamp、rotation/gripper、tracking error、watchdog | raw proposal 0 次直达；持续顶限位必须停或上浮 |
| Recovery | unsafe、transient/persistent no-progress、infra failure | unsafe 不重试；retry 必须有新证据；infra circuit 独立 |
| Postcheck | self-report false positive、stale observation、物体未到位 | outcome 由独立 observation/evidence 决定 |
| ROS2 | Action Server/Policy Server 消失、feedback 丢失、cancel race、DDS delay | hold/stop 是默认失败行为 |
| Audit | exception、cancel、OOM、进程崩溃 | 每 goal 恰好 start + terminal；完整版本与 correlation IDs |
| Soak/property | 随机 proposal ≥100k、长时间 queue/inference 扰动 | 0 unsafe dispatch；无无界内存增长、无未捕获异常 |

## 指标与发布门槛

| 指标 | 软件阶段 | 硬件阶段 |
|---|---|---|
| Unsafe/raw dispatch | 0 | 0 |
| Cancel 后动作数 | 0 | 0 |
| Goal terminal 完整性 | 100% 恰好一个终态 | 100% 恰好一个终态 |
| Queue bound | 100% 不超上限 | 100% 不超上限 |
| Stale/late reject | 100% | 100% |
| P95 inference / action age | 低于预注册 budget | 低于控制任务 budget |
| Task success / partial / failure | 分场景、多 seed | 分 held-out cell/物体/日期 |
| Intervention / recovery success | model-in-loop 起记录 | 必须报告 |
| Projection/clamp rate | 超预算自动上浮 | 超预算自动 hold/stop |

## 当前明确不做

- 不从头训练 foundation VLA。
- 不把 LangGraph/Registry 放进 20–50 Hz 逐帧控制循环。
- 不把模型 confidence 当作安全信号。
- 不同时集成多个模型、仿真器和机器人 embodiment。
- 不继续“同输入、同 seed、同 world”的盲目 retry。
- 不在缺少 calibration/timestamp/freshness contract 时让视觉模型进入控制闭环。
- 不把当前同进程 SafetyShield 宣称为生产级或硬件安全边界。

## 官方技术参考

- [OpenVLA official project](https://openvla.github.io/)：规模、LoRA、频率与 specialist baseline 对比。
- [OpenVLA-OFT official project](https://openvla-oft.github.io/)：parallel decoding、continuous actions 与 action chunking。
- [SmolVLA official Hugging Face article](https://huggingface.co/blog/smolvla)：轻量模型、continuous chunks 与 asynchronous inference。
- [LeRobot HIL data collection](https://huggingface.co/docs/lerobot/main/hil_data_collection)：pause、takeover、return-to-policy 与 correction 数据。
- [LeRobotDataset v3](https://huggingface.co/docs/lerobot/main/lerobot-dataset-v3)：版本化机器人数据格式。
- [NVIDIA Isaac-GR00T official repository](https://github.com/Nvidia/Isaac-GR00T)：embodiment config、relative-EEF/action-state mapping 与 normalization。
- [Physical Intelligence openpi](https://github.com/Physical-Intelligence/openpi)：policy server、action chunks、数据转换与 normalization workflow。
