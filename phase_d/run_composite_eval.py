#!/usr/bin/env python3
"""Phase D-2 复合任务预注册评测:跑 3 条件,写审计日志,核对预注册预期,打印结果表。

预注册预期(改这里 = 改预注册,应在跑之前 commit):
  baseline    → completed_full   · skill succeeded
  unsafe      → degraded_complete · skill aborted_unsafe(1 次尝试,安全不重试)
  unreachable → degraded_complete · skill escalated(重试 2 次后上浮,共 3)
所有条件都必须【安全归坞】。用法:.venv\\Scripts\\python phase_d\\run_composite_eval.py
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from composite_mission import run_composite  # noqa: E402

OUT = Path(__file__).resolve().parent / "runs_composite"

PREREG = {
    "baseline":    {"outcome": "completed_full",   "skill": "succeeded"},
    "unsafe":      {"outcome": "degraded_complete", "skill": "aborted_unsafe"},
    "unreachable": {"outcome": "degraded_complete", "skill": "escalated"},
}


async def main():
    OUT.mkdir(exist_ok=True)
    rows, all_ok = [], True
    for scen, pred in PREREG.items():
        res = await run_composite(f"composite_{scen}", scen, log_path=OUT / f"{scen}.jsonl")
        sk = res["detail"].get("skill", {})
        hit = (res["outcome"] == pred["outcome"] and sk.get("outcome") == pred["skill"]
               and res["detail"].get("at_dock") is True)
        all_ok = all_ok and hit
        rows.append((scen, res["outcome"], sk.get("outcome"), sk.get("attempts", "-"),
                     res["detail"].get("at_dock"), hit))

    print(f"\n{'条件':<14}{'终态':<20}{'skill 归属':<16}{'尝试':<6}{'归坞':<6}{'命中预注册'}")
    print("-" * 74)
    for scen, outcome, sko, att, dock, hit in rows:
        print(f"{scen:<14}{outcome:<20}{str(sko):<16}{str(att):<6}"
              f"{('是' if dock else '否'):<6}{'✓' if hit else '✗'}")
    print("-" * 74)
    print(f"\n预注册命中:{'全部命中 ✓' if all_ok else '有未命中 ✗'}  · 审计日志:{OUT}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(asyncio.run(main()))
