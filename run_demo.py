"""交互演示:真实时间节奏 + 中文叙述 + 可选交互式 HITL。

用法(在 .venv 里):
  python run_demo.py                        # 基线巡检
  python run_demo.py --scenario blocked     # 导航受阻 → 水位检测 → 绕行恢复
  python run_demo.py --scenario battery     # 低电量 → 回充 → 断点续跑
  python run_demo.py --scenario unreachable # 点位不可达 → 替代点降级
  python run_demo.py --scenario restricted  # 受限区 + HITL 审批 token 全流程
  python run_demo.py --interactive          # HITL 问题由你在控制台回答
  python run_demo.py --nl "去 A 区巡检,发现异常拍照上报"   # 规则意图解析(离线)
"""
from __future__ import annotations

import argparse
import asyncio
import io
import sys

from rich.console import Console

from embodied_agent.evaluation.scenarios import (
    APPROVE_DEGRADE,
    APPROVE_SKIP,
    Condition,
    fault_specs_for,
)
from embodied_agent.graph import run_graph
from embodied_agent.hitl import InteractiveHITLPolicy, ScriptedHITLPolicy
from embodied_agent.intent import rule_parse
from embodied_agent.runtime import RunConfig, build_runtime
from embodied_agent.world import default_map

console = Console()

SCENARIOS = {
    "baseline": Condition("baseline", []),
    "blocked": Condition("nav_blocked", ["nav_blocked"], hitl_rules=APPROVE_SKIP),
    "unreachable": Condition("nav_unreachable", ["nav_unreachable"],
                             hitl_rules=APPROVE_SKIP),
    "sensor": Condition("sensor_fault", ["sensor_fault"],
                        hitl_rules=APPROVE_DEGRADE),
    "battery": Condition("low_battery", ["low_battery"]),
    "tool": Condition("tool_failure", ["tool_failure"]),
    "compound": Condition("compound", ["nav_blocked", "low_battery"],
                          hitl_rules=APPROVE_SKIP),
}

STYLE = {
    "fault_injector": "red",
    "safety_monitor": "bold red",
    "exception_manager": "yellow",
    "replanner": "magenta",
    "hitl": "bold cyan",
}

NARRATE = {
    "plan_built": lambda p: f"计划就绪:{len(p['steps'])} 步 —— {p['mission']}",
    "goal_accepted": lambda p: f"出发 → {p['target']}(路线 {'→'.join(p['route'])})",
    "goal_finished": lambda p: f"目标 {p['goal_id']} 终态:{p['status']}"
                               + (f"({p['reason']})" if p.get("reason") else ""),
    "fault_armed": lambda p: f"[故障注入] 已武装:{p['fault_id']}",
    "fault_activated": lambda p: f"[故障注入] 激活:{p['fault_id']}",
    "watchdog_triggered": lambda p: f"水位检测触发:{p['kind']}",
    "fault_classified": lambda p: f"故障分类:{p['fclass']}(第{p['attempt']+1}次)→ 恢复阶段 {p['stage']}",
    "recovery_applied": lambda p: f"恢复动作落地:{p['action']['type']}",
    "queue_snapshot": lambda p: f"任务队列快照({len(p['snapshot'])} 步)→ 回坞充电",
    "queue_resumed": lambda p: f"断点续跑:恢复 {len(p['steps'])} 步",
    "hitl_request": lambda p: f"请求人工确认:{p['message']}",
    "hitl_decision": lambda p: f"人工决定:{p['decision']}",
    "finding_reported": lambda p: f"异常上报:{p['label']} @ {p['node_id']}({p['image_id']})",
    "violation": lambda p: f"!!! 安全违规:{p['kind']}",
    "guardrail_rejection": lambda p: f"门禁拦截 {p['tool']}:{p['code']}",
    "circuit_open": lambda p: f"熔断:{p['tool']}",
    "step_skipped": lambda p: f"跳过步骤(降级):{p.get('step', p)}",
    "run_summary": lambda p: "任务结束 —— "
        f"terminal={p.get('outcome_hint') or 'ok'} 巡检={p.get('visited')} "
        f"上报={len(p.get('anomalies_reported', []))} HITL={p.get('hitl_consults')}",
}


def narrator(event: dict) -> None:
    fn = NARRATE.get(event["event_type"])
    if fn is None:
        return
    style = STYLE.get(event["actor"], "white")
    console.print(f"[dim]t={event['tick']:>3}[/dim] "
                  f"[{style}]{event['actor']:<18}[/{style}] "
                  f"{fn(event['payload'])}")


async def run_scenario(args) -> None:
    cond = SCENARIOS[args.scenario]
    intent = None
    if args.nl:
        if args.llm:
            from embodied_agent.llm_intent import parse_intent
            intent, source = parse_intent(args.nl, default_map())
            console.print(f"[bold]意图解析(provider={source})[/bold]:"
                          f"{intent.model_dump()}")
        else:
            intent = rule_parse(args.nl, default_map())
            console.print(f"[bold]意图解析(规则/离线)[/bold]:{intent.model_dump()}")
    cfg = RunConfig(condition=cond.name, seed=args.seed,
                    fault_specs=fault_specs_for(cond),
                    hitl_rules=cond.hitl_rules,
                    tick_duration_s=args.tick, intent=intent)
    hitl = InteractiveHITLPolicy() if args.interactive else None
    rt = build_runtime(cfg, hitl=hitl)
    rt.event_log.on_emit = narrator
    console.rule(f"[bold]场景:{args.scenario}(seed={args.seed},mock adapter)")
    await run_graph(rt)


async def run_restricted_demo(args) -> None:
    """受限区审批 token 全流程(不经图,直接演示注册表门禁)。"""
    cfg = RunConfig(condition="restricted_demo", seed=args.seed, fault_specs=[],
                    tick_duration_s=args.tick)
    hitl = InteractiveHITLPolicy() if args.interactive else \
        ScriptedHITLPolicy([(r".*", "approve")])
    rt = build_runtime(cfg, hitl=hitl)
    rt.event_log.on_emit = narrator
    console.rule("[bold]场景:restricted —— 无 token 被拒 → HITL 审批 → 放行(token 一次一用)")
    res = await rt.registry.call("navigate_to", {"node_id": "r1"})
    assert not res.ok
    tok = await rt.registry.call("ask_human_confirmation", {
        "message": "需要经过受限区捷径 r1,是否批准?",
        "scope": "navigate_to:r1"})
    if not tok.data["approved"]:
        console.print("[red]HITL 拒绝,安全停。")
        return
    res = await rt.registry.call("navigate_to", {
        "node_id": "r1", "approval_token": tok.data["approval_token"]})
    gid = res.data["goal_id"]
    while await rt.adapter.result(gid) is None:
        await rt.adapter.wait(1)
    console.print(f"[green]已进入 r1(已授权,地面真值违规数={len(rt.safety.violations)})")


def main() -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")  # GBK 防线
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", default="baseline",
                        choices=[*SCENARIOS, "restricted"])
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--tick", type=float, default=0.12,
                        help="每 tick 的真实秒数;0=瞬时")
    parser.add_argument("--interactive", action="store_true",
                        help="HITL 由控制台交互回答")
    parser.add_argument("--nl", default=None,
                        help="自然语言任务(默认规则解析,离线)")
    parser.add_argument("--llm", action="store_true",
                        help="意图解析走 provider 链:LM Studio → Anthropic → 规则兜底")
    args = parser.parse_args()
    if args.scenario == "restricted":
        asyncio.run(run_restricted_demo(args))
    else:
        asyncio.run(run_scenario(args))


if __name__ == "__main__":
    main()
