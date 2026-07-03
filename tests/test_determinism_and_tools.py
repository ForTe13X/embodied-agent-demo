"""确定性回归 + 幂等重试/熔断。"""
import asyncio

from embodied_agent.evaluation.harness import run_once
from embodied_agent.evaluation.metrics import load_events
from embodied_agent.evaluation.scenarios import all_conditions
from embodied_agent.faults import FaultSpec
from embodied_agent.runtime import RunConfig, build_runtime


def test_same_seed_same_event_stream(tmp_path):
    """同 seed + 同条件 → 事件流逐条一致(整个'可回放/预注册'叙事的地基)。"""
    cond = all_conditions()["compound"]
    p1 = asyncio.run(run_once(cond, 3, tmp_path / "x"))
    p2 = asyncio.run(run_once(cond, 3, tmp_path / "y"))
    assert load_events(p1) == load_events(p2)


def test_different_seeds_differ(tmp_path):
    cond = all_conditions()["nav_blocked"]
    p1 = asyncio.run(run_once(cond, 0, tmp_path / "x"))
    p2 = asyncio.run(run_once(cond, 1, tmp_path / "y"))
    assert load_events(p1) != load_events(p2)


def _tool_fault_spec(fail_first: int) -> FaultSpec:
    return FaultSpec(
        fault_id="tool_failure", description="", target="tool",
        mode="tool_fault", trigger={"kind": "at_start"},
        params={"tool": "perceive", "fail_first_choices": [fail_first],
                "modes": ["timeout"]},
        expected_detection="", expected_recovery_chain=[])


def test_idempotent_retry_recovers_single_failure():
    rt = build_runtime(RunConfig(condition="test", seed=0,
                                 fault_specs=[_tool_fault_spec(1)]))

    async def go():
        res = await rt.registry.call("perceive", {"query": "anomaly"})
        assert res.ok, "幂等工具 1 次失败后自动重试应成功"
        fails = [e for e in rt.event_log.events
                 if e["event_type"] == "tool_attempt_failed"]
        assert len(fails) == 1

    asyncio.run(go())


def test_circuit_breaker_opens_after_three_consecutive_failures():
    rt = build_runtime(RunConfig(condition="test", seed=0,
                                 fault_specs=[_tool_fault_spec(4)]))

    async def go():
        res1 = await rt.registry.call("perceive", {"query": "anomaly"})
        assert not res1.ok  # 两次尝试都失败
        res2 = await rt.registry.call("perceive", {"query": "anomaly"})
        assert not res2.ok  # 第三次失败 → 熔断
        assert any(e["event_type"] == "circuit_open"
                   for e in rt.event_log.events)
        res3 = await rt.registry.call("perceive", {"query": "anomaly"})
        assert res3.error["code"] == "CIRCUIT_OPEN"

    asyncio.run(go())


def test_non_idempotent_tools_never_auto_retry():
    rt = build_runtime(RunConfig(
        condition="test", seed=0,
        fault_specs=[FaultSpec(
            fault_id="tool_failure", description="", target="tool",
            mode="tool_fault", trigger={"kind": "at_start"},
            params={"tool": "navigate_to", "fail_first_choices": [1],
                    "modes": ["timeout"]},
            expected_detection="", expected_recovery_chain=[])]))

    async def go():
        res = await rt.registry.call("navigate_to", {"node_id": "a1"})
        assert not res.ok
        fails = [e for e in rt.event_log.events
                 if e["event_type"] == "tool_attempt_failed"]
        assert len(fails) == 1, "非幂等工具绝不自动重试"

    asyncio.run(go())
