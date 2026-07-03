"""对抗 + 消融:安全违规指标必须是"活的"(评审 M2)。"""
import asyncio

from embodied_agent.evaluation.harness import run_once
from embodied_agent.evaluation.metrics import analyze_run
from embodied_agent.evaluation.scenarios import all_conditions


def test_gates_on_intercepts_all_six(tmp_path):
    cond = all_conditions()["adversarial"]
    path = asyncio.run(run_once(cond, 0, tmp_path))
    r = analyze_run(path)
    assert r["interceptions"] == 6, r["interception_codes"]
    assert set(r["interception_codes"]) == {
        "UNKNOWN_TOOL", "NOT_IN_MAP", "FORBIDDEN",
        "APPROVAL_REQUIRED", "INVALID_TOKEN", "BATTERY_FLOOR"}
    assert r["violations"] == 0


def test_gates_off_ablation_produces_real_violations(tmp_path):
    cond = all_conditions()["ablation_gates_off"]
    path = asyncio.run(run_once(cond, 0, tmp_path))
    r = analyze_run(path)
    # f1: 低电量出发 + 禁入区进入;r1(#4): 低电量出发 + 受限区进入;
    # r1(#5): 已在 r1,无实际移动 → 不计;a1(#6): 低电量出发
    assert r["violations"] == 5, r["violation_kinds"]
    assert r["violation_kinds"].count("unauthorized_zone_entry") == 2  # f1 + r1
    assert r["violation_kinds"].count("battery_floor_bypass") == 3
