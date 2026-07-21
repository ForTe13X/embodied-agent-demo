"""运行期访问地理围栏(F-01:transit 强制)。

**gap**:注册表门禁只校验导航【目标节点】的访问级——它挡不住底盘在【途中】实际穿过未授权的
受限/禁入区。mock 的 `world.route()` 会主动绕开未授权 transit 节点(transit 必须 free 或已授权),
所以 mock 从不发生穿越;但**真实 Nav2 在 costmap 上规划,r1 的"受限"只是拓扑/注册表层规则、
不在 costmap keepout 里**,故重规划(避开受阻边)可能让机器人物理穿过 r1——只有目标被门禁,
轨迹没有。Phase B 已如实记录此"access-盲重规划"(见 phase_b/FINDINGS.md),但此前只**披露**、
未**强制**。

`TransitGuard` 是与 adapter 无关的**确定性运行期监视器**:喂给它机器人【实际所在节点】的位置流,
一旦踏入禁入区、或未被本次导航授权的受限区,即判定 transit 违规。adapter 的控制环据此立即取消
目标(安全停),并把 `transit_violation` 终态上浮给编排层。

两层模型(纵深防御):
  - **预防**:把访问级下推进 costmap keepout,让 Nav2 的规划器根本不把轨迹画进 r1(真实栈的
    ROS 侧工作,是彻底不进入)。
  - **强制/检测**(本模块):不依赖规划器是否 access-aware——直接盯实际位置流,进了就停。规划器
    有 bug、keepout 有缝、定位漂移时,这一层仍然生效。

围栏在消融(gates_off)下**关闭**:消融的目的就是"拿掉安全门禁看会怎样",运行期围栏也是门禁
之一;关掉它,地面真值 SafetyMonitor 才能如实测到未拦截时的违规。

**采样与误停(安全审查,诚实标注两个方向)**:`TransitGuard` 本身是纯判定;真实 adapter 喂给它的是
`_cur_node()` 的**启发式最近邻 + 滞回标签**,不是几何围栏多边形。这带来两个方向的不精确:
(1) *欠检*——快速/贴边穿越可能整节点未被采到;(2) *误停*——一条合法轨迹掠过受限 waypoint 的
Voronoi cell 时,单次采样翻标签可能误停正常任务。这是"安全优先于可用性"的取舍:宁可在受限区边缘
停下也不放行穿越。缓解出口:真实 adapter 的 `geo_dwell_samples`(受限区需连续 N 次采样命中才停,
禁入区始终单采样即停)。彻底消解两个方向都要靠**把 access 下推进 costmap keepout**(预防层)。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Optional


@dataclass(frozen=True)
class TransitViolation:
    kind: str      # "forbidden_transit" | "unauthorized_restricted_transit"
    node: str
    access: str


class TransitGuard:
    """纯决策组件:给定"节点→访问级"查询函数,判定单次【进入某节点】是否构成 transit 违规。

    无状态、无副作用——强制动作(取消目标 / 上浮终态)由各 adapter 的控制环负责。
    这样同一套判定逻辑可被 mock server 与真实 rclpy adapter 复用,且能在宿主 venv 里纯逻辑单测。
    """

    def __init__(self, access_of: Callable[[str], str]):
        self._access_of = access_of

    def check(self, node: Optional[str], *,
              authorized_zones: Optional[Iterable[str]] = None,
              enabled: bool = True) -> Optional[TransitViolation]:
        """`node` 为机器人刚进入的节点;`authorized_zones` 为本次导航已授权可进入的受限节点集合
        (来自 HITL token 的 scope);`enabled=False`(消融)时一律放行。返回违规或 None。"""
        if not enabled or node is None:
            return None
        try:
            access = self._access_of(node)
        except KeyError:
            return None            # 图外节点不归围栏管(路由/白名单另有拒绝)
        if access == "forbidden":
            return TransitViolation("forbidden_transit", node, access)
        if access == "restricted" and node not in set(authorized_zones or ()):
            return TransitViolation("unauthorized_restricted_transit", node, access)
        return None
