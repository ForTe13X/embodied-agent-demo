# Demo 项目独立评审结论

> 评审日期：2026-07-11
> 评审基线：`b6d9ef7`（`docs/demo-text-clarity`）
> 评审方式：独立 worktree、只新增本目录文档，不修改实现、现有 README 或评测产物

## 一句话结论

这是一个明显高于普通作品集水平的 embodied-agent demo：mock 侧的确定性编排、故障恢复、门禁、事件日志和预注册评测形成了完整闭环。当前提交重新生成 90-run 后，归一化 code hash 与日志路径，其余结果文本与仓库结果一致。

但它目前更适合定位为 **“可信的编排与评测 demo”**，还不适合定位为 **“真实机器人上的安全控制层”**。最关键的原因不是抽象风险：真实 adapter 没有把 access policy 落到 Nav2 transit path，真实 runtime 也没有独立 SafetyMonitor；仓库日志中，无 token 导航到 free 目标时，TF-derived 最近拓扑节点还连续被归类为 restricted `r1`。因此当前不能证明 mock 与真实 Nav2 的 access safety 等价。

## 建议的发布判断

| 目标 | 判断 | 前置条件 |
|---|---|---|
| 面试/作品集展示 mock 编排、恢复和评测方法 | **可以继续** | 先修 Viewer 结果误标、默认场景和自然语言示例 |
| 展示 ROS 2/Nav2 软件栈迁移 | **可以，但必须收紧措辞** | 明确 `nav2_loopback_sim`、非物理机器人、只验证部分终态迁移 |
| 宣称真实 Nav2 与 mock 的 access safety 等价 | **暂缓** | 先把 restricted/forbidden 约束落到真实路径层，并加入 TF geofence monitor |
| 生产部署或无人值守真实机器人 | **不建议** | 还缺真实 deadline、错误分类、路径级安全、真实侧独立监视和故障注入完整性检查 |

## 评分卡

| 维度 | 评价 | 说明 |
|---|---|---|
| 架构叙事 | 强 | LLM 只做意图，确定性层负责执行、安全与恢复，边界容易讲清楚 |
| Mock 实现 | 强 | goal-handle、watchdog、恢复表、Tool Registry 和 SafetyMonitor 闭环完整 |
| 评测方法 | 强，但证据封装需加强 | 固定 seed、预注册、消融、失败原样披露都很好；日志仍会覆盖，正式结果带 dirty hash |
| 测试 | 中上 | 34 个测试全部通过；real adapter、Phase C 负例、Viewer 与畸形 LLM 输出覆盖不足 |
| Demo 体验 | 素材强，结果展示有高优先缺陷 | GIF、视频、POV 和 Viewer 很有说服力；Viewer 当前会把 unsafe run 标成“安全 ✓” |
| Real Nav2 安全闭环 | 弱 | 目标门禁没有约束实际 transit path，真实 runtime 也没有独立违规监视 |
| 可维护性 | 中 | 模块命名清楚；但 graph 内循环较重，真实适配依赖 shim、私有字段和多份拓扑真值 |

## Pros

1. **产品边界清晰。** LLM 不直接控制速度/力矩，安全与恢复留在确定性代码，是最值得保留的核心叙事。
2. **异步导航契约选得对。** `send_goal / feedback / cancel / result` 支持在途电量与停滞 watchdog，比阻塞式 `navigate()` 更接近真实机器人栈。
3. **Mock 安全有纵深。** Registry 的白名单、schema、token、电量闸之外，还有位于其下的 SafetyMonitor，违规不依赖 agent 自报。
4. **恢复策略可审计。** 故障分类、恢复链和替代候选是闭集且确定性的，适合做回放、回归和因果解释。
5. **评测意识很强。** 固定 seed、虚拟时钟、RNG 分流、预注册预测、消融对照和未收敛 case 原样披露，明显优于只展示 happy path 的 demo。
6. **结果口径在当前快照上可重现。** 34/34 tests 通过；当前提交重跑 90-run 后，归一化代码 hash 和输出路径，其余结果文本与仓库中的 `RESULTS.md` 一致。正式 provenance 的 dirty hash 仍是独立缺口。
7. **展示资产完整。** 首屏 GIF、完整视频、字幕、POV、截图和 Viewer 能支持不同深度的观众。首屏 `demo.gif` 应继续保留。

