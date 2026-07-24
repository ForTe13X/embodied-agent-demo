# Phase D:安全监管一个 learned policy 的仿真运行时

> 一句话:把一个**会吐越界/NaN/抖动/陈旧动作的 learned policy**,变成可**异步执行、确定性约束、
> 可取消、事后可审计**的机器人 skill —— 纯仿真、mock policy、**不训练、不碰真机**。
> 这是 [docs/POSITIONING.md](../docs/POSITIONING.md) 路线图里 Phase D 的落地:证明岗位核心价值
> (把不稳定 policy 变得可调用/可约束/可恢复),而这一层**不需要硬件**就能证。

## 为什么这么做(对照 review)

VLA 是挂在编排层下面的**一类 learned skill**,不是整个系统。它的输出是概率性的、可能越界/过期。
真正的工程价值是**运行时**:让这类 policy 能被安全监管。本目录用一个 mock policy 当"对手",
把运行时的每条保证做成可跑、可测的事实。

## 组件

| 文件 | 职责 |
|---|---|
| [`action_types.py`](action_types.py) | relative-EEF `Action`(policy 原始输出)vs `SafeAction`(可执行)。**`SafeAction` 只能由 shield 用私有令牌铸造** —— policy 结构上绕不过安全投影(复刻 Phase B 的 no-velocity-interface 断言) |
| [`safety_shield.py`](safety_shield.py) | **独立于模型**的确定性 action projection:workspace box / 单步限幅 / gripper 夹取;NaN·荒谬幅度·状态越界 → `must_stop`。不看模型 confidence,是最后确定性边界(review 局限 #7) |
| [`mock_vla_policy.py`](mock_vla_policy.py) | 桌面 pick 玩具任务的 mock VLA,吐 action chunk;确定性对抗开关(越界/NaN/抖动) |
| [`vla_skill_runtime.py`](vla_skill_runtime.py) | 异步 chunk 运行时:inference∥execution、**过期 chunk 丢弃**、**queue 空→hold**(不复用旧动作)、cancel 使在飞结果失效、no-progress/timeout 兜底、每步过 shield、全程写事件日志 |
| [`tabletop_sim.py`](tabletop_sim.py) | 运动学-only sim + 控制器(`send()` **只收 `SafeAction`**) |

## 跑

```powershell
.\.venv\Scripts\python -m pytest phase_d -q      # 49 项:安全核心 + 契约 + 异步 skill server + 复合任务 + graph 集成
.\.venv\Scripts\python phase_d\demo.py            # 6 场景汇总(含对抗)
```

`demo.py` 输出(实测):

| 场景 | 成功 | 终态 | 安全夹取 | 过期丢弃 | 步数 | 末端在盒内 |
|---|---|---|---|---|---|---|
| nominal | 是 | grasped | 0 | 0 | 23 | ✓ |
| out_of_bounds | 否 | must_stop:translation_hard_stop | 0 | 0 | 2 | ✓ |
| nan | 否 | must_stop:non_finite_action | 0 | 0 | 2 | ✓ |
| jitter | 是 | grasped | 68 | 0 | 69 | ✓ |
| stale(高延迟) | 否 | timeout | 0 | 5 | 0 | ✓ |
| cancel | 否 | canceled | 0 | 0 | 5 | ✓ |

**关键不变量:无论 policy 多离谱,末端永远在 workspace 内 —— 沿 runtime 执行路径,policy 绕不过安全投影。**

## 诚实边界

- **不是真 VLA**:mock policy 是确定性桩,不训练、不看图像;真实 VLA 换到 `predict_chunk` 同签名即可接。
- **不是物理仿真**:运动学 sim,不模拟接触力/摩擦/碰撞动力学。
- 价值在 **runtime / 安全集成 / 可审计**,不是操作物理或模型能力。详见 [docs/POSITIONING.md](../docs/POSITIONING.md)。
- **集成契约:D1/D2 已闭合**(PR #16/#17;下列曾经的缺口逐条已补,**实现文件可核对**):
  - ✅ 异步 goal-handle:[`skill_server.py`](skill_server.py) + 四工具 `execute_vla_skill` /
    `get_skill_feedback` / `cancel_skill` / `get_skill_result`(与 `navigate_to` 同构,在飞可取消);
    policy 推理走 `asyncio.to_thread`,不阻塞事件循环。
  - ✅ 独立后置校验:`verify_skill_postcondition` 回读 sim 末态,不采信 skill 自报,并记
    `agrees_with_skill`(自报与实测背离本身是审计信号)。
  - ✅ composite 并入**正式 LangGraph graph**:[`graph.py`](../embodied_agent/graph.py) 的
    `vla_skill` 步 + observer 轮询 + `SKILL_UNSAFE`/`SKILL_FAILED` 恢复归属。
  - ✅ horizon / freshness 语义:[`policy_contract.py`](policy_contract.py) 钉死 action/execution
    horizon 与**逐动作**新鲜度(交付预算 vs 执行预算分开)。
  - ✅ ROS 2 `ExecuteVLASkill` Action:[`ros2/`](ros2/),容器内 colcon 构建 + 四场景 smoke 实测通过。
- **仍然成立的边界(**不冒充已解决**)**:
  - `SafeAction` 的令牌加固只关掉了"一行 `import` 就能伪造"这个洞(令牌改每 shield 实例私有);
    同进程 Python **仍不存在不可绕过的边界** —— 这是**类型级约定**,不是安全隔离,真正不可绕需**进程隔离**。
  - 宿主 registry 的 `execute_vla_skill` 接的仍是 **in-process** `SkillServer`;切到 ROS 2 ActionClient
    是 adapter 层的后续接线。
  - **skill 执行期间不推进虚拟世界时钟**(mock 世界建模导航、无操作能耗模型)⇒ 操作过程中电量不衰减、
    故障注入不触发、低电量抢占不会发生。这是 mock 语义边界,不是真实机器人行为。

## Phase D-2:端到端复合任务(已完成)

`execute_vla_skill` 已注册成**一个 skill**接进现有 Tool Registry + 共享事件日志(同一门禁路径,
上层只见 running/progress/fault)。复合任务`去工作台 → VLA 抓取 → 校验 → 归坞`跑通,3 条件预注册
命中,recovery 由 Skill Supervisor 按 [恢复职责矩阵](../docs/RECOVERY_OWNERSHIP.md) 路由:

| 条件 | 终态 | skill 归属 |
|---|---|---|
| baseline | completed_full | succeeded |
| unsafe | degraded_complete | aborted_unsafe(安全停,不重试) |
| unreachable | degraded_complete | escalated(重试 2 次后上浮) |

```powershell
.\.venv\Scripts\python phase_d\composite_mission.py baseline     # 单条件跑
.\.venv\Scripts\python phase_d\run_composite_eval.py             # 3 条件预注册评测 + 审计日志
.\.venv\Scripts\python -m pytest phase_d -q                       # 全部 49 项
```

完整结果与逐条解读:[PHASE_D_RESULTS.md](PHASE_D_RESULTS.md)。

## D1/D2(已完成,**不需硬件**)

"可管理、可停止的 skill 边界"已落地:版本化 Policy Contract → 异步 goal/feedback/cancel/result
(含 ROS 2 `ExecuteVLASkill` Action,容器内实测)→ shield 令牌加固 + 独立 postcheck +
horizon/freshness 语义 → composite 并入正式 LangGraph graph。逐条实现见上面「诚实边界」。

## 再下一步(**需硬件**,Phase E+)

真臂 + 遥操作数据 + SmolVLA/ACT 微调 + 真实闭环 eval + intervention 数据飞轮;真实 VLM 感知进
控制闭环。属未来,不在当前范围(见 [docs/POSITIONING.md](../docs/POSITIONING.md) 路线图)。
