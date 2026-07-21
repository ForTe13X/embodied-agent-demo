# 恢复职责矩阵(Recovery Ownership Matrix)

> 一句话:**每一类故障都应该有且只有一个"主责层"去恢复;最忌讳的是导航层、编排层、
> learned policy 三层同时抢救同一个错误,互相打架。** 这份文件把"哪层负责哪种恢复"写成
> 可核对的表,并把 Phase C 实测撞出来的证据钉进去。

## 0. 为什么需要这张表(不是空谈,是实测撞出来的)

Phase C 把同一套编排从 mock 底盘搬到真实 ROS 2 / Nav2,`nav_blocked`(路径受阻)这一条
暴露了一个真问题:**"谁来恢复"取决于底盘,而不是取决于编排层的意愿。**

| 底盘 | `nav_blocked` 的实际恢复者 | 编排层看到 fault 了吗? |
|---|---|---|
| mock(不自 replan) | 编排层:observer 停滞水位 → `replan_avoid_edge` | 看到了(检出=1) |
| 真实 Nav2(重规划 BT) | **导航层自己**在 nav 层就地改道 | **没看到**(检出=0,见 [PHASE_C_RESULTS.md](../phase_c/PHASE_C_RESULTS.md)) |

这不是 bug——是"重规划住在哪一层"的**层级归属**问题。如果不把它显式定死,换一个更强的底盘
(或以后挂上会自我纠正的 VLA)时,就会出现两三层都以为自己该负责、同时动手的竞态。
**这张矩阵就是把归属从"隐式、随底盘漂移"变成"显式、可审计"。**

## 1. 归属原则(四条)

1. **就近原则**:能在最低层稳定解决的,不要上浮。局部绕障归导航层,别惊动编排层。
2. **单一主责**:每类故障一个 primary owner;其余层只做"观测 + 兜底",不主动抢救。
3. **语义上浮**:低层**能力边界内解决不了**的(目标语义不可达、任务级降级),才上浮到编排层。
4. **安全独立**:物理安全(碰撞、超限、急停)由**独立于 Agent/VLA/编排**的确定性 supervisor 负责,
   它是最后边界,永远不被模型或 LangGraph 决定。

## 2. 矩阵

`状态`:✅ 已实现并实测 · 🟡 已实现(mock/sim)· ⬜ 路线图(未实现)

| 故障 | 主责层 | 恢复动作 | 本 repo 现状 |
|---|---|---|---|
| 局部障碍物 / 动态绕行 | 导航层(Nav2 local planner + BT) | costmap 重规划、就地改道 | ✅ Phase C 实测:真实 Nav2 自己消化(检出=0) |
| 全局路径临时变化 | 导航层(Nav2 planner) | 全局重规划 | ✅ 同上 |
| **目标语义不可达**(隔离节点) | **编排层(Mission Executive)** | 查预注册表 → 替代观测点 `a3_alt` | ✅ Phase C mock/real 同机制同结果 |
| 替代巡检点选择 / 任务降级 | **编排层** | `substitute_target` → `degraded_report` | ✅ `recovery.py` NAV_UNREACHABLE 链 |
| 底盘停滞(不自 replan 的底盘) | **编排层** | 停滞水位 → `retry_same_route` → `replan_avoid_edge` | ✅ mock + Day3-D no-replan BT 实测 |
| 低电量抢占 | 编排层 / 机器人安全(安全类优先) | `dock_recharge_resume`,抢占任务类 | 🟡 mock-only(loopback 无电量模型) |
| 禁区 / 权限(**目标节点** access) | 确定性策略层(Tool Registry) | 门禁拦截,token 也不放行 forbidden | ✅ mock + real 同拦(门禁在 adapter 之上) |
| **过境访问违规**(轨迹闯入未授权受限/禁入区) | **运行期访问围栏(adapter 内置,注册表之下)** | 盯实际位置流 → 踏入即取消目标、安全停、上浮 `transit_violation` | 🟡 mock 强制端到端实测 + 纯逻辑单测;真实 `RclpyAdapter` 同源接入,复验需容器(F-01)。彻底预防(costmap keepout)仍属路线图 |
| 传感器降级 | 编排层 | `skip_step_degraded` → `pause_and_escalate` | 🟡 mock-only |
| 工具超时 / 畸形 | 注册表 + 编排层 | 幂等重试 → 熔断 → `failure_report_and_degrade` | 🟡 mock-only |
| **抓取姿态微调 / 局部视觉纠正** | **VLA / manipulation skill** | policy 闭环内自纠(chunk 重预测) | ⬜ 路线图(Phase D 仿真 runtime) |
| **连续抓取失败** | **Skill Supervisor(编排层之下、skill 之上)** | 重观测 → 重试 N 次 → 上浮编排层 | ⬜ 路线图 |
| **电机过流 / 碰撞 / 关节超限** | **独立硬件安全 supervisor** | action projection / 急停,**独立于模型** | ⬜ 路线图(局限 #7) |

> 现状小结:本 repo 已经实测的是**上两层**(导航层 ⇄ 编排层)的归属划分,并且证明了它随底盘
> 正确切换。下三行(VLA skill / Skill Supervisor / 硬件安全)是 learned-skill 接入后的归属,
> 属路线图,尚未实现——**如实标注,不冒充。**

## 3. 反模式(明确禁止)

- **三层同时抢救**:Nav2 在改道、编排层同时 `avoid_edge`、VLA 又在自纠——同一个错误被三层
  并发处理,状态互相覆盖。→ 用本表的"单一主责"消除。
- **低层静默吞掉需要上浮的故障**:Nav2 无限重规划却从不报"这个目标根本进不去",编排层永远
  等不到 fault、任务挂死。→ 低层要有"我这层解决不了"的显式上浮信号(超时 / 重试上限 / error_code)。
- **模型当最后安全边界**:让 VLA 的 confidence 或 LangGraph 的判断决定"要不要急停"。→ 物理安全
  必须是独立确定性层(§1 第 4 条)。

## 4. 落到代码怎么强制

- 声明式记录:恢复链在 [`recovery.py`](../embodied_agent/recovery.py) `RECOVERY_CHAINS`,
  与 `faults.yaml` 的 `expected_recovery_chain` 一致,进事件日志供审计。
- 归属边界:`exception_manager` 只处理**上浮到编排层的** fault;导航层自己消化的(如真实 Nav2
  改道)编排层根本收不到——**这正是"就近原则"在起作用的证据,而非漏检**。
- 下一步(Phase D):新增一个 `recovery_router`,在故障进入编排恢复链之前先判"这该不该由我这层
  管";learned-skill 的 fault(抓取失败等)先给 Skill Supervisor,超出其能力再上浮。见
  [POSITIONING.md](POSITIONING.md) 的路线图。
