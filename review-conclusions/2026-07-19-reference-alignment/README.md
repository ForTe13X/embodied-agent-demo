# 参考架构对齐评审

> 评审日期：2026-07-19
>
> 代码基线：`origin/main@8dd61a3`
>
> 参考基线：用户提供的“Mission Executive + VLA skill + Safety Runtime”完整链路文档
>
> 交付方式：独立 worktree、独立分支、只新增本目录；未修改根 README、`demo.gif`、实现或既有实验产物

## 一句话结论

项目已经从“ROS2/Nav2 任务编排 demo”前进到一个**混合技能运行时原型**：经典导航 skill 与 VLA-shaped action-chunk skill 已共享 Registry 和审计边界，并分别采用明确、有限的恢复规则；但它目前证明的是 **learned-policy integration contract 的纯 Python/运动学 vertical slice**，不是训练后的 VLA、ROS2 `ExecuteVLASkill` Action、真实机械臂闭环或物理安全系统。

参考文档的核心定位仍然成立，但其中“当前项目没有 action chunk / policy inference，VLA skill 仍是未来”的描述已被 Phase D 部分超越。当前更准确的对外表述是：

> 一个面向机器人技能的任务级运行时原型：将经典导航技能与 VLA-shaped action-chunk skill 放在同一 Registry/runtime 审计边界下，导航与操作各自采用确定性门禁和有限恢复规则。现有证据覆盖 mock orchestration、ROS2/Nav2 loopback 迁移和 mock-policy 运行时；不包含训练后的 VLA、真实视觉感知、机械臂或物理安全认证。

不建议使用“VLA 系统”“真实机器人系统”“生产级 Safety Runtime”或“policy 绝对绕不过安全层”等表述。

## 当前进度判断

| 能力层 | 当前状态 | 判断 |
|---|---|---|
| Mission Executive / 任务级编排 | **已形成较完整 demo 证据** | Registry、门禁、恢复、日志、mock 评测与 Nav2 loopback 迁移构成项目最强基础 |
| learned-skill runtime 形态 | **Phase D 已完成原型** | action chunk、queue-empty hold、stale drop、cancel、逐动作 SafetyShield、Skill Supervisor 已有代码和测试 |
| Mission Executive 可管理的异步 skill | **部分实现** | skill 内部是异步 loop；Registry handler 仍阻塞等待终态，任务层没有 goal handle、feedback、cancel 或结果查询 |
| 同一正式编排图管理 Nav + learned skill | **部分实现** | Phase D 复用了 Registry/runtime/log，但复合任务是 procedural sequence，并未接入主 LangGraph graph |
| VLA / 视觉策略 | **未实现** | 当前 policy 是确定性、无图像、无训练的 stub；不能把 Phase D 称为 learned policy 实证 |
| ROS2 skill deployment | **未实现** | 无 `ExecuteVLASkill.action`、Action Server、独立 Policy Server、controller bridge 或 QoS/deadline 语义 |
| 真实安全闭环 | **未实现** | 当前是同进程、运动学 box 投影；没有碰撞、关节/速度/力矩、控制器反馈、deadman 或急停链 |
| 数据与 HIL 飞轮 | **未实现** | 无标定、时钟同步、遥操作数据、归一化、训练、shadow/HIL 或介入数据回灌 |

截至评审时，Phase D 已合入 `main`；另有开放的 PR #10 在处理 2026-07-11 独立评审中的一批 P1 问题。该 PR 不属于本评审基线，也未被带入本分支。

## Pros

