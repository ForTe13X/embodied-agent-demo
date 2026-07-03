"""安全门禁:白名单 / schema / 访问级 / token / 电量闸。面试必问的每条规则都有对应断言。"""
import asyncio

from embodied_agent.hitl import ScriptedHITLPolicy
from embodied_agent.runtime import RunConfig, build_runtime


def make_rt(gates_on=True, battery=100.0, hitl_rules=None):
    cfg = RunConfig(condition="test", seed=0, fault_specs=[], gates_on=gates_on,
                    initial_battery_pct=battery)
    hitl = ScriptedHITLPolicy(hitl_rules or [], default="deny")
    return build_runtime(cfg, hitl=hitl)


def test_unknown_tool_rejected():
    rt = make_rt()

    async def go():
        res = await rt.registry.call("override_motors", {"velocity": 2.0})
        assert not res.ok and res.error["code"] == "UNKNOWN_TOOL"
        assert any(e["event_type"] == "guardrail_rejection"
                   for e in rt.event_log.events)

    asyncio.run(go())


def test_unknown_param_rejected():
    rt = make_rt()

    async def go():
        res = await rt.registry.call("navigate_to",
                                     {"node_id": "a1", "torque": 99})
        assert not res.ok and res.error["code"] == "SCHEMA_VIOLATION"

    asyncio.run(go())


def test_out_of_map_rejected():
    rt = make_rt()

    async def go():
        res = await rt.registry.call("navigate_to", {"node_id": "z9"})
        assert not res.ok and res.error["code"] == "NOT_IN_MAP"

    asyncio.run(go())


def test_forbidden_rejected_even_with_token():
    rt = make_rt(hitl_rules=[(".*", "approve")])

    async def go():
        tok = await rt.registry.call(
            "ask_human_confirmation",
            {"message": "进入配电室?", "scope": "navigate_to:f1"})
        assert tok.data["approved"]
        res = await rt.registry.call(
            "navigate_to",
            {"node_id": "f1", "approval_token": tok.data["approval_token"]})
        assert not res.ok and res.error["code"] == "FORBIDDEN"

    asyncio.run(go())


def test_restricted_requires_valid_single_use_token():
    rt = make_rt(hitl_rules=[(".*", "approve")])

    async def go():
        # 无 token
        res = await rt.registry.call("navigate_to", {"node_id": "r1"})
        assert not res.ok and res.error["code"] == "APPROVAL_REQUIRED"
        # 伪造 token
        res = await rt.registry.call(
            "navigate_to", {"node_id": "r1", "approval_token": "forged"})
        assert not res.ok and res.error["code"] == "INVALID_TOKEN"
        # scope 不符的 token
        tok = await rt.registry.call(
            "ask_human_confirmation",
            {"message": "x", "scope": "navigate_to:somewhere_else"})
        res = await rt.registry.call(
            "navigate_to",
            {"node_id": "r1", "approval_token": tok.data["approval_token"]})
        assert not res.ok and res.error["code"] == "INVALID_TOKEN"
        # 正确 token → 放行,且地面真值记录为已授权(0 违规)
        tok = await rt.registry.call(
            "ask_human_confirmation",
            {"message": "进入受限捷径?", "scope": "navigate_to:r1"})
        res = await rt.registry.call(
            "navigate_to",
            {"node_id": "r1", "approval_token": tok.data["approval_token"]})
        assert res.ok
        gid = res.data["goal_id"]
        for _ in range(50):
            if await rt.adapter.result(gid):
                break
            await rt.adapter.wait(1)
        assert rt.world.robot_node == "r1"
        assert rt.safety.violations == []
        # token 一次一用:同一 token 不能再用
        res = await rt.registry.call(
            "navigate_to",
            {"node_id": "r1", "approval_token": tok.data["approval_token"]})
        assert not res.ok and res.error["code"] == "INVALID_TOKEN"

    asyncio.run(go())


def test_battery_floor_gate():
    rt = make_rt(battery=15.0)

    async def go():
        res = await rt.registry.call("navigate_to", {"node_id": "a1"})
        assert not res.ok and res.error["code"] == "BATTERY_FLOOR"
        # dock 永远放行
        res = await rt.registry.call("navigate_to", {"node_id": "dock"})
        assert res.ok

    asyncio.run(go())


def test_gates_off_ablation_records_ground_truth_violations():
    rt = make_rt(gates_on=False, battery=15.0)

    async def go():
        for node in ("f1", "r1"):
            res = await rt.registry.call("navigate_to", {"node_id": node})
            assert res.ok, f"门禁关闭时 {node} 应可执行"
            gid = res.data["goal_id"]
            for _ in range(100):
                if await rt.adapter.result(gid):
                    break
                await rt.adapter.wait(1)
        kinds = [v["kind"] for v in rt.safety.violations]
        assert "unauthorized_zone_entry" in kinds
        assert "battery_floor_bypass" in kinds

    asyncio.run(go())
