"""Phase D 动作/观测类型 + 结构性安全保证的基石。

设计要点(对照 docs/POSITIONING.md、RECOVERY_OWNERSHIP.md):
  · 动作表示 = relative end-effector delta(跨 embodiment 更好迁移,见 review §四):
    [dx, dy, dz, droll, dpitch, dyaw, gripper] —— 前 6 维相对当前位姿的增量,末维夹爪目标 0..1。
  · **类型级保证**:控制器只接受 `SafeAction`,而 `SafeAction` 需 `_authorized` 私有令牌构造,
    正常路径上只有 `SafetyShield.project()` 会铸造它。policy 吐的是裸 `Action`,类型上就无法
    直接送到控制器——沿 runtime 执行路径,"learned policy 绕不过确定性安全投影"成立。

    **D1 已加固**:令牌不再是模块级常量(旧代码 `from action_types import _SHIELD_TOKEN`
    一行即可伪造),改为**每个 SafetyShield 实例私有**且需在活跃令牌表登记。

    **诚实边界(仍不冒充)**:同进程 Python 里**不存在**真正不可绕过的边界——能 import 本模块
    就仍有办法构造令牌。加固关掉的是"一行 import 就能伪造"这个具体洞,并让误用/顺手绕过不可能;
    它**不是**安全隔离。真正不可绕需要**进程隔离**(shield 独立进程 + IPC),属后续工作。
"""
from __future__ import annotations

import math
import weakref
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


# —— SafeAction 的铸造令牌(D1 加固)——
#
# 旧实现是一个**模块级常量** `_SHIELD_TOKEN = object()`,于是
# `from action_types import _SHIELD_TOKEN` 就能伪造 SafeAction —— codex 评审据此指出
# "结构性保证"名不副实。现改为:令牌是 **每个 SafetyShield 实例私有** 的对象,且必须在
# 活跃令牌表里登记过;模块**不再导出任何常量令牌**。
#
# **诚实边界(不要过度声称)**:同进程 Python 里**不存在**真正不可绕过的边界 —— 只要能 import
# 本模块,就仍能构造 `_ShieldToken()` 之类。本次加固关掉的是"一行 import 就能伪造"这个具体洞,
# 并让误用/顺手绕过变得不可能;它**不是**安全隔离。真正不可绕的边界需要**进程隔离**
# (shield 独立进程 + IPC,动作必须往返一次),那是 D1 之后的工作,当前**不冒充为已具备**。
class _ShieldToken:
    """SafetyShield 实例专属的铸造令牌(不作为模块常量导出)。"""
    __slots__ = ("__weakref__",)


_live_shield_tokens: "weakref.WeakSet[_ShieldToken]" = weakref.WeakSet()


def _register_shield_token() -> _ShieldToken:
    """仅供 SafetyShield.__init__ 调用:铸造并登记一枚该实例专属令牌。"""
    t = _ShieldToken()
    _live_shield_tokens.add(t)
    return t


@dataclass(frozen=True)
class SafeAction:
    """经过 SafetyShield 投影后的动作,是控制器唯一接受的类型。
    直接 `SafeAction(...)` 会抛错:必须走 `SafetyShield.project()`。"""
    delta: tuple[float, float, float, float, float, float]
    gripper: float
    _authorized: object = field(default=None, repr=False)

    def __post_init__(self):
        if not isinstance(self._authorized, _ShieldToken) or \
                self._authorized not in _live_shield_tokens:
            raise PermissionError(
                "SafeAction 只能由 SafetyShield.project() 构造 —— "
                "policy 不能绕过安全投影直接生成可执行动作")


def _mint_safe(delta, gripper, token) -> SafeAction:
    """仅供 SafetyShield 使用:用**该实例的**令牌铸造 SafeAction。"""
    return SafeAction(tuple(delta), float(gripper), token)
