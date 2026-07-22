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
    # D2:learned skill 上浮到编排层的两类。重试属 Skill Supervisor(skill 之上、编排之下),
    # 到了编排层就意味着"skill 那一层已经尽力了",故这里不再重试,只做降级归坞。
    SKILL_UNSAFE = "skill_unsafe"        # 安全停(shield must_stop)——绝不重试
    SKILL_FAILED = "skill_failed"        # 重试耗尽/不可重试失败后上浮


# 数值越小优先级越高。裁决的实际执行者是 observer 的检查顺序
# (graph.py:终态 → 电量 → 停滞,电量先于停滞即安全类抢占);
# 本表是该顺序的声明式记录,进事件日志供审计,不是运行时查表。
PRIORITY: dict[FaultClass, int] = {
    FaultClass.LOW_BATTERY: 0,
    FaultClass.NAV_BLOCKED: 1,
    FaultClass.NAV_UNREACHABLE: 2,
    FaultClass.SENSOR_FAULT: 3,
    FaultClass.TOOL_FAILURE: 4,
    FaultClass.SKILL_UNSAFE: 0,          # 安全类:与低电量同级,绝不因"再试一次"而重来
    FaultClass.SKILL_FAILED: 4,
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
    # 两类都【不在编排层重试】:重试是 Skill Supervisor 的职责(skill 之上、编排之下),
    # 故障能上浮到编排层,本身就说明 skill 那一层已经尽力了。编排层只做降级跳过 + 继续
    # 剩余任务(随后安全归坞),不再"多试一次"——否则就是两层抢救同一个错误(反模式)。
    FaultClass.SKILL_UNSAFE: ["skip_step_degraded"],
    FaultClass.SKILL_FAILED: ["skip_step_degraded"],
}


def next_stage(fclass: FaultClass, attempt: int) -> str:
    chain = RECOVERY_CHAINS[fclass]
    return chain[min(attempt, len(chain) - 1)]
