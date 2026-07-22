# Phase D-2 结果:端到端复合任务(同一编排壳子管 Nav + learned skill)

> 一句话:**同一个 Tool Registry + 同一份事件日志,既管 Nav(mock)又管 VLA learned skill;
> skill 失败时由 Skill Supervisor 按恢复职责矩阵路由(安全停不重试 / 无进展重试后上浮);
> 三种情况都安全归坞。** 证明"编排壳子对 learned skill 和经典 skill 一视同仁地约束/恢复/审计"。

任务:`去工作台(a1) → 用 VLA 抓起红方块 → 后置校验 → 归坞`。

## 预注册评测(3 条件,预期先于结果)

| 条件 | 终态 | skill 归属 | 尝试 | 归坞 | 命中预注册 |
|---|---|---|---|---|---|
| baseline | `completed_full` | succeeded(抓到,23 步) | 1 | 是 | ✓ |
| unsafe | `degraded_complete` | **aborted_unsafe** | 1 | 是 | ✓ |
| unreachable | `degraded_complete` | **escalated** | 3 | 是 | ✓ |

复现:`.venv\Scripts\python phase_d\run_composite_eval.py`(审计日志写 `phase_d/runs_composite/`)。
回归:`pytest phase_d/test_composite.py -q`(4 项)。

## 逐条解读(恢复归属矩阵在起作用)

- **baseline**:Nav 到 a1 → VLA 抓取成功 → 后置校验通过 → 归坞。完整闭环。
- **unsafe**:policy 冲界 → SafetyShield `must_stop`(`emergency_stop` 进日志)→ Skill Supervisor
  判"安全停=不可重试",**1 次尝试即上浮编排层**(`escalate_unsafe`)→ 跳过校验、安全归坞。
  这正是 [RECOVERY_OWNERSHIP.md](../docs/RECOVERY_OWNERSHIP.md) §1.4:**物理安全类不重试、独立上浮**。
- **unreachable**:目标够不到 → `no_progress` → Skill Supervisor **重试 2 次**(`retry`×2)仍失败 →
  上浮编排层(`escalate_exhausted`)→ 降级、安全归坞。对应矩阵"连续抓取失败 → Skill Supervisor"。

## 关键证据(在共享事件日志里可核对)

- **同一 registry**:`navigate_to` / `return_to_dock` / `execute_vla_skill` 都是同一条 `tool_call`
  路径(白名单 + schema `extra=forbid` + 熔断)。`execute_vla_skill` 是【一个 skill】,上层只见
  running/progress/fault,**不逐帧调 policy**(review §七)。
- **一份日志贯穿两类 skill**:VLA runtime 的 `vla_skill` 事件(safety_clamped / emergency_stop /
  skill_succeeded 等)折进了和 Nav 事件同一份 append-only 日志 → 整条复合任务可回放、可审计。
- **沿 runtime 执行路径,learned policy 绕不过安全投影**:unsafe 条件里 policy 想冲界,末端从未离开
  workspace(见 [safety_shield.py](safety_shield.py) + [test_safety_shield.py](test_safety_shield.py))。
  边界见下:这是**类型级同进程约定**,不是不可绕过的隔离。

## 诚实边界

- **Nav 是 mock server**(真实 ROS 2 / Nav2 见 [Phase B/C](../phase_c/PHASE_C_RESULTS.md));D-2 证的是
  **skill 组合 + 恢复归属 + 共享审计**,不是又跑一遍真实 Nav2。把 mock adapter 换成 RclpyAdapter
  即在真实 Nav2 上跑同一条复合任务(接口不变,Phase B 已证可换)。
- **VLA 是 mock policy + 运动学 sim**,不训练、不碰真机。
  价值在 runtime / 安全集成 / 可审计。详见 [docs/POSITIONING.md](../docs/POSITIONING.md) 与 [README](README.md)。
- **后置校验已改为独立观测(D1 已闭合)**:`verify_skill_postcondition` / `_independent_postcheck`
  直接回读 sim 末态判定,**不采信 skill 自报的 success**,并记 `skill_reported_success` 与
  `agrees_with_skill` —— 自报与实测背离本身是高价值审计信号。
- **集成契约 D1/D2 已闭合**:异步 goal-handle 四工具(在飞可取消)、composite 并入**正式
  LangGraph graph**、版本化 Policy Contract、ROS 2 `ExecuteVLASkill` Action(容器内实测)。
  仍成立的边界见 [README 诚实边界](README.md)(令牌为类型级约定、skill 期间不推进世界时钟等)。

## 意义

至此,"同一编排壳子既管 Nav2 又管 learned skill"从口号变成**可跑、可测、预注册命中**的事实:
LLM/planner 只出高层意图与 skill 调用;**安全(SafetyShield)、恢复归属(Skill Supervisor + 矩阵)、
审计(事件日志)全部是确定性内核**——正是这个岗位把"benchmark 上会输出动作的模型"变成
"产品里可被调度、约束、暂停、恢复、审计的机器人能力"的那层。
