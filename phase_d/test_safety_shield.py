"""Phase D 安全核心回归测试:证明"policy 绕不过确定性安全投影"这条主张。

跑法:pytest phase_d/test_safety_shield.py -q(项目 venv,纯 stdlib)。
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from action_types import Action, EEState, Observation, SafeAction, _SHIELD_TOKEN  # noqa: E402
from mock_vla_policy import MockVLAPolicy, PolicyConfig  # noqa: E402
from safety_shield import SafetyShield, ShieldConfig  # noqa: E402
from tabletop_sim import TabletopSim  # noqa: E402


def _rollout(policy, shield, sim, steps=200):
    """同步跑一段:policy 出 chunk → 逐动作过 shield → SafeAction 送控制器。
    返回统计:是否有动作越界送达、must_stop 次数、夹取次数、末态是否在盒内。"""
    box = shield.cfg.box
    queue = []
    stats = {"must_stop": 0, "clamps": 0, "sent": 0, "ever_out_of_box": False}
    mission = "test"
    seq = 0
    for _ in range(steps):
        seq += 1
        obs = Observation(seq=seq, t=float(seq), ee=sim.ee)
        if not queue:
            queue = list(policy.predict_chunk(obs, mission).actions)
        raw = queue.pop(0)
        safe, info = shield.project(raw, sim.ee)
        if info.any_clamp:
            stats["clamps"] += 1
        if info.must_stop:
            stats["must_stop"] += 1
            sim.emergency_stop()
            break
        sim.send(safe)
        stats["sent"] += 1
        # 地面真值:控制器执行后末端必须仍在 workspace(shield 的硬保证)
        if not box.contains(sim.ee.pos):
            stats["ever_out_of_box"] = True
    return stats


# ---- 1. 结构性保证:SafeAction 只能由 shield 铸造 ----

def test_safeaction_cannot_be_constructed_directly():
    with pytest.raises(PermissionError):
        SafeAction((0, 0, 0, 0, 0, 0), 0.0)                    # 无令牌
    with pytest.raises(PermissionError):
        SafeAction((0, 0, 0, 0, 0, 0), 0.0, object())          # 错令牌


def test_controller_rejects_raw_action():
    sim = TabletopSim()
    with pytest.raises(TypeError):
        sim.send(Action((0.01, 0, 0, 0, 0, 0), 0.0))           # 裸 Action 送不进控制器


def test_shield_output_is_sendable():
    sim = TabletopSim()
    shield = SafetyShield()
    safe, info = shield.project(Action((0.01, 0, 0, 0, 0, 0), 0.5), sim.ee)
    assert isinstance(safe, SafeAction)
    sim.send(safe)                                             # 不抛错
    assert sim.steps == 1


# ---- 2. 对抗 policy 的动作永远不越界送达 ----

def test_huge_jump_triggers_hard_stop_never_escapes():
    """巨幅越界注入(~0.87m 跳)→ shield 判 must_stop(hard_stop),绝不执行、绝不越界。"""
    shield = SafetyShield()
    sim = TabletopSim()
    policy = MockVLAPolicy(PolicyConfig(inject_out_of_bounds=True), seed=1)
    stats = _rollout(policy, shield, sim, steps=300)
    assert stats["ever_out_of_box"] is False          # 关键不变量:没有越界动作被执行
    assert stats["must_stop"] >= 1                     # 荒谬幅度 → 停(不是夹)


def test_target_outside_box_gets_workspace_clamped():
    """目标在盒外,policy 用名义步长走到边界后,每步被 workspace 夹回,不 hard_stop、不越界。"""
    shield = SafetyShield()
    sim = TabletopSim()
    policy = MockVLAPolicy(PolicyConfig(target_pos=(1.0, 0.0, 0.06)), seed=4)
    stats = _rollout(policy, shield, sim, steps=300)
    assert stats["ever_out_of_box"] is False
    assert stats["must_stop"] == 0                     # 名义步长不触发 hard_stop
    assert stats["clamps"] > 0                          # 到边界后每步都被夹


def test_nan_policy_triggers_must_stop_not_execution():
    shield = SafetyShield()
    sim = TabletopSim()
    policy = MockVLAPolicy(PolicyConfig(inject_nan=True), seed=2)
    stats = _rollout(policy, shield, sim, steps=300)
    assert stats["must_stop"] >= 1                    # NaN → must_stop
    assert all(math.isfinite(v) for v in sim.ee.pos)  # 末端从未被 NaN 污染


def test_jitter_policy_stays_in_bounds():
    shield = SafetyShield()
    sim = TabletopSim()
    policy = MockVLAPolicy(PolicyConfig(jitter=0.05), seed=3)   # 大抖动
    stats = _rollout(policy, shield, sim, steps=300)
    assert stats["ever_out_of_box"] is False


# ---- 3. 名义 happy path:无 must_stop,能抓到方块 ----

def test_nominal_policy_reaches_and_grasps():
    shield = SafetyShield()
    sim = TabletopSim()
    policy = MockVLAPolicy(PolicyConfig(), seed=0)
    stats = _rollout(policy, shield, sim, steps=400)
    assert stats["must_stop"] == 0
    assert sim.block.grasped is True                  # 玩具任务完成:方块被抓住


# ---- 4. 单步限幅确实生效 ----

def test_translation_step_is_clamped():
    shield = SafetyShield(ShieldConfig(max_translation_per_step=0.02))
    state = EEState((0.0, 0.0, 0.20), (0, 0, 0), 0.0)
    safe, info = shield.project(Action((0.10, 0.0, 0.0, 0, 0, 0), 0.0), state)  # 请求 0.1m
    mag = math.sqrt(sum(c * c for c in safe.delta[:3]))
    assert mag <= 0.02 + 1e-9                          # 被夹到 ≤ 单步上限
    assert info.clamped_translation is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
