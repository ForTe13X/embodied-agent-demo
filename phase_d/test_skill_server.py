"""异步 skill goal-handle 契约(D1)测试。

证明 codex 评审指出的缺口已闭合:此前 `execute_vla_skill` 阻塞到终态、**上层根本没有句柄**,
所以既不能在飞取消、也拿不到进度。这里逐条断言新契约与 `navigate_to` 同构:
send_goal 立即返回 → feedback 在飞可轮询 → cancel 在飞可取消 → result 终态后可取。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from embodied_agent.runtime import RunConfig, build_runtime  # noqa: E402

from vla_skill_tool import make_sim_factory, register_vla_skill  # noqa: E402

ARGS = {"instruction": "pick up the red block", "skill_id": "tabletop_pick", "timeout_s": 5.0}


def _rt(scenario="baseline"):
    rt = build_runtime(RunConfig(condition="test", seed=0, fault_specs=[]))
    server = register_vla_skill(rt.registry, make_sim_factory(scenario))
    return rt, server


def test_execute_returns_handle_immediately_without_blocking():
    """核心:execute 立即拿到 skill_goal_id,此时 skill 还没跑完(旧实现这里已经阻塞到终态)。"""
    rt, _ = _rt()

    async def go():
        res = await rt.registry.call("execute_vla_skill", ARGS)
        assert res.ok, res.error
        sid = res.data["skill_goal_id"]
        # 立即查一次:应仍在飞(证明 execute 没有阻塞到终态)
        fb = await rt.registry.call("get_skill_feedback", {"skill_goal_id": sid})
        assert fb.ok and fb.data["status"] == "executing"
        # 终态前取 result → 明确拒绝,而不是阻塞或返回半成品
        r = await rt.registry.call("get_skill_result", {"skill_goal_id": sid})
        assert not r.ok and r.error["code"] == "SKILL_NOT_FINISHED"
        # 收尾:轮询到终态
        for _ in range(4000):
            fb = await rt.registry.call("get_skill_feedback", {"skill_goal_id": sid}, poll=True)
            if fb.data["status"] != "executing":
                break
            await asyncio.sleep(0.002)
        assert fb.data["status"] == "succeeded"

    asyncio.run(go())


def test_cancel_in_flight_terminates_as_canceled():
    """在飞取消 —— 旧的阻塞实现里上层完全没有这个能力。"""
    rt, _ = _rt()

    async def go():
        sid = (await rt.registry.call("execute_vla_skill", ARGS)).data["skill_goal_id"]
        await asyncio.sleep(0.01)                     # 让它跑几步
        c = await rt.registry.call("cancel_skill", {"skill_goal_id": sid})
        assert c.ok and c.data["canceled"] is True
        for _ in range(4000):                          # 等 runtime 收敛
            fb = await rt.registry.call("get_skill_feedback", {"skill_goal_id": sid}, poll=True)
            if fb.data["status"] != "executing":
                break
            await asyncio.sleep(0.002)
        r = await rt.registry.call("get_skill_result", {"skill_goal_id": sid})
        assert r.ok and r.data["status"] == "failed"
        assert r.data["terminal_reason"] == "canceled"
        assert r.data["code"] == "VLA_CANCELED"
        assert r.data["retriable"] is False            # 取消不是"重试就能好"的失败

    asyncio.run(go())


def test_cancel_after_terminal_returns_false():
    rt, _ = _rt()

    async def go():
        sid = (await rt.registry.call("execute_vla_skill", ARGS)).data["skill_goal_id"]
        for _ in range(4000):
            fb = await rt.registry.call("get_skill_feedback", {"skill_goal_id": sid}, poll=True)
            if fb.data["status"] != "executing":
                break
            await asyncio.sleep(0.002)
        c = await rt.registry.call("cancel_skill", {"skill_goal_id": sid})
        assert c.ok and c.data["canceled"] is False    # 已终态 → 取消无效(与 nav 语义一致)

    asyncio.run(go())


def test_unknown_skill_goal_is_rejected():
    rt, _ = _rt()

    async def go():
        fb = await rt.registry.call("get_skill_feedback", {"skill_goal_id": "nope"})
        assert not fb.ok and fb.error["code"] == "UNKNOWN_SKILL_GOAL"

    asyncio.run(go())


def test_second_goal_while_active_is_busy():
    rt, _ = _rt()

    async def go():
        await rt.registry.call("execute_vla_skill", ARGS)
        second = await rt.registry.call("execute_vla_skill", ARGS)
        assert not second.ok and second.error["code"] == "SKILL_BUSY"

    asyncio.run(go())


def test_unsafe_scenario_surfaces_unsafe_stop_code():
    """安全停必须以 VLA_UNSAFE_STOP + 不可重试 上浮(Skill Supervisor 据此绝不重试)。"""
    rt, _ = _rt("unsafe")

    async def go():
        sid = (await rt.registry.call("execute_vla_skill", ARGS)).data["skill_goal_id"]
        for _ in range(4000):
            fb = await rt.registry.call("get_skill_feedback", {"skill_goal_id": sid}, poll=True)
            if fb.data["status"] != "executing":
                break
            await asyncio.sleep(0.002)
        r = await rt.registry.call("get_skill_result", {"skill_goal_id": sid})
        assert r.ok and r.data["status"] == "failed"
        assert r.data["code"] == "VLA_UNSAFE_STOP"
        assert r.data["retriable"] is False
        assert r.data["terminal_reason"].startswith("must_stop")

    asyncio.run(go())


def test_schema_forbids_unknown_param():
    """与 nav 同一门禁路径:未知超参一样被 schema 拒。"""
    rt, _ = _rt()

    async def go():
        res = await rt.registry.call("execute_vla_skill", {**ARGS, "torque": 99})
        assert not res.ok and res.error["code"] == "SCHEMA_VIOLATION"

    asyncio.run(go())
