"""SafetyShield —— 独立于模型的确定性 action projection(review 局限 #7)。

它是 policy 与控制器之间的最后确定性边界:每一个动作都被投影进硬约束,越界就夹回,
荒谬(NaN/巨幅)就令 must_stop。它【不看模型的 confidence】,只按几何/运动学限位判断——
"模型和 LangGraph 都不能是最后安全边界"这条,靠这一层落地。

约束(桌面 relative-EEF 玩具设定,数值可配):
  · workspace box:末端绝对位置必须留在盒子里(会越界就把该轴 delta 夹到边界)
  · 单步平移幅度 ≤ max_translation_per_step
  · 单步旋转幅度 ≤ max_rotation_per_step
  · gripper ∈ [0, 1]
  · must_stop:动作非有限(NaN/inf)、或单步平移幅度超过 hard_stop 倍数、或当前状态已越界
输出 ProjectionInfo 记录每类夹取是否发生 + 修正量(correction),供上层统计"policy 顶着限位跑"。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from action_types import (Action, EEState, SafeAction, _mint_safe,
                          _register_shield_token)


@dataclass(frozen=True)
class WorkspaceBox:
    xmin: float = -0.30; xmax: float = 0.30
    ymin: float = -0.30; ymax: float = 0.30
    zmin: float = 0.02;  zmax: float = 0.40   # z=0 桌面,不许穿桌

    def contains(self, p, margin=0.0) -> bool:
        x, y, z = p
        return (self.xmin - margin <= x <= self.xmax + margin
                and self.ymin - margin <= y <= self.ymax + margin
                and self.zmin - margin <= z <= self.zmax + margin)

    def clamp(self, p):
        x, y, z = p
        return (min(max(x, self.xmin), self.xmax),
                min(max(y, self.ymin), self.ymax),
                min(max(z, self.zmin), self.zmax))


@dataclass
class ShieldConfig:
    box: WorkspaceBox = field(default_factory=WorkspaceBox)
    max_translation_per_step: float = 0.02      # 米/步
    max_rotation_per_step: float = 0.10         # 弧度/步
    hard_stop_translation: float = 0.20         # 单步平移超此值 → must_stop(明显失控)
    state_margin: float = 0.05                  # 当前状态越界容差(超此才 must_stop)


@dataclass
class ProjectionInfo:
    must_stop: bool = False
    reason: str = ""
    clamped_translation: bool = False           # 平移幅度被夹
    clamped_rotation: bool = False              # 旋转幅度被夹
    clamped_workspace: bool = False             # 因越 workspace 被夹
    clamped_gripper: bool = False               # 夹爪出 [0,1] 被夹
    correction: float = 0.0                     # ||raw - safe|| 的粗略幅度(越大=越顶限位)

    @property
    def any_clamp(self) -> bool:
        return (self.clamped_translation or self.clamped_rotation
                or self.clamped_workspace or self.clamped_gripper)


def _norm3(v) -> float:
    return math.sqrt(sum(c * c for c in v))


class SafetyShield:
    def __init__(self, config: ShieldConfig | None = None):
        self.cfg = config or ShieldConfig()
        self.__token = _register_shield_token()   # 本实例专属铸造令牌(D1:不再是模块常量)

    def project(self, action: Action, state: EEState) -> tuple[SafeAction, ProjectionInfo]:
        cfg = self.cfg
        info = ProjectionInfo()
        hold = _mint_safe((0.0, 0.0, 0.0, 0.0, 0.0, 0.0), state.gripper, self.__token)  # must_stop 时保持

        # 1) 非有限动作 → 立即停(绝不把 NaN 送控制器)
        if not action.is_finite():
            info.must_stop = True; info.reason = "non_finite_action"
            return hold, info
        # 2) 当前状态已明显越界 → 停(不在越界状态上继续叠 delta)
        if not cfg.box.contains(state.pos, margin=cfg.state_margin):
            info.must_stop = True; info.reason = "state_out_of_workspace"
            return hold, info

        dx, dy, dz, dr, dp, dyaw = action.delta
        raw_delta = (dx, dy, dz)
        # 3) 荒谬幅度 → 停
        if _norm3(raw_delta) > cfg.hard_stop_translation:
            info.must_stop = True; info.reason = "translation_hard_stop"
            return hold, info

        # 4) 单步平移限幅(等比缩放,保方向)
        tnorm = _norm3(raw_delta)
        if tnorm > cfg.max_translation_per_step and tnorm > 0:
            s = cfg.max_translation_per_step / tnorm
            dx, dy, dz = dx * s, dy * s, dz * s
            info.clamped_translation = True
        # 5) 单步旋转限幅(逐轴夹)
        def clamp_rot(a):
            lim = cfg.max_rotation_per_step
            if a > lim: info.clamped_rotation = True; return lim
            if a < -lim: info.clamped_rotation = True; return -lim
            return a
        dr, dp, dyaw = clamp_rot(dr), clamp_rot(dp), clamp_rot(dyaw)

        # 6) workspace 投影:落点越盒 → 夹回边界(逐轴)
        endpoint = (state.pos[0] + dx, state.pos[1] + dy, state.pos[2] + dz)
        clamped_ep = cfg.box.clamp(endpoint)
        if clamped_ep != endpoint:
            info.clamped_workspace = True
            dx = clamped_ep[0] - state.pos[0]
            dy = clamped_ep[1] - state.pos[1]
            dz = clamped_ep[2] - state.pos[2]

        # 7) gripper 夹到 [0,1]
        g = action.gripper
        if g < 0.0 or g > 1.0:
            info.clamped_gripper = True
            g = min(max(g, 0.0), 1.0)

        safe_delta = (dx, dy, dz, dr, dp, dyaw)
        # 修正量:平移+旋转+夹爪 与原始的差(粗略,用于统计 policy 顶限位程度)
        info.correction = (
            _norm3((safe_delta[0] - action.delta[0],
                    safe_delta[1] - action.delta[1],
                    safe_delta[2] - action.delta[2]))
            + abs(safe_delta[3] - action.delta[3])
            + abs(safe_delta[4] - action.delta[4])
            + abs(safe_delta[5] - action.delta[5])
            + abs(g - action.gripper))
        return _mint_safe(safe_delta, g, self.__token), info
