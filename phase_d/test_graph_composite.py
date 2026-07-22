"""D2:复合任务跑在【正式 LangGraph graph】里(不再是独立壳子)。

codex 评审:"composite 仍未进入正式 LangGraph graph"。这里断言 nav 与 VLA skill 在同一张图、
同一 registry、同一事件日志下混编执行,且 skill 失败按恢复归属矩阵路由(安全停绝不重试)。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from embodied_agent.graph import run_graph  # noqa: E402
from embodied_agent.runtime import RunConfig, build_runtime  # noqa: E402

from vla_skill_tool import make_sim_factory, register_vla_skill  # noqa: E402

MISSION = [
    {"kind": "navigate", "target": "a1"},
    {"kind": "vla_skill", "instruction": "pick up the red block", "timeout_s": 5.0},
    {"kind": "navigate", "target": "dock"},
]


def _run(scenario):
    rt = build_runtime(RunConfig(condition=f"d2_{scenario}", seed=0, fault_specs=[]))
    register_vla_skill(rt.registry, make_sim_factory(scenario))
    final = asyncio.run(run_graph(rt, mission_queue=list(MISSION)))
    return rt, final, rt.event_log.events


def _tools(events, name):
    return [e for e in events if e["event_type"] == "tool_call"
            and e["payload"].get("tool") == name]


def _ev(events, etype):
    return [e for e in events if e["event_type"] == etype]


def test_composite_runs_inside_formal_graph(tmp_path):
    """nav → vla_skill → nav 全程走正式图:同一 registry 的 tool_call,同一份事件日志。"""
    rt, final, events = _run("baseline")
    # skill 与 nav 都经同一 registry(同一门禁/熔断/日志路径)
    assert _tools(events, "navigate_to"), "nav 应走 registry"
    assert _tools(events, "execute_vla_skill"), "skill 应走同一 registry"
    assert _tools(events, "get_skill_feedback"), "observer 应轮询 skill(与 nav 同构)"
    # 图的节点确实参与了:planner 出计划、observer 记 step_completed
    assert _ev(events, "plan_built")
    assert any(e["payload"].get("step", {}).get("kind") == "vla_skill"
               for e in _ev(events, "step_completed"))
    # VLA runtime 事件折进同一份日志(可回放、可审计)
    assert any(e["actor"] == "vla_skill" for e in events)
    assert rt.world.robot_node == "dock"          # 安全归坞


def test_postcheck_is_independent_tool_call_in_graph(tmp_path):
    """后置校验是 registry 里的独立工具调用,依据回读末态而非 skill 自报。"""
    _, _, events = _run("baseline")
    assert _tools(events, "verify_skill_postcondition"), "postcheck 应是独立工具调用"
    pc = _ev(events, "verify_manipulation")
    assert pc, "应发 verify_manipulation"
    p = pc[-1]["payload"]
    assert p["method"] == "independent_sim_readback"
    assert p["block_grasped"] is True
    assert p["verified"] is True
    assert p["agrees_with_skill"] is True


def test_unsafe_skill_routes_to_skill_unsafe_and_never_retries(tmp_path):
    """安全停 → SKILL_UNSAFE(与低电量同级的安全类)→ 不重试、降级继续 → 仍安全归坞。"""
    rt, final, events = _run("unsafe")
    faults = _ev(events, "fault_classified")
    assert faults, "安全停应上浮为编排层故障"
    assert any(f["payload"]["fclass"] == "skill_unsafe" for f in faults), \
        f"应归类 skill_unsafe,实得 {[f['payload']['fclass'] for f in faults]}"
    # 绝不重试:execute_vla_skill 只被调用一次
    assert len(_tools(events, "execute_vla_skill")) == 1, "安全停不得重试"
    # 独立校验如实报未抓住
    p = _ev(events, "verify_manipulation")[-1]["payload"]
    assert p["verified"] is False and p["block_grasped"] is False
    # 降级后仍安全归坞
    assert rt.world.robot_node == "dock"


def test_unreachable_skill_degrades_and_still_docks(tmp_path):
    """够不到 → no_progress → SKILL_FAILED → 降级跳过 → 安全归坞(不搁浅)。"""
    rt, _, events = _run("unreachable")
    faults = _ev(events, "fault_classified")
    assert any(f["payload"]["fclass"] == "skill_failed" for f in faults), \
        f"应归类 skill_failed,实得 {[f['payload']['fclass'] for f in faults]}"
    assert rt.world.robot_node == "dock"
