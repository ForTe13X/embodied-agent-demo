"""VLASkillRuntime 异步运行时测试:happy path / must_stop / stale-drop / cancel / 事件日志。

跑法:pytest phase_d/test_runtime.py -q(项目 venv;用 asyncio.run,不需 pytest-asyncio)。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from mock_vla_policy import MockVLAPolicy, PolicyConfig  # noqa: E402
from safety_shield import SafetyShield  # noqa: E402
from tabletop_sim import TabletopSim  # noqa: E402
from vla_skill_runtime import SkillGoal, VLASkillRuntime  # noqa: E402


def _make(policy_cfg=None, latency=0.0):
    events = []
    rt = VLASkillRuntime(MockVLAPolicy(policy_cfg or PolicyConfig(), seed=0),
                         SafetyShield(), TabletopSim(),
                         inference_latency_s=latency, events=events)
    return rt, events


def test_happy_path_grasps():
    rt, events = _make()
    res = asyncio.run(rt.execute(SkillGoal("m1", "pick up the block")))
    assert res.success is True
    assert res.terminal_reason == "grasped"
    assert res.steps > 0
    assert rt.sim.block.grasped is True
    # 事件日志:起止事件都在
    types = [e["event_type"] for e in events]
    assert "skill_started" in types and "skill_succeeded" in types and "skill_finished" in types


def test_out_of_bounds_must_stop():
    rt, events = _make(PolicyConfig(inject_out_of_bounds=True))
    res = asyncio.run(rt.execute(SkillGoal("m2", "pick", timeout_s=3.0)))
    assert res.success is False
    assert res.terminal_reason.startswith("must_stop")
    assert any(e["event_type"] == "emergency_stop" for e in events)
    # 末端从未越界(shield 兜住)
    assert SafetyShield().cfg.box.contains(rt.sim.ee.pos)


def test_stale_chunks_are_dropped():
    # 高推理延迟:chunk 回来时观测已推进 > 容差 → 判过期丢弃,不执行陈旧动作
    rt, events = _make(latency=0.05)
    res = asyncio.run(rt.execute(SkillGoal("m3", "pick", timeout_s=0.3)))
    assert res.stale_drops >= 1
    assert any(e["event_type"] == "chunk_dropped_stale" for e in events)
    assert res.success is False          # 一直拿不到新鲜 chunk → hold 到超时


def test_cancel_stops_cleanly():
    # 目标在盒外(永不 grasp)→ 一直跑到被 cancel
    async def run_and_cancel():
        rt = VLASkillRuntime(MockVLAPolicy(PolicyConfig(target_pos=(1.0, 0, 0.06)), seed=0),
                             SafetyShield(), TabletopSim(), events=[])
        task = asyncio.create_task(rt.execute(SkillGoal("m4", "pick", timeout_s=5.0)))
        await asyncio.sleep(0.05)
        rt.cancel()
        return await task, rt.events
    res, events = asyncio.run(run_and_cancel())
    assert res.terminal_reason == "canceled"
    assert res.success is False
    assert any(e["event_type"] == "skill_canceled" for e in events)


def test_every_run_finishes_with_summary():
    for cfg in (PolicyConfig(), PolicyConfig(jitter=0.03), PolicyConfig(target_pos=(1.0, 0, 0.06))):
        rt, events = _make(cfg)
        res = asyncio.run(rt.execute(SkillGoal("m", "pick", timeout_s=2.0)))
        fin = [e for e in events if e["event_type"] == "skill_finished"]
        assert len(fin) == 1                          # 每个 run 恰好一条终态汇总
        assert res.terminal_reason != ""              # 终态原因非空


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
