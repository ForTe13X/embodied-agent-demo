"""证据溯源门 (F-09) + 非幂等捕获与非空输出校验 (F-14) 的回归钉子。

F-09:白名单能限制"调哪个工具",但挡不住调用方提交无来源/张冠李戴的 finding。
F-14:capture_image 自动重试会产生第二次物理捕获,故非幂等;输出校验要求"键在且非 None"。
"""
import asyncio

from embodied_agent.hitl import ScriptedHITLPolicy
from embodied_agent.registry import ToolError, ToolSpec, _In
from embodied_agent.runtime import RunConfig, build_runtime


def make_rt():
    cfg = RunConfig(condition="test", seed=0, fault_specs=[], gates_on=True,
                    initial_battery_pct=100.0)
    hitl = ScriptedHITLPolicy([], default="deny")
    return build_runtime(cfg, hitl=hitl)


# ---- F-09 证据溯源 ---------------------------------------------------------

def test_report_finding_accepts_matched_evidence():
    """在当前节点拍照、并按当前节点上报 → 放行(合法巡检流)。"""
    rt = make_rt()

    async def go():
        here = rt.world.robot_node                 # 机器人起点(dock)
        img = await rt.registry.call("capture_image", {})
        assert img.ok
        rep = await rt.registry.call("report_finding", {
            "image_id": img.data["image_id"], "label": "leak", "node_id": here})
        assert rep.ok and rep.data["report_id"].startswith("report-")
        assert any(e["event_type"] == "finding_reported"
                   for e in rt.event_log.events)

    asyncio.run(go())


def test_report_finding_rejects_uncaptured_image():
    """从未拍过的 image_id → EVIDENCE_UNVERIFIED,不进 finding_reported。"""
    rt = make_rt()

    async def go():
        rep = await rt.registry.call("report_finding", {
            "image_id": "img-fabricated", "label": "leak",
            "node_id": rt.world.robot_node})
        assert not rep.ok and rep.error["code"] == "EVIDENCE_UNVERIFIED"
        assert not any(e["event_type"] == "finding_reported"
                       for e in rt.event_log.events)

    asyncio.run(go())


def test_report_finding_rejects_out_of_topo_node():
    """真拍了照,但上报到图外节点 → 拒。"""
    rt = make_rt()

    async def go():
        img = await rt.registry.call("capture_image", {})
        rep = await rt.registry.call("report_finding", {
            "image_id": img.data["image_id"], "label": "leak", "node_id": "z9"})
        assert not rep.ok and rep.error["code"] == "EVIDENCE_UNVERIFIED"

    asyncio.run(go())


def test_report_finding_rejects_node_mismatch():
    """拍于 dock、报成 c1(合法节点但张冠李戴) → 拒。"""
    rt = make_rt()

    async def go():
        assert rt.world.robot_node == "dock"
        img = await rt.registry.call("capture_image", {})       # ledger[img]=dock
        rep = await rt.registry.call("report_finding", {
            "image_id": img.data["image_id"], "label": "leak", "node_id": "c1"})
        assert not rep.ok and rep.error["code"] == "EVIDENCE_UNVERIFIED"

    asyncio.run(go())


# ---- F-14 非幂等捕获 + 非空输出 -------------------------------------------

def test_capture_image_is_non_idempotent():
    """capture_image 非幂等:注册表不得对它自动重试(重试=第二次物理捕获)。"""
    rt = make_rt()
    assert rt.registry.tools["capture_image"].idempotent is False


def test_execute_rejects_none_valued_required_key():
    """输出校验:required key 存在但值为 None → SCHEMA_VIOLATION_OUT。"""
    rt = make_rt()

    class _FakeIn(_In):
        pass

    async def none_handler(p):
        return {"image_id": None}

    spec = ToolSpec("__none_probe__", _FakeIn, none_handler, False, ("image_id",))

    async def go():
        try:
            await rt.registry._execute(spec, _FakeIn())
        except ToolError as e:
            assert e.code == "SCHEMA_VIOLATION_OUT"
        else:
            raise AssertionError("None 值的 required key 应被拒")

    asyncio.run(go())


def test_execute_accepts_falsey_but_present_values():
    """空列表 []/布尔 False 是合法值(非 None),不得被非空校验误杀。"""
    rt = make_rt()

    class _FakeIn(_In):
        pass

    async def falsey_handler(p):
        return {"objects": [], "canceled": False}

    spec = ToolSpec("__falsey_probe__", _FakeIn, falsey_handler, True,
                    ("objects", "canceled"))

    async def go():
        data = await rt.registry._execute(spec, _FakeIn())
        assert data == {"objects": [], "canceled": False}

    asyncio.run(go())