1. **核心分层判断正确。** LLM 停留在任务层，policy 产生动作 proposal，确定性 runtime 决定能否执行；这比“让 LLM 直接控制机器人”更可信。
2. **VLA 被建模为一个高层 skill，而不是每个关节一个 tool。** [`vla_skill_tool.py`](../../phase_d/vla_skill_tool.py#L58-L85) 保留了清晰的 Registry 边界，符合参考架构。
3. **动作级监管已有可运行骨架。** [`vla_skill_runtime.py`](../../phase_d/vla_skill_runtime.py#L101-L187) 已覆盖 action queue、空队列 hold、stale-drop、cancel、timeout/no-progress 与逐动作投影。
4. **恢复职责有辨识度。** [`skill_supervisor.py`](../../phase_d/skill_supervisor.py#L19-L40) 对 unsafe、no-progress、timeout 采用不同归属和重试策略，避免所有层同时“抢救”同一故障。
5. **SafetyShield 是实际代码，不只是架构图。** [`safety_shield.py`](../../phase_d/safety_shield.py#L75-L135) 对非有限值、工作空间和步长做确定性处理，并由测试覆盖。
6. **项目已经有两层证据。** 任务层有 mock/loopback 迁移和故障恢复；动作层有越界、NaN、jitter、stale 与 cancel 的对抗路径。
7. **审计思路连续。** Nav skill 与 mock VLA skill 能复用 Registry 和事件日志；这是“Hybrid Embodied Agent Runtime”叙事最值得保留的部分。
8. **Phase D 局部边界披露诚实。** [`phase_d/README.md`](../../phase_d/README.md#L44-L48) 明确说明无训练、无真机、无物理接触。

## Cons / claim blockers

1. **`SafeAction` 并不能形成不可绕过的安全边界。** `_SHIELD_TOKEN` 和 `_mint_safe` 都可以从 Python 模块导入；测试自身也导入了私有令牌。它最多是正常代码路径上的 API 不变量，不是进程隔离、权限边界或物理安全保证。
2. **任务层看不到真正的异步 skill。** [`vla_skill_tool.py`](../../phase_d/vla_skill_tool.py#L64-L81) 在 handler 内一直 `await rt.execute(goal)`；因此没有外部 goal ID、feedback、cancel、deadline/cancel race 或 late-result invalidation。
3. **真实 policy 不能“同签名直接替换”。** runtime 直接依赖同步 `MockVLAPolicy.predict_chunk()`；若真实模型在此阻塞，整个 asyncio loop 会被阻塞。Observation 也没有 image/frame、calibration、model version 或 normalization 信息。
4. **action horizon 与 execution horizon 语义混在一起。** 当前 `execution_horizon` 实际是 queue 低水位；接收的 chunk 会全部进入队列，而不是“每次只执行 N 步后重新观测”。这削弱了参考文档强调的闭环控制含义。
5. **stale 判定基于 loop 自增序号，而非同步传感帧。** 每 10 ms 增加一次 observation sequence，使中等延迟推理容易被结构性判为过期；当前 stale demo 更像故障路径自检，尚未证明可用异步推理。
6. **postcheck 是循环证明。** [`composite_mission.py`](../../phase_d/composite_mission.py#L72-L78) 从 skill success 得出 `manipulation_ok`，再据此写 `verified=True`，没有重新读取世界状态或独立感知证据。
7. **复合任务不是同一正式 LangGraph graph。** 它是手写顺序流程；可证明的是“same Registry/runtime boundary and shared event log”，不是“同一编排图已管理经典与 learned skill”。
8. **当前 retry 不是可靠恢复。** 每次调用会重新构造独立 sim/policy；相同输入、seed 和 world 的重复尝试既没有新观察，也没有副作用 reconciliation。真实抓取中这可能重复已经发生的动作。
9. **评测证据仍很薄。** Phase D-2 只有三个 deterministic 条件、每个一次；prediction、实现、结果文档和 runs 在同一提交 `c95bafc` 中引入，缺少多 seed、延迟矩阵、模型/环境 manifest、CI 与独立复现。
10. **文档状态漂移。** [`PRODUCT.md`](../../docs/PRODUCT.md#L58-L59) 已把 Phase D 标为完成，但 [`POSITIONING.md`](../../docs/POSITIONING.md#L65-L85) 和 [`RECOVERY_OWNERSHIP.md`](../../docs/RECOVERY_OWNERSHIP.md#L44-L50) 仍把同一能力写成未来。
11. **真实链路的主要缺口没有消失。** 物理世界、视觉感知、机械臂控制、独立安全 supervisor、标定/时钟同步与长期 failure-data flywheel 仍未实现。

## 对外 claim 建议

| Claim | 是否可以使用 | 建议措辞 |
|---|---|---|
| 同一运行时边界约束经典 skill 与 mock learned-skill contract | **可以** | 明确限定为纯 Python/运动学 Phase D vertical slice |
| 已实现 action-chunk supervision 的关键故障路径 | **可以** | 指向 stale、NaN、越界、cancel、hold 和有限恢复测试 |
| 已把真实 VLA 接入机器人 | **不可以** | 当前为 deterministic VLA-shaped policy stub，无视觉/训练/硬件 |
| policy 结构上绝对绕不过安全层 | **不可以** | 改为“正常 API 路径要求 SafetyShield 投影”；进程边界尚未做 |
| Mission Executive 已能异步管理 VLA skill | **暂缓** | 先实现 goal handle、feedback、cancel、result 和 deadline 语义 |
| 已有独立 postcondition verification | **不可以** | 当前 postcheck 复用 skill outcome，没有独立 observation |
| 已证明真实机器人安全 | **不可以** | 当前只证明运动学 sim 中的确定性数值约束 |

## 建议优先级

1. **先把契约做真。** 让 skill 具备外部可管理的 start/feedback/cancel/result，抽出异步 `PolicyClient`，修正 horizon、freshness 和 terminal cleanup 语义。
2. **再把证据做强。** 独立 postcheck、共享 world state、多 seed/latency/failure matrix、真实 provenance 与 CI。
3. **然后接真实 policy 的 open-loop/shadow mode。** 先验证 image/state/action mapping、normalization、延迟和 late-result handling，不直接下发硬件。
4. **最后进入硬件闭环。** 完成 Robot Contract、标定/时钟同步、controller-owned safety、遥操作数据与 HIL gates 后，才允许 closed-loop action。

详细覆盖矩阵见 [ALIGNMENT_MATRIX.md](ALIGNMENT_MATRIX.md)，分阶段实施与验收门槛见 [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)，本次验证记录见 [VALIDATION.md](VALIDATION.md)。