## Cons

1. **真实路径安全与工具门禁脱节。** Registry 只审批目标，Nav2 实际路线可能穿过 restricted 区域。
2. **Viewer 没有复用正式指标口径。** 默认首屏就是关门禁消融，且 5 次违规后仍显示“已拦截，安全 ✓”。
3. **真实评测的证据完整性不够。** Phase C 不验证 mask 加载结果，异常后也可能遗留底层导航任务污染下一 rep。
4. **意图层存在静默偏差。** README 的中文节点示例在 rule fallback 下会把 `a1,a3` 扩成 `a1,a2,a3`；畸形 LLM JSON 还会直接抛异常。
5. **任务级安全约束没有原子 preflight。** Intent 收紧到 50% 电量红线时，30% 电量仍会先启动目标，下一 tick 才取消。
6. **“超时”主要是模拟故障。** Registry 没有真实 deadline；真实 ROS 调用异常或阻塞可能拖住整个 event loop。
7. **证据与环境没有完全冻结。** 无 CI、lockfile、Python 版本约束；正式结果标记为 `ebc3548-dirty`。
8. **文档有阶段漂移。** README、PRODUCT、USER_MANUAL 和 Phase C 对 Phase 命名、real/physical、venv 使用的描述不一致。

## 最高优先级建议

### 0. 先修真实路径 access safety，再谈“真实安全等价”

- 将 restricted/forbidden 区域落实到 Nav2 costmap、route server 或可验证的路径约束层，而不是只检查目标 node。
- 审批应授权一个明确目标或走廊，不能把整个路径的 `authorized=True` 当成笼统通行证。
- 真实 runtime 增加独立于 Registry 的 TF/geofence monitor，记录实际进入的区域。
- 新增 forced-replan 场景：free 目标可经 `r1` 绕行时，未授权路径必须被阻止且违规数为 0。

### 1. 对外演示前的同日修复

- Viewer 从后端取得与 `metrics.py` 同源的 `normalized_outcome`，并对全部 90 runs 做 UI/metrics 一致性测试。
- 默认 hero 改为 `nav_blocked/seed0` 或 baseline，不要默认打开消融。
- 修中文相邻 node ID、否定表达和 LLM malformed shape；fallback 时回显并确认将执行的任务。
- USER_MANUAL 全部使用 `.\.venv\Scripts\python`；README_EN 明确 “real ROS 2/Nav2 software stack on loopback simulation, no physical robot”。

### 2. 下一轮可靠性修复

- 为 ToolSpec 增加真实 deadline、typed output schema 和 transport error taxonomy。
- 修 Phase C：检查 mask 返回码、异常时 cancel/wait/finally、以最终 TF 判断是否归坞。
- 将 capture/perception/report 绑定为可验证 evidence chain，避免伪造 image/node/label。
- Viewer 用 `textContent`/安全 DOM API 渲染日志，消除 stored XSS。

### 3. 工程化补强

- 加 CI：pytest、90-run smoke、Viewer outcome 一致性、文档链接、real_metrics 负例。
- 固定 Python 版本并生成 lock；ROS 镜像使用 digest 或版本化 tag。
- 从干净 tag 重跑正式评测，发布 environment manifest、依赖版本和 artifact checksum。
- 把拓扑与阈值改成单一配置源，通过 Protocol 注入 mock/real runtime。

## 推荐拆分为四个后续 PR

1. `fix/viewer-truth-and-defaults`：统一结果分类、默认场景、错误状态和 XSS。
2. `fix/intent-and-onboarding`：规则解析、LLM schema、venv 命令、双语 claim。
3. `fix/phase-c-integrity`：mask 校验、最终位姿、异常清理、错误分类。
4. `safety/nav2-transit-enforcement`：真实路径 access enforcement + TF monitor + adversarial real-run。

详细证据见 [FINDINGS.md](FINDINGS.md)，验证命令与结果见 [VALIDATION.md](VALIDATION.md)。
