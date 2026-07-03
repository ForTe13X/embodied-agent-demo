"""异常恢复:确定性故障分类 + 预注册恢复策略表(评审 M3)。

优先级:安全类(低电量)抢占任务类(受阻/不可达)——复合故障 case 的裁决规则,预注册。
每类故障一条链,链内按该类累计出现次数逐级升级;链尾多为 HITL(升级人工是显式终态,
不是失败,也不冒充成功——指标里单独一列)。
"""
from __future__ import annotations

from enum import Enum


class FaultClass(str, Enum):
    LOW_BATTERY = "low_battery"          # 安全类,最高优先
    NAV_BLOCKED = "nav_blocked"
    NAV_UNREACHABLE = "nav_unreachable"
    SENSOR_FAULT = "sensor_fault"
    TOOL_FAILURE = "tool_failure"


# 数值越小优先级越高。裁决的实际执行者是 observer 的检查顺序
# (graph.py:终态 → 电量 → 停滞,电量先于停滞即安全类抢占);
# 本表是该顺序的声明式记录,进事件日志供审计,不是运行时查表。
PRIORITY: dict[FaultClass, int] = {
    FaultClass.LOW_BATTERY: 0,
    FaultClass.NAV_BLOCKED: 1,
    FaultClass.NAV_UNREACHABLE: 2,
    FaultClass.SENSOR_FAULT: 3,
    FaultClass.TOOL_FAILURE: 4,
}

# 预注册恢复链(与 faults.yaml 的 expected_recovery_chain 一致)
RECOVERY_CHAINS: dict[FaultClass, list[str]] = {
    FaultClass.NAV_BLOCKED: [
        "retry_same_route", "replan_avoid_edge", "substitute_target", "escalate_hitl",
    ],
    FaultClass.NAV_UNREACHABLE: ["substitute_target", "degraded_report"],
    FaultClass.SENSOR_FAULT: ["skip_step_degraded", "pause_and_escalate"],
    FaultClass.LOW_BATTERY: ["dock_recharge_resume"],
    FaultClass.TOOL_FAILURE: ["skip_step_degraded", "failure_report_and_degrade"],
}


def next_stage(fclass: FaultClass, attempt: int) -> str:
    chain = RECOVERY_CHAINS[fclass]
    return chain[min(attempt, len(chain) - 1)]
