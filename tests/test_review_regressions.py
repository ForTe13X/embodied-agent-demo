"""复审(多 agent find→adversarial-verify)证实缺陷的回归测试。"""
import asyncio

from embodied_agent.evaluation.harness import run_once
from embodied_agent.evaluation.metrics import analyze_run, load_events
from embodied_agent.evaluation.scenarios import all_conditions
from embodied_agent.graph import run_graph
from embodied_agent.intent import Intent
from embodied_agent.runtime import RunConfig, build_runtime


def test_battery_floor_gate_rejection_terminates_via_recharge():
    """复审 critical:电量闸拒绝曾被误分类为 TOOL_FAILURE → 零 tick 死循环。
    修复后:BATTERY_FLOOR 拒绝 → LOW_BATTERY 链 → 回坞充电续跑,run 正常终止。"""
    rt = build_runtime(RunConfig(condition="test", seed=0, fault_specs=[],
                                 initial_battery_pct=15.0))

    async def go():
        await run_graph(rt)

    asyncio.run(go())
    summaries = [e for e in rt.event_log.events
                 if e["event_type"] == "run_summary"]
    assert summaries, "run 必须以 run_summary 终止,而不是 GraphRecursionError"
    assert any(e["event_type"] == "queue_snapshot"
               for e in rt.event_log.events), "应走回坞充电链"
    assert rt.world.battery_pct > 15.0, "应当充过电"


def test_skipped_navigate_also_skips_paired_perceive(tmp_path):
    """复审 major:跳过 navigate 后配对 perceive 曾在错误节点执行并记为完成。"""
    cond = all_conditions()["compound"]
    path = asyncio.run(run_once(cond, 7, tmp_path))  # 已知的被困坞内 seed
    events = load_events(path)
    skipped_targets = {
        e["payload"]["step"].get("target")
        for e in events if e["event_type"] == "step_skipped"
        and e["payload"]["step"].get("kind") == "navigate"}
    completed_perceives_at = {
        e["payload"]["step"].get("at")
        for e in events if e["event_type"] == "step_completed"
        and e["payload"].get("step", {}).get("kind") == "perceive"}
    assert not (skipped_targets & completed_perceives_at), \
        "被跳过 navigate 的配对 perceive 不得被记为完成"


def test_stranded_run_not_classified_safe(tmp_path):
    """复审 major:安全中止类终态必须核对地面真值位姿。compound 的致死 seed
    仍应是 unsafe_failure(battery_dead),不能被任何 hint 洗白。"""
    cond = all_conditions()["compound"]
    path = asyncio.run(run_once(cond, 0, tmp_path))
    r = analyze_run(path)
    if r["summary"] and r["summary"].get("outcome_hint") == "battery_dead":
        assert r["outcome"] == "unsafe_failure"


def test_intent_resume_threshold_clamped():
    """复审 major:红线高于复归阈值曾导致回坞/续跑无限乒乓。"""
    it = Intent(mission="x", patrol_nodes=["a1"],
                battery_floor_pct=85, resume_battery_pct=80)
    assert it.resume_battery_pct >= it.battery_floor_pct + 5


def test_zone_token_not_burned_by_battery_gate():
    """复审 minor:双闸场景(受限区 + 低电量)曾先烧掉 zone token 再拒绝。"""
    from embodied_agent.hitl import ScriptedHITLPolicy
    rt = build_runtime(RunConfig(condition="test", seed=0, fault_specs=[],
                                 initial_battery_pct=15.0),
                       hitl=ScriptedHITLPolicy([(".*", "approve")]))

    async def go():
        tok = await rt.registry.call("ask_human_confirmation", {
            "message": "进入 r1?", "scope": "navigate_to:r1"})
        token = tok.data["approval_token"]
        res = await rt.registry.call("navigate_to", {
            "node_id": "r1", "approval_token": token})
        assert not res.ok and res.error["code"] == "BATTERY_FLOOR"
        # token 未被烧掉:充电后同一 token 仍可用
        rt.world.battery_pct = 90.0
        res = await rt.registry.call("navigate_to", {
            "node_id": "r1", "approval_token": token})
        assert res.ok, "被电量闸拒绝的调用不应消耗 zone token"

    asyncio.run(go())


def test_determinism_across_conditions(tmp_path):
    """复审 minor:确定性回归只覆盖单一条件/seed → 扩展到多条件多 seed。"""
    conds = all_conditions()
    for name in ("nav_blocked", "low_battery", "tool_failure"):
        for seed in (1, 4):
            p1 = asyncio.run(run_once(conds[name], seed, tmp_path / "x" / name))
            p2 = asyncio.run(run_once(conds[name], seed, tmp_path / "y" / name))
            assert load_events(p1) == load_events(p2), f"{name} seed={seed}"
