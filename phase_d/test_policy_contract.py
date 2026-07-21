"""版本化 Policy Contract(D1)测试:版本兼容 / chunk 校验 / 执行边界 / 逐动作新鲜度。

重点回归的是 codex 评审指出的两条语义漏洞:
  1. `execution_horizon` 此前是"补片阈值"而非"一批最多执行几个"——真正的执行边界根本不存在;
  2. 新鲜度只在 chunk 接收时判一次,**执行每个动作时不判**,导致陈旧感知仍会驱动动作。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import pytest  # noqa: E402

from action_types import Action, ChunkResult  # noqa: E402
from mock_vla_policy import MockVLAPolicy, PolicyConfig  # noqa: E402
from policy_contract import (POLICY_CONTRACT_VERSION, ContractViolation,  # noqa: E402
                             PolicyContract)
from safety_shield import SafetyShield  # noqa: E402
from tabletop_sim import TabletopSim  # noqa: E402
from vla_skill_runtime import SkillGoal, VLASkillRuntime  # noqa: E402


def _chunk(mission="m", seq=5, n=2):
    acts = tuple(Action((0.001, 0.0, 0.0, 0.0, 0.0, 0.0), 0.0) for _ in range(n))
    return ChunkResult(mission_id=mission, observation_seq=seq, actions=acts)


# ---- 版本兼容 --------------------------------------------------------------

def test_version_major_match_accepted_minor_ignored():
    c = PolicyContract()
    major = POLICY_CONTRACT_VERSION.split(".")[0]
    assert c.accepts_version(POLICY_CONTRACT_VERSION)
    assert c.accepts_version(f"{major}.99")          # minor 向后兼容
    assert not c.accepts_version("99.0")             # major 不同 → 不兼容
    assert not c.accepts_version(None)
    assert not c.accepts_version("garbage")


def test_mock_policy_declares_compatible_version():
    PolicyContract().assert_policy_compatible(MockVLAPolicy())


def test_policy_without_version_is_rejected_at_load():
    class _NoVersion:
        pass
    with pytest.raises(ContractViolation) as e:
        PolicyContract().assert_policy_compatible(_NoVersion())
    assert e.value.code == "POLICY_CONTRACT_MISMATCH"


def test_runtime_refuses_incompatible_policy_at_construction():
    class _Bad(MockVLAPolicy):
        contract_version = "99.0"
    with pytest.raises(ContractViolation):
        VLASkillRuntime(_Bad(), SafetyShield(), TabletopSim())


# ---- 契约自洽 --------------------------------------------------------------

def test_execution_horizon_cannot_exceed_action_horizon():
    with pytest.raises(ValueError):
        PolicyContract(action_horizon=4, execution_horizon=5)


# ---- chunk 级校验 ----------------------------------------------------------

def test_validate_chunk_rejects_wrong_mission():
    c = PolicyContract()
    with pytest.raises(ContractViolation) as e:
        c.validate_chunk(_chunk(mission="other"), current_seq=5, now=0.0, mission_id="m")
    assert e.value.code == "CHUNK_WRONG_MISSION"


def test_validate_chunk_rejects_empty():
    c = PolicyContract()
    with pytest.raises(ContractViolation) as e:
        c.validate_chunk(_chunk(n=0), current_seq=5, now=0.0, mission_id="m")
    assert e.value.code == "CHUNK_EMPTY"


def test_validate_chunk_rejects_over_action_horizon():
    c = PolicyContract(action_horizon=3, execution_horizon=3)
    with pytest.raises(ContractViolation) as e:
        c.validate_chunk(_chunk(n=4), current_seq=5, now=0.0, mission_id="m")
    assert e.value.code == "CHUNK_OVER_HORIZON"


def test_validate_chunk_rejects_stale_observation():
    c = PolicyContract(max_obs_age_steps=2)
    with pytest.raises(ContractViolation) as e:
        c.validate_chunk(_chunk(seq=1), current_seq=10, now=0.0, mission_id="m")
    assert e.value.code == "OBSERVATION_STALE"


def test_execution_budget_is_delivery_plus_horizon():
    """执行预算必须 = 交付预算 + 执行边界。否则 chunk 执行几步就"过期",chunking 失去意义
    (第一版正是把两个预算混用,被 test_execution_horizon_caps_actions_per_chunk 抓到)。"""
    c = PolicyContract(max_obs_age_steps=2, execution_horizon=3)
    assert c.execution_age_budget_steps == 5
    # 交付时按 2 步判;执行时按 5 步判 —— 第 3 个动作(age=3)在交付预算下"过期"但执行合法
    assert not c.is_fresh(7, None, current_seq=10, now=0.0)   # 交付预算:3 步 > 2 → 不新鲜
    assert c.is_executable(7, current_seq=10)                 # 执行预算:3 步 ≤ 5 → 可执行
    assert c.is_executable(5, current_seq=10)                 # 刚好 5 步
    assert not c.is_executable(4, current_seq=10)             # 6 步 → 超预算,丢弃
    assert not c.is_executable(None, current_seq=10)


def test_is_fresh_boundaries():
    c = PolicyContract(max_obs_age_steps=2, max_obs_age_s=0.5)
    assert c.is_fresh(8, None, current_seq=10, now=0.0)        # 刚好 2 步
    assert not c.is_fresh(7, None, current_seq=10, now=0.0)    # 3 步 → 过期
    assert not c.is_fresh(10, 0.0, current_seq=10, now=1.0)    # 时间维度过期
    assert not c.is_fresh(None, None, current_seq=10, now=0.0)  # 无 seq → 不新鲜


# ---- 运行时集成:执行边界真的生效 ------------------------------------------

def test_execution_horizon_caps_actions_per_chunk():
    """policy 一次吐 8 个动作,但契约只允许执行 3 个 → 必须换新 chunk(旧代码会一路执行到队列空)。"""
    events = []
    contract = PolicyContract(action_horizon=16, execution_horizon=3)
    rt = VLASkillRuntime(MockVLAPolicy(PolicyConfig(horizon=8), seed=0),
                         SafetyShield(), TabletopSim(), events=events, contract=contract)
    asyncio.run(rt.execute(SkillGoal("m", "pick", timeout_s=0.5)))
    exhausted = [e for e in events if e["event_type"] == "chunk_exhausted_horizon"]
    assert exhausted, "应触发执行边界并丢弃该 chunk 剩余动作"
    assert all(e["payload"]["execution_horizon"] == 3 for e in exhausted)
    assert all(e["payload"]["dropped"] > 0 for e in exhausted)


def test_chunk_over_action_horizon_is_rejected_never_executed():
    """policy 吐 8 个但契约 action_horizon=4 → 整块拒收,一个动作都不执行。"""
    events = []
    contract = PolicyContract(action_horizon=4, execution_horizon=4)
    rt = VLASkillRuntime(MockVLAPolicy(PolicyConfig(horizon=8), seed=0),
                         SafetyShield(), TabletopSim(), events=events, contract=contract)
    res = asyncio.run(rt.execute(SkillGoal("m", "pick", timeout_s=0.3)))
    rejects = [e for e in events if e["event_type"] == "chunk_rejected"
               and e["payload"].get("code") == "CHUNK_OVER_HORIZON"]
    assert rejects, "超长 chunk 应被契约拒收"
    assert res.steps == 0, "被拒的 chunk 不得执行任何动作"
    assert res.success is False
