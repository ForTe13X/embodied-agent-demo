"""MockVLAPolicy —— 桌面 pick 玩具任务的【mock VLA】(不是真模型,纯确定性桩)。

它扮演 review §四/§六 里的 VLA:输入观测(这里只用 proprioception),输出一段 relative-EEF
**action chunk**(预测未来 H 步)。目的不是"聪明",而是给 runtime + SafetyShield 一个可控的
对手 —— 能按开关吐出【越界 / NaN / 抖动】动作,验证下游确定性约束真的兜得住。

诚实标注:这不是学习到的策略,不训练、不看图像;真实 VLA 换到这个接口即可(predict_chunk 同签名)。
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

from action_types import Action, ChunkResult, Observation
from policy_contract import POLICY_CONTRACT_VERSION


@dataclass
class PolicyConfig:
    target_pos: tuple[float, float, float] = (0.15, 0.05, 0.06)  # 方块上方一点
    target_gripper: float = 1.0                  # 1=合(抓)
    nominal_step: float = 0.015                  # 名义单步平移(略小于 shield 的 0.02)
    horizon: int = 8                             # 一次预测多少步(action chunk 长度)
    # —— 对抗开关(默认全关=nominal happy path)——
    # 注入是【确定性】的(按全局动作序号,可复现):第 inject_at 个动作起、每 inject_period 个注入一次
    inject_out_of_bounds: bool = False           # 巨幅 delta(冲出 workspace)→ 应触发 shield hard_stop
    inject_nan: bool = False                     # NaN → 应触发 shield must_stop
    jitter: float = 0.0                          # 每步叠加的高斯噪声幅度
    inject_at: int = 2                            # 第几个动作开始注入
    inject_period: int = 5                        # 每隔几个动作注入一次


class MockVLAPolicy:
    # D1 版本化 Policy Contract:policy 必须声明它遵循的契约版本,runtime 装载期校验 major 相符。
    # 真实 VLA 接入时同样要声明(权重换代 = 可能换契约,不能只靠"跑起来看")。
    contract_version = POLICY_CONTRACT_VERSION

    def __init__(self, config: PolicyConfig | None = None, seed: int = 0):
        self.cfg = config or PolicyConfig()
        self.rng = random.Random(seed)
        self.calls = 0
        self._act_count = 0        # 全局动作序号(确定性注入用)

    def _should_inject(self) -> bool:
        c = self._act_count
        return c >= self.cfg.inject_at and (c - self.cfg.inject_at) % self.cfg.inject_period == 0

    def predict_chunk(self, obs: Observation, mission_id: str) -> ChunkResult:
        """从当前观测开环预测 H 步:每步朝 target 走 nominal_step,并按开关注入对抗。"""
        cfg = self.cfg
        self.calls += 1
        actions: list[Action] = []
        # 开环内部推演当前位姿(chunk 后半段基于"预测的"位姿,天然更陈旧——review §三)
        px, py, pz = obs.ee.pos
        for k in range(cfg.horizon):
            tx, ty, tz = cfg.target_pos
            vx, vy, vz = tx - px, ty - py, tz - pz
            dist = math.sqrt(vx * vx + vy * vy + vz * vz)
            if dist > 1e-6:
                s = min(cfg.nominal_step, dist) / dist
                dx, dy, dz = vx * s, vy * s, vz * s
            else:
                dx = dy = dz = 0.0
            # 抖动
            if cfg.jitter > 0:
                dx += self.rng.gauss(0, cfg.jitter)
                dy += self.rng.gauss(0, cfg.jitter)
                dz += self.rng.gauss(0, cfg.jitter)
            inj = self._should_inject()
            # 越界注入:确定性地放一个冲出盒子的巨幅平移(~0.87m,应触发 shield hard_stop)
            if cfg.inject_out_of_bounds and inj:
                dx, dy, dz = 0.5, 0.5, 0.5
            # NaN 注入(确定性)
            if cfg.inject_nan and inj:
                dx = float("nan")
            gripper = cfg.target_gripper if dist < 0.03 else 0.0  # 近了才合爪
            actions.append(Action((dx, dy, dz, 0.0, 0.0, 0.0), gripper))
            self._act_count += 1
            # 推演位姿前进(用注入前的名义位移推,避免 NaN 污染开环)
            px, py, pz = px + (dx if math.isfinite(dx) else 0.0), py + dy, pz + dz
        return ChunkResult(mission_id=mission_id, observation_seq=obs.seq,
                           actions=tuple(actions))
