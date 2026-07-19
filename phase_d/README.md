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
.\.venv\Scripts\python -m pytest phase_d -q      # 14 项:安全核心 + 异步运行时
.\.venv\Scripts\python phase_d\demo.py            # 6 场景汇总(含对抗)
```

`demo.py` 输出(实测):

| 场景 | 成功 | 终态 | 安全夹取 | 过期丢弃 | 末端在盒内 |
|---|---|---|---|---|---|
| nominal | 是 | grasped | 0 | 0 | ✓ |
| out_of_bounds | 否 | must_stop:translation_hard_stop | 0 | 0 | ✓ |
| nan | 否 | must_stop:non_finite_action | 0 | 0 | ✓ |
| jitter | 是 | grasped | 68 | 0 | ✓ |
| stale(高延迟) | 否 | timeout | 0 | 4 | ✓ |
| cancel | 否 | canceled | 0 | 0 | ✓ |

**关键不变量:无论 policy 多离谱,末端永远在 workspace 内 —— policy 绕不过安全投影。**

## 诚实边界

- **不是真 VLA**:mock policy 是确定性桩,不训练、不看图像;真实 VLA 换到 `predict_chunk` 同签名即可接。
- **不是物理仿真**:运动学 sim,不模拟接触力/摩擦/碰撞动力学。
- 价值在 **runtime / 安全集成 / 可审计**,不是操作物理或模型能力。详见 [docs/POSITIONING.md](../docs/POSITIONING.md)。

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
.\.venv\Scripts\python -m pytest phase_d -q                       # 全部 18 项
```

完整结果与逐条解读:[PHASE_D_RESULTS.md](PHASE_D_RESULTS.md)。

## 再下一步(需硬件,Phase E+)

真臂 + 遥操作数据 + SmolVLA/ACT 微调 + 真实闭环 eval + intervention 数据飞轮;真实 VLM 感知进
控制闭环。属未来,不在当前范围(见 [docs/POSITIONING.md](../docs/POSITIONING.md) 路线图)。
