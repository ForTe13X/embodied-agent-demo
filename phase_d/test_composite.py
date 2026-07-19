"""Phase D-2 复合任务回归:同一编排壳子管 nav + VLA skill,恢复归属正确,全程一份事件日志。

跑法:.venv\\Scripts\\python -m pytest phase_d/test_composite.py -q
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from composite_mission import run_composite  # noqa: E402


def _run(condition, scenario, tmp_path):
    log_path = tmp_path / f"{scenario}.jsonl"
    res = asyncio.run(run_composite(condition, scenario, log_path=log_path))
    events = [json.loads(l) for l in log_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    return res, events


def _tool_calls(events, tool):
    return [e for e in events if e["event_type"] == "tool_call" and e["payload"].get("tool") == tool]


def test_baseline_completed_full(tmp_path):
    res, events = _run("c_baseline", "baseline", tmp_path)
    assert res["outcome"] == "completed_full"
    assert res["detail"]["skill"]["outcome"] == "succeeded"
    # 同一 registry:nav 和 VLA skill 都走 tool_call
    assert _tool_calls(events, "navigate_to")
    assert _tool_calls(events, "execute_vla_skill")
    assert _tool_calls(events, "return_to_dock")
    # VLA skill runtime 事件折进了共享日志
    assert any(e["actor"] == "vla_skill" for e in events)
    assert any(e["event_type"] == "vla_skill" or e["actor"] == "vla_skill" for e in events)


def test_unsafe_aborts_without_retry(tmp_path):
    res, events = _run("c_unsafe", "unsafe", tmp_path)
    assert res["outcome"] == "degraded_complete"       # 操作安全放弃,仍安全归坞
    sk = res["detail"]["skill"]
    assert sk["outcome"] == "aborted_unsafe"
    assert sk["attempts"] == 1                          # 安全停:不重试
    # 供应商上浮事件在日志里
    assert any(e["event_type"] == "escalate_unsafe" for e in events)
    # emergency_stop 来自 shield/runtime
    assert any(e["event_type"] == "emergency_stop" for e in events)


def test_unreachable_retries_then_escalates(tmp_path):
    res, events = _run("c_unreach", "unreachable", tmp_path)
    assert res["outcome"] == "degraded_complete"
    sk = res["detail"]["skill"]
    assert sk["outcome"] == "escalated"
    assert sk["attempts"] == 3                          # 重试 2 次后上浮(共 3)
    assert len([e for e in events if e["event_type"] == "retry"]) == 2
    assert any(e["event_type"] == "escalate_exhausted" for e in events)


def test_all_conditions_return_to_dock_safely(tmp_path):
    for scen in ("baseline", "unsafe", "unreachable"):
        res, _ = _run(f"c_{scen}", scen, tmp_path)
        assert res["detail"]["at_dock"] is True         # 无论操作成败,都安全归坞
        assert res["outcome"] in ("completed_full", "degraded_complete")


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
