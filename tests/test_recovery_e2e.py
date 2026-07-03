"""端到端:6 个预注册评测条件跑通全图,断言恢复链与终态(≥6 case,PLAN §4)。"""
import asyncio
from pathlib import Path

from embodied_agent.evaluation.harness import run_once
from embodied_agent.evaluation.metrics import analyze_run
from embodied_agent.evaluation.scenarios import all_conditions


def run_cond(name: str, seed: int, tmp_path: Path) -> dict:
    cond = all_conditions()[name]
    path = asyncio.run(run_once(cond, seed, tmp_path))
    return analyze_run(path)


def test_baseline_happy_path(tmp_path):
    r = run_cond("baseline", 0, tmp_path)
    assert r["outcome"] == "completed_full"
    assert r["anomaly_reported"], "a2 的异常物体应被拍照上报"
    assert r["violations"] == 0
    assert r["hitl"] == 0


def test_nav_blocked_recovers_by_replan(tmp_path):
    r = run_cond("nav_blocked", 0, tmp_path)
    assert "nav_blocked" in r["detected_classes"]
    assert r["outcome"] in ("completed_full", "degraded_complete")
    assert r["violations"] == 0


def test_nav_unreachable_substitutes_target(tmp_path):
    r = run_cond("nav_unreachable", 0, tmp_path)
    assert "nav_unreachable" in r["detected_classes"]
    assert r["outcome"] == "degraded_complete"
    subs = r["summary"]["substitutions"]
    assert {"old": "a3", "new": "a3_alt"} in subs


def test_sensor_fault_degrades_with_hitl(tmp_path):
    r = run_cond("sensor_fault", 0, tmp_path)
    assert "sensor_fault" in r["detected_classes"]
    assert r["outcome"] == "degraded_complete"
    assert r["hitl"] >= 1, "第二次感知失败应升级 HITL"


def test_low_battery_docks_and_resumes_original_queue(tmp_path):
    r = run_cond("low_battery", 0, tmp_path)
    assert "low_battery" in r["detected_classes"]
    assert r["outcome"] == "completed_full", "回充后应按原队列续跑完成全部巡检点"
    visited = r["summary"]["visited"]
    assert visited.count("dock") >= 2, "中途回坞 + 结束回坞"


def test_tool_failure_skips_degraded(tmp_path):
    r = run_cond("tool_failure", 0, tmp_path)
    assert "tool_failure" in r["detected_classes"]
    assert r["outcome"] == "degraded_complete"
    assert r["violations"] == 0


def test_compound_battery_preempts_blocked(tmp_path):
    """seed 0 是已知的诚实致死 case:电量抢占回坞后,受阻故障恰好砸在回坞边上,
    绕行超出剩余电量 → battery_dead。断言的是裁决与留痕的确定性,不是粉饰结果。"""
    r = run_cond("compound", 0, tmp_path)
    assert r["battery_preempt"], "复合故障时安全类(低电量)应先被处理"
    assert r["violations"] == 0
    assert "nav_blocked" in r["detected_classes"]
    assert r["outcome"] == "unsafe_failure"  # 确定性回归:如实记录,不掩盖
    assert r["summary"]["outcome_hint"] == "battery_dead"
