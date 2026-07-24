# 评审验证记录

## 隔离策略

- 原工作区：`E:\Documents\Dev\embodied-agent-demo`
- 创建隔离 worktree 时的原工作区快照：`fix/codex-review-p1`，且有未跟踪 `.claude/`
- 本评审 worktree：`E:\Documents\Dev\.codex-worktrees\embodied-agent-demo-reference-review-20260719`
- 本评审分支：`codex/reference-alignment-review`
- 本评审基线：`origin/main@8dd61a3393466256da427541e261c2ca39f25f93`

评审期间其他进程继续切换和修改原工作区；这正是本次固定 `origin/main@8dd61a3` 隔离基线的原因。本次只新增 `review-conclusions/2026-07-19-reference-alignment/`，未读取或纳入原工作区的未跟踪内容，未修改原工作区、根 README、`demo.gif`、实现、测试或既有运行产物。

## 证据范围

### 已静态检查

- 参考文档的目标分层、VLA skill contract、action chunks、Safety Shield、数据/训练/HIL 路线。
- `phase_d/action_types.py`、`safety_shield.py`、`mock_vla_policy.py`、`vla_skill_runtime.py`、`vla_skill_tool.py`、`skill_supervisor.py`、`composite_mission.py` 与相应 tests/results。
- `docs/PRODUCT.md`、`docs/POSITIONING.md`、`docs/RECOVERY_OWNERSHIP.md` 的 Phase D 状态一致性。
- Phase A-C 既有评审结论与当前开放 PR #10 的范围；PR #10 未进入本评审基线。

### 定向动态检查

以下命令均在隔离 worktree、禁用 pytest cache 与 `.pyc` 写入的条件下执行：

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'

# Root regression suite
E:\Documents\Dev\embodied-agent-demo\.venv\Scripts\python.exe `
  -m pytest tests -q -p no:cacheprovider

# Phase D regression suite
E:\Documents\Dev\embodied-agent-demo\.venv\Scripts\python.exe `
  -m pytest phase_d -q -p no:cacheprovider

# SafeAction token boundary probe
E:\Documents\Dev\embodied-agent-demo\.venv\Scripts\python.exe `
  -c "import sys; ...; from action_types import SafeAction, _SHIELD_TOKEN; ..."
```

结果：

```text
tests/   : 34 passed in 5.79s
phase_d/ : 18 passed in 18.27s
probe    : forged=SafeAction
```

回归测试确认当前基线行为自洽；探针同时证明，外部调用者只要能导入模块，就可以导入 `_SHIELD_TOKEN` 并构造 `SafeAction`。因此本评审将它判定为 API convention / claim blocker，而不是已实现的隔离安全边界。

## 关键静态证据

1. `_SHIELD_TOKEN` 与 `_mint_safe` 位于模块级，测试也直接导入令牌，因此“不可绕过”只能视为 API convention。
2. Registry handler 等待 `rt.execute()` 终态才返回，因此 skill 的内部 asyncio loop 不等价于 Mission Executive 可管理的异步 Action。
3. `_infer()` 直接调用同步 predictor；真实 GPU/网络 inference 会阻塞同一 event loop。
4. `execution_horizon` 实际用作 queue low-watermark，完整 chunk 被 append；这与参考文档的 receding-horizon execution 不等价。
5. Observation sequence 每 runtime loop 增加，不对应同步 sensor frame；stale demo 不能外推为真实多传感器 freshness 方案。
6. postcheck 从 skill result 推导 `verified=True`，没有独立 world/sensor observation。
7. Phase D-2 复用了 Registry/runtime/log，但 composite 是 procedural sequence，并非主 LangGraph graph。

## 外部事实核验

只采用项目/机构官方来源：

- OpenVLA：官方页面的模型规模、训练资源、LoRA 与 Diffusion Policy 对比。
- OpenVLA-OFT：官方页面的 parallel decoding、action chunking 与 speed/success 改进。
- SmolVLA：Hugging Face 官方文章的 450M、continuous chunks 与 async inference。
- LeRobot：官方 HIL 与 Dataset v3 文档。
- GR00T / openpi：官方仓库中的 action/state mapping、normalization、policy server 与 action-chunk workflow。

这些资料只用于校准实施方案，不作为当前仓库已经具备相应能力的证据。

## 未验证范围

- 未启动 Docker/ROS2/Nav2，不重跑 Phase B/C real-stack 实验。
- 未运行真实 VLA checkpoint、GPU inference、camera pipeline 或机械臂。
- 未进行碰撞、力矩、急停、controller tracking 或实时性能验证。
- 未把 PR #10 的未合并代码当作 `main` 的已完成能力。
- 本评审不修改实现，因此定向探针用于验证 claim，不代表修复已完成。
