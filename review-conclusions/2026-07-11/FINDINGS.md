# 分级 Findings

## 严重度定义

- **P0 — claim/release blocker**：会直接推翻核心安全或结果可信度声明。
- **P1 — high**：下次公开 demo 或真实评测前应修。
- **P2 — medium**：不一定立即失败，但明显削弱安全、证据链或维护性。
- **P3 — low**：工程 polish 或已知限制。

## P0

### F-01：真实 Nav2 的 transit path 不受 access policy 约束

**状态：代码缺口已确认；日志按项目自身 TF→topological-node 映射给出直接风险信号。**

Registry 只检查目标节点的 access：

- [`registry.py` L195-L224](../../embodied_agent/registry.py#L195-L224)

真实 adapter 明确说明 `avoid_edges`、`restricted_ok_nodes` 和 `allow_*` 只用于“进度路线”计算，不影响 Nav2 实际发出的目标；随后直接 `goToPose`：

- [`rclpy_adapter.py` L125-L155](../../phase_b/rclpy_adapter.py#L125-L155)

真实 runtime 又将 `safety` 设为 `None`：

- [`real_runtime.py` L75-L85](../../phase_b/real_runtime.py#L75-L85)

仓库日志中的证据：

- free 目标 `a2` 的请求没有 approval token：[`rep_0.jsonl` L2](../../phase_c/runs_real/nav_blocked/rep_0.jsonl#L2)
- 同一目标执行期间，TF-derived 最近拓扑节点被标为 `pose == "r1"`：[`rep_0.jsonl` L155](../../phase_c/runs_real/nav_blocked/rep_0.jsonl#L155)
- 这个 pose 是“最近 waypoint + 滞回”分类，而不是 geofence polygon 进入事件：[`rclpy_adapter.py` L318-L333](../../phase_b/rclpy_adapter.py#L318-L333)
- `rep_0/1/2` 每个都有连续 13 个 `pose == "r1"` 采样：[`rep_0.jsonl` L155](../../phase_c/runs_real/nav_blocked/rep_0.jsonl#L155)、[`rep_1.jsonl` L155](../../phase_c/runs_real/nav_blocked/rep_1.jsonl#L155)、[`rep_2.jsonl` L155](../../phase_c/runs_real/nav_blocked/rep_2.jsonl#L155)
- 三个 rep 的结果仍聚合为 `completed_full=3`：[`PHASE_C_RESULTS.md` L34-L43](../../phase_c/PHASE_C_RESULTS.md#L34-L43)
- 仓库文字也记录了改道穿过 `r1`：[`FINDINGS.md` L123-L130](../../phase_b/FINDINGS.md#L123-L130)

**影响**

“门禁在 adapter 上方，所以真实 Nav2 也照旧安全”的结论目前无法成立。当前只验证了直接请求 restricted/forbidden 目标会被拦截，没有约束或监视 free 目标的 transit path。由于项目尚未定义真实侧 restricted polygon，日志不能单独证明进入了一个有几何边界的禁区；但代码确实不具备宣称 access-safety 等价所需的 enforcement 与 detection。

**建议**

1. 把访问区变成真实 costmap/geofence/route 约束。
2. 真实侧增加基于 TF 的独立区域监视器。
3. approval token 绑定目标和允许走廊。
4. 对 free 目标做 forced-replan adversarial test，断言实际轨迹不进入 `r1/f1`。

## P1

### F-02：Viewer 将 unsafe run 显示为“安全 ✓”，且默认自动加载该场景

**状态：浏览器实测确认。**

Viewer 直接读取 reporter 的 `outcome_hint`，没有复用正式指标的地面真值分类：

- [`index.html` L132-L143](../../viewer/index.html#L132-L143)
- [`index.html` L191-L193](../../viewer/index.html#L191-L193)
- 正式分类器：[`metrics.py` L106-L131](../../embodied_agent/evaluation/metrics.py#L106-L131)

后端按条件目录字母排序，`ablation_gates_off` 排第一；前端启动后自动加载第一项：

- [`serve.py` L44-L54](../../viewer/serve.py#L44-L54)
- [`index.html` L168-L177](../../viewer/index.html#L168-L177)
- [`index.html` L335](../../viewer/index.html#L335)

实测 `ablation_gates_off:0` 到最终 tick 时：

```text
violations = 5
outcome = 越权请求已被拦截,安全 ✓
```

全部 90 runs 的口径漂移还包括：10 个 ablation 正式分类均为 `unsafe_failure`；`compound/seed7` 正式为 `safe_abort`；30 个 nav_unreachable/sensor/tool run 正式为 `degraded_complete`，Viewer 却显示普通 completed。

**建议**：由后端返回唯一的 `normalized_outcome` 与解释；做 90-run UI/metrics 一致性回归；默认选择 `nav_blocked/seed0`。

### F-03：Intent 收紧的电量红线不会阻止目标启动

`Intent` 允许把安全红线收紧到高于静态 20%：

- [`intent.py` L17-L34](../../embodied_agent/intent.py#L17-L34)

但 Registry preflight 始终只使用静态 `BATTERY_FLOOR_PCT`；更严格的 Intent 阈值要等目标已启动、推进一个 tick 后才由 observer 处理：

- [`registry.py` L212-L242](../../embodied_agent/registry.py#L212-L242)
- [`graph.py` L193-L243](../../embodied_agent/graph.py#L193-L243)

复现：`battery_floor_pct=50`、初始电量 30 时，`t=0 goal_started`，`t=1 watchdog_triggered`。

**建议**：将本次任务的 effective floor 注入 Registry，`send_goal` 前原子检查；SafetyMonitor 分开记录静态安全违规和任务策略违规。

### F-04：真实 Nav2 失败被统一压成 `unreachable`

除成功与取消外，真实 adapter 将其余结果全部映射为 `aborted/unreachable`：

- [`rclpy_adapter.py` L387-L393](../../phase_b/rclpy_adapter.py#L387-L393)
- 上层据此走 `NAV_UNREACHABLE`：[`graph.py` L224-L230](../../embodied_agent/graph.py#L224-L230)

这会把定位错误、BT 加载失败、基础设施超时和真正无路可达混为一类，并可能把目标写入不可达记忆。

**建议**：至少区分 `target_unreachable / transient_infra / localization / timeout / canceled / policy_rejection`。

### F-05：Registry 没有真实 deadline，ROS 同步调用可能阻塞 watchdog

Registry 的 timeout 路径仅在 FaultInjector 返回 `"timeout"` 时模拟；正常 handler 没有 deadline：

- [`registry.py` L167-L191](../../embodied_agent/registry.py#L167-L191)
- [`registry.py` L331-L346](../../embodied_agent/registry.py#L331-L346)

`RclpyAdapter.send_goal()` 是 `async def`，但内部同步调用 `goToPose`：

- [`rclpy_adapter.py` L125-L169](../../phase_b/rclpy_adapter.py#L125-L169)

**建议**：每个 ToolSpec 配置 deadline；使用原生 async action future 或隔离 ROS executor；明确处理“超时但动作可能已提交”。

### F-06：Phase C 故障注入失败仍会计入对应条件

`swap_mask()` 只检查 response 非空，不检查 LoadMap 成功码；调用方还忽略其返回值：

- [`run_real_eval.py` L54-L62](../../phase_c/run_real_eval.py#L54-L62)
- [`run_real_eval.py` L89-L107](../../phase_c/run_real_eval.py#L89-L107)

**影响**：mask 加载失败时，baseline 行为也可能被标为 `nav_blocked` 或 `nav_unreachable`。

**建议**：失败即使 rep 无效；日志记录 mask 名、内容 hash、ROS 返回码和生效确认。

### F-07：Phase C 异常清理可能遗留真实导航任务

- `run_condition()` 仅在成功路径关闭日志：[`run_real_eval.py` L89-L108](../../phase_c/run_real_eval.py#L89-L108)
- 外层捕获异常后直接继续下一 rep：[`run_real_eval.py` L120-L129](../../phase_c/run_real_eval.py#L120-L129)
- `reset_dock()` 清空 `_active`，却没有 cancel 底层任务：[`run_real_eval.py` L65-L70](../../phase_c/run_real_eval.py#L65-L70)

**建议**：`try/finally` 中 cancel、等待终态、关闭日志，再复位；每个 rep 使用独立 adapter 生命周期更稳妥。

### F-10：README 的中文 LLM fallback 示例会静默改变任务

README 示例是：

- [`README.md` L61](../../README.md#L61)

当 LLM provider 不可用时，规则 fallback 的 Unicode `\b` 不能识别紧邻中文的 `a1/a3`，于是退回默认 `a1,a2,a3`：

- [`intent.py` L45-L52](../../embodied_agent/intent.py#L45-L52)

同时 `report_anomalies=report or True` 永远为 True：

- [`intent.py` L53-L59](../../embodied_agent/intent.py#L53-L59)

**建议**：使用 ASCII 边界 lookaround；增加中文相邻、标点、否定表达测试；fallback 时显式显示差异并要求确认。

### F-11：文档对 real/physical 和阶段状态的描述不一致

英文 README 使用 “verified 1:1” 和 “real robot navigation stack”，强于 Phase C 的实际证据：

- [`README_EN.md` L15-L21](../../README_EN.md#L15-L21)
- Phase C 明确是 `nav2_loopback_sim`、非物理机器人、缩减迁移验证：[`PHASE_C_RESULTS.md` L3-L16](../../phase_c/PHASE_C_RESULTS.md#L3-L16)

`docs/PRODUCT.md` 仍把 Phase B 写成未来，并把 Phase C 定义为 VLM：

- [`PRODUCT.md` L49-L56](../../docs/PRODUCT.md#L49-L56)

**建议**：建立唯一 phase/status 表；统一为 “real ROS 2/Nav2 software stack on loopback simulation; no physical robot; selected outcomes transfer, internal replanning may differ”。

### F-12：用户手册安装到 venv，后续却调用全局 Python

- 安装：[`USER_MANUAL.md` L5-L14](../../docs/USER_MANUAL.md#L5-L14)
- 后续命令：[`USER_MANUAL.md` L16-L24](../../docs/USER_MANUAL.md#L16-L24)、[`USER_MANUAL.md` L40-L53](../../docs/USER_MANUAL.md#L40-L53)

本机按手册使用全局 Python 运行 demo，得到 `ModuleNotFoundError: rich`。

**建议**：始终使用 `.\.venv\Scripts\python`，或先明确激活并打印 `sys.executable`。

## P2

### F-08：真实指标把“曾到过 dock”误当成“最终停在 dock”

**状态：latent classifier defect，未发现它改变当前 Phase C 四个条件的已提交结果。**

`at_dock = DOCK in visited`，并没有检查最终位置：

- [`real_metrics.py` L40-L66](../../phase_c/real_metrics.py#L40-L66)

最小输入 `visited=["a2", "a3", "dock", "a2"]` 会得到 `completed_full`，尽管最终位置是 a2。当前场景只在任务末尾访问 dock，因此这是需要负例锁住的潜在缺陷，而不是对当前结果的反证。

**建议**：以最终 TF/最终 state 为准，至少使用 `visited[-1] == dock`。

### F-09：`report_finding` 允许伪造 evidence

**状态：adversarial ToolRegistry capability gap，未发现当前固定 planner 已污染现有 run。**

输入只是三个任意字符串，handler 不验证 image 是否存在、node 是否在图中、机器人是否到过该点：

- [`registry.py` L58-L62](../../embodied_agent/registry.py#L58-L62)
- [`registry.py` L286-L292](../../embodied_agent/registry.py#L286-L292)

实测 `image_id=never-captured, node_id=z9, label=<script>...` 仍返回成功并写入 `finding_reported`。这说明白名单能限制“调用哪个工具”，却不能保证该工具提交的 evidence 有来源。

**建议**：维护 capture/perception ledger；report 只接受不可伪造的 artifact ID，并校验捕获位姿、感知结果和当前 run；把该调用加入 adversarial matrix。

### F-13：LLM 后校验对合法 JSON 的错误类型会崩溃

`_validate()` 直接遍历 `patrol_nodes` 并直接 `float()`；异常发生在 LM Studio 网络/JSON try 块之外：

- [`llm_intent.py` L41-L52](../../embodied_agent/llm_intent.py#L41-L52)
- [`llm_intent.py` L86-L94](../../embodied_agent/llm_intent.py#L86-L94)

复现：`patrol_nodes=None` → `TypeError`；`battery_floor_pct="not-a-number"` → `ValueError`；字符串 `"false"` 又会被 `bool()` 当成 True。

**建议**：为 provider 输出定义独立 Pydantic schema，捕获 ValidationError 后统一 fallback。

### F-14：输出 schema 与幂等语义不完整

Registry 只检查 required key 是否存在，不验证类型、非空或字段关系：

- [`registry.py` L331-L345](../../embodied_agent/registry.py#L331-L345)

`capture_image` 被标记幂等，但 mock 每次递增 image ID：

- [`registry.py` L318-L329](../../embodied_agent/registry.py#L318-L329)
- [`adapter.py` L97-L99](../../embodied_agent/adapter.py#L97-L99)

一次 malformed 后自动重试会产生第二次 capture。建议使用 typed output model；capture 设为非幂等或加入 idempotency key。

### F-15：“append-only”是进程内语义，不是不可覆盖的审计证据

- EventLog 对已有文件使用 `"w"`：[`events.py` L33-L36](../../embodied_agent/events.py#L33-L36)
- 正式评测检查忽略 `runs/` 修改：[`run_eval.py` L21-L28](../../run_eval.py#L21-L28)
- 同 seed 重跑会覆盖同名文件：[`harness.py` L42-L52](../../embodied_agent/evaluation/harness.py#L42-L52)

当前没有 run manifest、hash chain、rerun ID 或 artifact checksum。“预注册只跑一次”主要靠流程纪律，而非代码强制。

### F-16：Viewer 对日志内容使用 `innerHTML`，存在 stored XSS

- run option 与事件流均通过字符串拼接写入 DOM：[`index.html` L168-L173](../../viewer/index.html#L168-L173)、[`index.html` L296-L310](../../viewer/index.html#L296-L310)

日志包含 mission、HITL message、finding label 等可控字段。Viewer 只绑定 localhost 降低了影响，但不应执行日志内容。

**建议**：使用 `document.createElement` 和 `textContent`，并对后端返回做 schema 校验。

### F-17：依赖与证据环境没有冻结，也没有 CI

- [`requirements.txt`](../../requirements.txt) 使用宽范围 `~=`，无 Python 版本与 lock。
- ROS 镜像/tag 也可漂移；仓库没有 `.github/workflows`、`pyproject.toml`、lint/type/security 配置。
- 正式结果标记为 `ebc3548-dirty`：[`RESULTS.md` L4](../../RESULTS.md#L4)。

本次在干净 `b6d9ef7` 上重跑 90 runs，归一化结果与已提交结果一致，这是优点；但正式 provenance 仍无法单凭仓库精确重建。

### F-18：真实适配依赖 shim、私有字段和多份拓扑真值

真实 runtime 需要 `RealWorld`、`NoopInjector`、动态 `adapter.world`，并把 mock 专用字段设为 `None`：

- [`real_runtime.py` L41-L85](../../phase_b/real_runtime.py#L41-L85)

图与 Registry 还直接读取 world/adapter 私有状态；真实 runtime 用 `default_map()`，adapter 另读 YAML。

**建议**：定义 `Clock / WorldState / Injector / Adapter` Protocol；同一个加载后的 TopoMap 注入所有层；启动时校验 map hash。

### F-19：阈值时间尺度与契约文档不一致

契约说停滞与超时阈值归 adapter 配置：

- [`ADAPTER_CONTRACT.md` L38-L41](../../docs/ADAPTER_CONTRACT.md#L38-L41)

实际 graph 使用全局常量；真实 adapter 又有自己的 `tick_seconds`：

- [`runtime.py` L28-L31](../../embodied_agent/runtime.py#L28-L31)
- [`graph.py` L245-L257](../../embodied_agent/graph.py#L245-L257)

建议统一使用 duration，避免把轮询次数、虚拟 tick 和墙钟秒混在同一策略里。

### F-20：Quick Start 把会覆盖证据的完整评测放在普通体验路径

README 将 demo、pytest、viewer 与 `run_eval.py` 放在同一块；后者会重写 `runs/` 和 `RESULTS.md`。

**建议**：拆成“60 秒体验 / 可选验证 / 会生成产物的正式评测”；完整评测默认写未跟踪目录并允许独立 `--results-out`。

## P3 / 已知限制

- Viewer 硬编码拓扑并平铺 90 个 run，缺少 curated presets、环境 badge、空/错误状态和分享 URL。
- 真实 MCAP audit artifact 未入库；可放 GitHub Release 并发布 SHA256，而不必塞进 Git。
- 两个 README GIF 合计约 10.34 MiB。未经明确确认，不应替换、重压、移动或删除现有首屏 `demo.gif` 及其 embed 块；任何性能优化都应先单独确认并保持视觉等价。
- observer 在单个 LangGraph node 内运行长循环，LangGraph 目前更像外层路由；后续可拆成一次 poll + 纯 transition reducer。
- restricted + low-battery 双闸只支持单 token，README 已明确选择保持阻断，本评审不把它列为新 bug。
