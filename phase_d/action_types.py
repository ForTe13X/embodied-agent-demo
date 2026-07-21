"""Phase D 动作/观测类型 + 结构性安全保证的基石。

设计要点(对照 docs/POSITIONING.md、RECOVERY_OWNERSHIP.md):
  · 动作表示 = relative end-effector delta(跨 embodiment 更好迁移,见 review §四):
    [dx, dy, dz, droll, dpitch, dyaw, gripper] —— 前 6 维相对当前位姿的增量,末维夹爪目标 0..1。
  · **类型级保证**:控制器只接受 `SafeAction`,而 `SafeAction` 需 `_authorized` 私有令牌构造,
    正常路径上只有 `SafetyShield.project()` 会铸造它。policy 吐的是裸 `Action`,类型上就无法
    直接送到控制器——沿 runtime 执行路径,"learned policy 绕不过确定性安全投影"成立。

    **诚实边界(codex 评审,D1 待办)**:这是**同进程内的约定**,不是**不可绕过**的安全边界——
    `from action_types import _SHIELD_TOKEN` 就能取到令牌并伪造 `SafeAction`。它挡得住"误用/
    顺手绕过",挡不住蓄意绕过。真正不可绕的边界需要进程/能力隔离(如 shield 独立进程 + IPC,
    或令牌不可从模块命名空间取得),属 D1 工作,**当前不冒充为已具备**。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

ACTION_DIM = 7  # dx dy dz droll dpitch dyaw gripper


@dataclass(frozen=True)
class Action:
    """policy 输出的【原始、未审查】动作。永远不能直接送控制器。"""
    delta: tuple[float, float, float, float, float, float]  # dx dy dz droll dpitch dyaw
    gripper: float                                          # 目标夹爪 0..1

    def is_finite(self) -> bool:
        return all(math.isfinite(v) for v in self.delta) and math.isfinite(self.gripper)


@dataclass(frozen=True)
class EEState:
    """本体观测(proprioception)的最小子集:末端位姿 + 夹爪。"""
    pos: tuple[float, float, float]                        # x y z(绝对,米)
    rot: tuple[float, float, float]                        # roll pitch yaw(绝对,弧度)
    gripper: float


@dataclass(frozen=True)
class Observation:
    """带序号+时间戳的观测(runtime 用序号判 stale;真实系统这里还含图像)。"""
    seq: int
    t: float
    ee: EEState
    # 真实系统:images=..., 这里 sim 用不到,留字段位


@dataclass(frozen=True)
class ChunkResult:
    """一次 policy 推理的产物:一段 action chunk + 它基于哪个观测序号(判 stale 用)。"""
    mission_id: str
    observation_seq: int
    actions: tuple[Action, ...]
    inference_ms: float = 0.0


# —— 结构性保证的核心:SafeAction 的私有构造令牌 ——
_SHIELD_TOKEN = object()


@dataclass(frozen=True)
class SafeAction:
    """经过 SafetyShield 投影后的动作,是控制器唯一接受的类型。
    直接 `SafeAction(...)` 会抛错:必须走 `SafetyShield.project()`。"""
    delta: tuple[float, float, float, float, float, float]
    gripper: float
    _authorized: object = field(default=None, repr=False)

    def __post_init__(self):
        if self._authorized is not _SHIELD_TOKEN:
            raise PermissionError(
                "SafeAction 只能由 SafetyShield.project() 构造 —— "
                "policy 不能绕过安全投影直接生成可执行动作")


def _mint_safe(delta, gripper) -> SafeAction:
    """仅供 SafetyShield 使用:用私有令牌铸造 SafeAction。"""
    return SafeAction(tuple(delta), float(gripper), _SHIELD_TOKEN)
