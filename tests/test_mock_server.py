"""mock server 的 goal-handle 语义:在飞 feedback / 中途 cancel / 停滞不自报 / unreachable。"""
import asyncio

from embodied_agent.faults import FaultSpec
from embodied_agent.runtime import RunConfig, build_runtime
from embodied_agent.world import edge_key


def make_rt(fault_specs=None, **kw):
    cfg = RunConfig(condition="test", seed=0, fault_specs=fault_specs or [], **kw)
    return build_runtime(cfg)


def test_navigation_reaches_target_with_feedback():
    rt = make_rt()

    async def go():
        res = rt.server.send_goal("a1")
        gid = res["goal_id"]
        saw_executing = False
        for _ in range(60):
            await rt.adapter.wait(1)
            fb = rt.server.feedback(gid)
            if fb["status"] == "executing":
                saw_executing = True
            if rt.server.result(gid):
                break
        assert saw_executing
        assert rt.server.result(gid)["status"] == "succeeded"
        assert rt.world.robot_node == "a1"

    asyncio.run(go())


def test_cancel_mid_flight():
    rt = make_rt()

    async def go():
        gid = rt.server.send_goal("a3")["goal_id"]
        await rt.adapter.wait(4)  # 走到半路
        assert rt.server.result(gid) is None
        assert rt.server.cancel(gid)
        res = rt.server.result(gid)
        assert res["status"] == "canceled"
        assert rt.world.robot_node != "a3"

    asyncio.run(go())


def test_blocked_edge_stalls_but_server_never_reports_blocked():
    """诚实性核心:server 只停滞,受阻必须由编排层水位检测发现。"""
    rt = make_rt()

    async def go():
        rt.world.blocked_edges.add(edge_key("dock", "c1"))
        gid = rt.server.send_goal("c1")["goal_id"]
        for _ in range(20):
            await rt.adapter.wait(1)
        fb = rt.server.feedback(gid)
        assert fb["status"] == "executing"      # 不是 'blocked'
        assert fb["velocity"] == 0.0
        assert fb["stall_ticks"] >= 19
        assert rt.server.result(gid) is None    # 永不自行终态

    asyncio.run(go())


def test_unreachable_returns_aborted_result():
    rt = make_rt(fault_specs=[FaultSpec(
        fault_id="nav_unreachable", description="", target="nav",
        mode="isolate_node", trigger={"kind": "at_start"},
        params={"node": "a3"}, expected_detection="", expected_recovery_chain=[])])

    async def go():
        gid = rt.server.send_goal("a3")["goal_id"]
        res = rt.server.result(gid)
        assert res is not None
        assert res["status"] == "aborted" and res["reason"] == "unreachable"

    asyncio.run(go())
