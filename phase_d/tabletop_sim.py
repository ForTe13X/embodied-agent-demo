"""极简【运动学】桌面 sim + 控制器。无物理引擎——只够测 runtime / SafetyShield。

诚实标注(对照 docs/POSITIONING.md 边界):这不是物理仿真,不模拟接触力、摩擦、碰撞动力学;
末端位姿直接积分 SafeAction 的 delta(理想跟踪),方块在夹爪合拢且足够近时被"抓住"随动。
它验证的是 runtime 时序 + 安全投影 + 抓取后置条件,不验证真实操作物理。

控制器的关键:`send()` 只收 `SafeAction`。裸 `Action` 送不进来(类型 + SafeAction 私有令牌
双重把关)—— "policy 绕不过 shield"落到接口上。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from action_types import EEState, SafeAction


@dataclass
class Block:
    pos: tuple[float, float, float] = (0.15, 0.05, 0.03)
    grasped: bool = False


@dataclass
class TabletopSim:
    ee: EEState = field(default_factory=lambda: EEState((-0.10, -0.10, 0.20), (0, 0, 0), 0.0))
    block: Block = field(default_factory=Block)
    grasp_dist: float = 0.04        # 夹爪合且末端距方块 < 此值 → 抓住
    grasp_close: float = 0.7        # gripper >= 此值算"合"
    emergency: bool = False
    steps: int = 0

    def send(self, safe: SafeAction) -> None:
        """唯一执行入口。只接受 SafeAction —— 结构性拒绝未经 shield 的动作。"""
        if not isinstance(safe, SafeAction):
            raise TypeError(f"控制器只接受 SafeAction(经 SafetyShield 投影),收到 {type(safe).__name__}")
        if self.emergency:
            return
        self.steps += 1
        dx, dy, dz, dr, dp, dyaw = safe.delta
        px, py, pz = self.ee.pos
        npos = (px + dx, py + dy, pz + dz)
        nrot = (self.ee.rot[0] + dr, self.ee.rot[1] + dp, self.ee.rot[2] + dyaw)
        self.ee = EEState(npos, nrot, safe.gripper)
        # 抓取:合爪且够近 → 方块被抓住随动
        if not self.block.grasped and safe.gripper >= self.grasp_close \
                and self._dist(npos, self.block.pos) < self.grasp_dist:
            self.block.grasped = True
        if self.block.grasped:
            self.block = Block(pos=npos, grasped=True)

    def hold_position(self) -> None:
        pass  # 运动学 sim:不发即不动

    def emergency_stop(self) -> None:
        self.emergency = True

    @staticmethod
    def _dist(a, b) -> float:
        return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))

    def ee_out_of(self, box) -> bool:
        return not box.contains(self.ee.pos)
