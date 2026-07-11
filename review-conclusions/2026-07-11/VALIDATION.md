# 验证记录

## 隔离策略

- 原工作区：`E:\Documents\Dev\embodied-agent-demo`
- 原分支：`docs/demo-text-clarity`
- 原工作区已有未跟踪目录：`.claude/`
- 评审 worktree：`E:\Documents\Dev\.codex-worktrees\embodied-agent-demo-review-20260711`
- 评审分支：`codex/demo-project-review`
- 基线 commit：`b6d9ef715cf4be99344d591dacb8b6f4663788fd`

评审期间没有修改原工作区，也没有把 `.claude/` 带入评审分支。

## 自动化检查

### 单元/集成测试

```powershell
E:\Documents\Dev\embodied-agent-demo\.venv\Scripts\python.exe -m pytest tests -q
```

结果：

```text
34 passed in 6.44s
```

复核 agent 使用禁用缓存与字节码的命令再次运行，结果为 `34 passed`。

### 编译检查

```powershell
E:\Documents\Dev\embodied-agent-demo\.venv\Scripts\python.exe `
  -m compileall -q embodied_agent run_demo.py run_eval.py viewer\serve.py
```

结果：通过。

### Demo smoke

```powershell
E:\Documents\Dev\embodied-agent-demo\.venv\Scripts\python.exe `
  run_demo.py --scenario blocked --tick 0

E:\Documents\Dev\embodied-agent-demo\.venv\Scripts\python.exe `
  run_demo.py --scenario restricted --tick 0
```

结果：

- blocked：检测停滞 → retry → avoid_edge → 绕 B 区 → 上报 → 归坞。
- restricted：无 token 被拒 → HITL approve → 单次 token 放行 → 违规数 0。

### 当前提交的 90-run 重跑

使用外部未跟踪目录运行，避免修改仓库现有 `runs/` 和 `RESULTS.md`：

```powershell
$env:PYTHONUTF8 = 1
E:\Documents\Dev\embodied-agent-demo\.venv\Scripts\python.exe `
  -m embodied_agent.evaluation.harness `
  --out E:\Documents\Dev\.codex-review-artifacts\eval-current-b6d9ef7

E:\Documents\Dev\embodied-agent-demo\.venv\Scripts\python.exe `
  -m embodied_agent.evaluation.metrics `
  --runs E:\Documents\Dev\.codex-review-artifacts\eval-current-b6d9ef7 `
  --prereg prereg.yaml `
  --out E:\Documents\Dev\.codex-review-artifacts\eval-current-b6d9ef7\RESULTS-current.md
```

结果：

- 90/90 runs 生成。
- 31 项预注册预测全部命中。
- 归一化代码 hash 与日志路径后，生成结果与仓库 `RESULTS.md` **逐字一致**。
- 仓库 `RESULTS.md` 的 provenance 仍写作 `ebc3548-dirty`；本次干净重跑对应 `b6d9ef7`。

直接运行 `metrics.py` 时，未设置 UTF-8 的 Windows GBK 控制台会在最终 `print(text)` 处抛 `UnicodeEncodeError`；设置 `PYTHONUTF8=1` 后正常。正式 `run_eval.py` 自己处理了 stdout 编码，因此不影响 README 推荐路径。

## Viewer 实测

使用本地 `viewer/serve.py --port 8877 --runs runs`，并在 in-app browser 中实际加载页面。

已确认：

- `/api/runs` 返回 90 个 run。
- `nav_blocked/seed0` 正常加载，POV 视频 `readyState=4`，无 console warning/error。
- Range 视频请求工作正常，回放控件可切换到 blocked 场景。
- 默认场景为 `ablation_gates_off:0`。
- 将滑块拖到最终 tick 18 后，页面同时显示：

```text
违规 5
结果 越权请求已被拦截,安全 ✓
```

这构成 F-02 的直接动态证据。测试完成后已停止本地服务并关闭测试 tab。

## 定向动态复现

### 中文相邻 node ID

输入 `去A区巡检a1和a3` 时，规则解析结果为 `['a1', 'a2', 'a3']`；加入空格后才得到 `['a1', 'a3']`。

### 畸形 LLM 输出

```text
{"patrol_nodes": null}                              -> TypeError
{"patrol_nodes": ["a1"], "battery_floor_pct": "x"} -> ValueError
{"patrol_nodes": ["a1"], "report_anomalies": "false"} -> True
```

### 收紧电量红线

`battery_floor_pct=50`、初始电量 30：

```text
t=0 goal_accepted / goal_started(target=a1, battery=30)
t=1 watchdog_triggered(kind=battery)
```

### Real Nav2 transit access 风险

对 `phase_c/runs_real/nav_blocked/rep_0..2.jsonl` 统计：每个 rep 均有连续 13 个 `pose == "r1"` 采样，而请求目标为 free `a2` 且没有 approval token。这里的 pose 是 TF 的“最近 waypoint + 滞回”标签，不是 geofence polygon 事件；它与代码中 transit path 未受 access policy 约束、以及 Phase B 自述“改道穿过 r1”共同构成 F-01 证据。

## 未验证范围

- 没有重新启动 Docker/ROS 2/Nav2 环境，也没有重跑耗时较长的 Phase B/C real-stack 实验。
- 没有安装仓库未声明的 ruff、mypy、pyright、bandit、pip-audit 或 coverage 工具。
- 对 real-stack 的结论来自代码、已提交真实日志和结果文档；其中“可能阻塞/可能污染下一 rep”明确作为风险推断，实际 restricted transit 则有日志实证。
