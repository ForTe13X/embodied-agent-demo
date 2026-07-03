"""被动回放:从事件日志重放一个 run 的决策轨迹(评审 minor:回放承诺的兑现)。

用法:python -m embodied_agent.replay runs/nav_blocked/seed_0.jsonl [--all]
默认只放决策级事件;--all 放全部(含轮询)。
主动重执行回放 = 重跑同 seed(确定性由回归测试保证):
  python -m embodied_agent.evaluation.harness --conditions nav_blocked --seeds 0
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

DECISION_EVENTS = {
    "run_start", "plan_built", "fault_armed", "fault_activated",
    "goal_accepted", "goal_finished", "watchdog_triggered",
    "fault_classified", "candidates_enumerated", "candidate_chosen",
    "recovery_applied", "queue_snapshot", "queue_resumed",
    "hitl_request", "hitl_decision", "guardrail_rejection", "circuit_open",
    "token_consumed", "finding_reported", "step_skipped", "violation",
    "run_summary", "run_crashed", "attempt", "recovery_exhausted",
    "cancel_noop",
}


def main() -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("logfile", type=Path)
    parser.add_argument("--all", action="store_true", help="包含轮询等全部事件")
    args = parser.parse_args()

    with open(args.logfile, encoding="utf-8") as f:
        events = [json.loads(line) for line in f if line.strip()]
    if not events:
        print("空日志")
        return
    head = events[0]
    print(f"run={head['run_id']} condition={head['condition']} seed={head['seed']} "
          f"events={len(events)}")
    print("-" * 80)
    for e in events:
        if not args.all and e["event_type"] not in DECISION_EVENTS:
            continue
        payload = json.dumps(e["payload"], ensure_ascii=False)
        if len(payload) > 140:
            payload = payload[:140] + "…"
        print(f"t={e['tick']:>4} #{e['seq']:<4} {e['actor']:<18} "
              f"{e['event_type']:<22} {payload}")


if __name__ == "__main__":
    main()
