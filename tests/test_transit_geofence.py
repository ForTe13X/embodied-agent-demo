"""F-01 运行期访问地理围栏:transit 强制(不只披露)。

两组:
  1. TransitGuard 纯决策逻辑(宿主 venv 可测,无 rclpy 依赖);
  2. mock server 端到端强制——模拟"access-盲规划器"(强制路线穿过 r1),验证围栏在踏入
     未授权受限区时安全停;并验证【授权穿越仍放行】与【消融关闭围栏时如实放行以供测量】。
"""
import asyncio

from embodied_agent.geofence import TransitGuard, TransitViolation
from embodied_agent.runtime import RunConfig, build_runtime
from embodied_agent.world import default_map

TOPO = default_map()   # r1=restricted, f1=forbidden, 其余 free


def make_rt():
    return build_runtime(RunConfig(condition="test", seed=0, fault_specs=[]))


# ---- 1. 纯决策逻辑 ---------------------------------------------------------

def test_guard_forbidden_always_violation():
    g = TransitGuard(TOPO.access)
    v = g.check("f1", authorized_zones=frozenset())
    assert isinstance(v, TransitViolation) and v.kind == "forbidden_transit"


def test_guard_forbidden_violation_even_with_authorization():
    # 禁入区永不可 transit——即便误把它塞进授权集也拦(access 铁律优先)
    g = TransitGuard(TOPO.access)
    v = g.check("f1", authorized_zones=frozenset({"f1"}))
    assert v is not None and v.kind == "forbidden_transit"


def test_guard_restricted_unauthorized_violation():
    g = TransitGuard(TOPO.access)
    v = g.check("r1", authorized_zones=frozenset())
    assert v is not None and v.kind == "unauthorized_restricted_transit"


def test_guard_restricted_authorized_ok():
    g = TransitGuard(TOPO.access)
    assert g.check("r1", authorized_zones=frozenset({"r1"})) is None


def test_guard_free_node_ok():
    g = TransitGuard(TOPO.access)
    assert g.check("c2", authorized_zones=frozenset()) is None


def test_guard_disabled_passes_everything():
    # 消融(gates_off):围栏整体关闭,连禁入区也不拦(交给地面真值 SafetyMonitor 如实测量)
    g = TransitGuard(TOPO.access)
    assert g.check("f1", authorized_zones=frozenset(), enabled=False) is None


def test_guard_none_and_unknown_node_pass():
    g = TransitGuard(TOPO.access)
    assert g.check(None, authorized_zones=frozenset()) is None
    assert g.check("z9", authorized_zones=frozenset()) is None   # 图外 → KeyError 吞掉,不归围栏管


# ---- 2. mock server 端到端强制 --------------------------------------------

def _drive_to_terminal(rt, gid, max_ticks=60):
    async def go():
        for _ in range(max_ticks):
            await rt.adapter.wait(1)
            if rt.server.result(gid):
                return rt.server.result(gid)
        return rt.server.result(gid)
    return asyncio.run(go())


def test_geofence_halts_access_blind_transit_through_restricted():
    # 模拟真实 Nav2 的 access-盲规划器:allow_all_restricted 使路线穿过 r1,
    # 但围栏开着且未授权 r1 → 踏入 r1 即安全停,不继续到 a3。
    rt = make_rt()
    res = rt.server.send_goal("a3", authorized=False, allow_all_restricted=True,
                              restricted_ok_nodes=frozenset(), geofence_on=True)
    gid = res["goal_id"]
    final = _drive_to_terminal(rt, gid)
    assert final is not None and final["status"] == "aborted"
    assert final["reason"].startswith("transit_violation")
    assert rt.world.robot_node == "r1"           # 踏入 r1 被拦停在此,未继续到 a3
    stops = [e for e in rt.event_log.events if e["event_type"] == "transit_guard_stop"]
    assert len(stops) == 1
    assert stops[0]["payload"]["node"] == "r1"
    assert stops[0]["payload"]["kind"] == "unauthorized_restricted_transit"


def test_geofence_allows_authorized_restricted_target():
    # 带 r1 授权(token scope=r1)导航到 r1:进入 r1 是被授权的,围栏放行,正常到达。
    rt = make_rt()
    res = rt.server.send_goal("r1", authorized=True,
                              restricted_ok_nodes=frozenset({"r1"}), geofence_on=True)
    gid = res["goal_id"]
    final = _drive_to_terminal(rt, gid)
    assert final is not None and final["status"] == "succeeded"
    assert rt.world.robot_node == "r1"
    assert not any(e["event_type"] == "transit_guard_stop" for e in rt.event_log.events)


def test_geofence_off_under_ablation_lets_transit_through_for_measurement():
    # 消融:路线穿 r1 且围栏关 → 不拦停,机器人抵达 a3;地面真值仍记违规(供测量),
    # 但运行期围栏静默——保证 ablation_gates_off 的违规计数不被强制层污染。
    rt = make_rt()
    res = rt.server.send_goal("a3", authorized=False, allow_all_restricted=True,
                              restricted_ok_nodes=frozenset(), geofence_on=False)
    gid = res["goal_id"]
    final = _drive_to_terminal(rt, gid)
    assert final is not None and final["status"] == "succeeded"
    assert rt.world.robot_node == "a3"
    assert not any(e["event_type"] == "transit_guard_stop" for e in rt.event_log.events)
    # 地面真值 SafetyMonitor 仍如实记录 r1 的未授权进入(测量不受围栏影响)
    assert any(v["kind"] == "unauthorized_zone_entry" and v["node"] == "r1"
               for v in rt.safety.violations)
