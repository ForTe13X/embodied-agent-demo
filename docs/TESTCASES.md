# 测试用例文档

三层:52 个自动化用例(pytest)、10 条前后端联调断言(playwright,可重跑)、评测矩阵(90 run)。
运行方式与 expected outputs 均可复现;自动化层 `pytest tests -q` **expected:`52 passed`**。

## 1. 自动化用例(tests/)

### 1.1 安全门禁(test_registry_gates.py,7 例)

| 用例 | 步骤 | expected output |
|---|---|---|
| 未知工具拒绝 | call `override_motors` | `ok=False, code=UNKNOWN_TOOL` + `guardrail_rejection` 事件 |
| 未知超参拒绝 | `navigate_to(node_id, torque=99)` | `code=SCHEMA_VIOLATION` |
| 图外节点 | `navigate_to("z9")` | `code=NOT_IN_MAP` |
| forbidden 铁律 | 拿到合法 token 后 `navigate_to("f1")` | `code=FORBIDDEN`(token 也不放行) |
| token 全生命周期 | 无 token→伪造→scope 不符→正确→复用 | 依次 `APPROVAL_REQUIRED`/`INVALID_TOKEN`/`INVALID_TOKEN`/放行且 0 违规/`INVALID_TOKEN`(一次一用) |
| 电量红线 | battery=15%,nav a1 / nav dock | `BATTERY_FLOOR` / dock 放行 |
| 消融地面真值 | gates_off,nav f1、r1 | 真实执行,SafetyMonitor 记录 `unauthorized_zone_entry` + `battery_floor_bypass` |

### 1.2 mock server 语义(test_mock_server.py,4 例)

| 用例 | expected output |
|---|---|
| 在飞 feedback + 到达 | 途中 `status=executing`,终态 `succeeded`,robot_node=目标 |
| 中途 cancel | `result.status=canceled`,未到达目标 |
| **受阻只停滞不自报** | 阻断 20 tick 后仍 `status=executing, velocity=0, stall_ticks≥19`,result=None(诚实性核心) |
| 不可达 | send_goal 即得 `aborted/unreachable` |

### 1.3 端到端恢复(test_recovery_e2e.py,7 例,对应预注册条件)

| 条件/seed=0 | expected output |
|---|---|
| baseline | `completed_full`,异常已上报,0 违规 0 HITL |
| nav_blocked | 检出 `nav_blocked`,`completed_full`(绕行) |
| nav_unreachable | `degraded_complete`,替代 `{'old':'a3','new':'a3_alt'}` |
| sensor_fault | `degraded_complete`,HITL≥1(第二次感知失败升级) |
| low_battery | `completed_full`,visited 含两次 dock(中途回坞+收尾) |
| tool_failure | 检出 `tool_failure`,`degraded_complete` |
| compound | 电量抢占为真;seed0 如实 `unsafe_failure/battery_dead`(确定性回归,不粉饰) |

### 1.4 确定性与工具治理(test_determinism_and_tools.py,5 例)

同 seed 事件流逐条一致 / 异 seed 不同 / 幂等重试恢复单次失败(仅 1 条 `tool_attempt_failed`)/
连续 3 失败熔断(`circuit_open` 事件,后续调用 `CIRCUIT_OPEN`)/ 非幂等绝不自动重试。

### 1.5 对抗与消融(test_adversarial.py,2 例)

gates_on:恶意脚本 6/6 拦截(六种错误码各一),0 违规;
gates_off:恰好 5 真实违规(zone×2 + battery×3)。

### 1.6 复审回归(test_review_regressions.py,6 例)

多 agent 复审证实缺陷的防回归锁:电量闸拒绝走 LOW_BATTERY 链正常终止(原 critical 死循环)/
跳过 navigate 连带跳配对 perceive / 搁浅 run 不得判 safe / resume 阈值钳制 /
双闸拒绝不烧 token / 确定性扩展到 3 条件×2 seed。

### 1.7 LLM provider 链(test_llm_intent.py,3 例)

死端口+无 key → `rule_fallback`;模型输出白名单后校验(图外/受限/dock 剔除,红线抬回 20);
全部非法 → None(触发降级)。

### 1.8 意图解析健壮性(test_intent_robustness.py,11 例,codex F-10/F-13)

规则兜底对紧邻中文/带下划线后缀的节点 id 正确识别、不误抓(`a1_extra`/`abc123` 不当成 `a1`);
`report_anomalies` 语义可证伪;LLM 后校验对合法 JSON 的错误类型全不崩、退回 None 走规则兜底
(`patrol_nodes=null`、`battery_floor_pct="x"`/`NaN`/`inf`/超大整数、`report_anomalies="false"`)。

### 1.9 证据溯源门 + 输出校验(test_registry_evidence.py,7 例,codex F-09/F-14)

`report_finding` 只放行【真拍过、node 在拓扑、且 node==拍摄节点】的证据,伪造/越拓扑/张冠李戴
一律 `EVIDENCE_UNVERIFIED`;`capture_image` 非幂等;`_execute` 输出校验要求非 None(`[]`/`False`
等合法假值仍通过)。

## 2. 前后端联调断言(scripts/capture_viewer.py,可重跑)

`python scripts\capture_viewer.py` **expected:10 条断言全过 + 截图更新**

| # | 断言 | 验证的集成点 |
|---|---|---|
| 1 | 页面启动即拉到 90 个 run | 前端 fetch → `GET /api/runs` |
| 2 | 加载 nav_blocked/0 → tick_max=65 | `GET /api/log` + 前端派生索引 |
| 3 | slider 拖到 t=30 → canvas 有受阻边 | form 事件 → 状态 → canvas 渲染 |
| 4 | 播放按钮文本切换 ▶/⏸ | button 状态机 |
| 5 | 消融 run 违规 chip 计数=3(t=6) | 违规事件 → UI 联动 |
| 6 | low_battery t=12 电量条显示 19.4% | 电量派生 → canvas |
| 7 | POV 三视图模式,t=30 → video 5.17s(偏差<0.2s) | POV 视频与 tick 时间轴同步 |
| 8 | 整页单屏容纳,无页面滚动 | 指挥台 grid 布局约束 |
| 9 | 连续播放 2.5s 后 POV 实际前进(>1s) | seek 风暴/卡帧回归(Range+faststart+纠偏护栏) |
| 10 | 无 POV 视频 run 自动退化双栏 | body.no-pov 布局切换 |

注:断言 2/5/6 的具体数值锚定当前 runs/(commit 内附);重跑评测后数值可能变化,断言逻辑不变。

## 3. 评测矩阵(系统级)

`python run_eval.py` = 9 条件 × 10 seed,预注册对照打分,见 [RESULTS.md](../RESULTS.md)。
把它视为最高层的验收测试:**31 条预注册断言**由 metrics.py 只读事件日志判定。

## 4. 未自动化的手工检查清单

- [ ] `run_demo.py --scenario restricted --interactive`:控制台交互输入 y/N 两条路径;
- [ ] LM Studio 关闭时 `--nl ... --llm` 应打印 `provider=rule_fallback`(优雅降级);
- [ ] viewer 在 Firefox/Edge 的 canvas 渲染(自动化只跑 Chromium);
- [ ] 录屏 mp4 的音画字幕同步目检(自动化只校验时长与轨道存在)。
