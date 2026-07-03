"""评测 harness:N seed × 条件矩阵,顺序执行(确定性优先;虚拟时钟下全矩阵秒级)。

评测环路 0 次 LLM 调用(评审 B2)。每个 run 独立装配 Runtime(记忆不跨 run)。
产出:runs/<condition>/seed_<n>.jsonl 事件日志;指标由 metrics.py 只读日志计算。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from ..graph import run_graph
from ..planner_rules import malicious_script
from ..runtime import RunConfig, Runtime, build_runtime
from .scenarios import Condition, all_conditions, fault_specs_for

DEFAULT_SEEDS = list(range(10))


async def _run_malicious(rt: Runtime) -> None:
    """对抗/消融条件:恶意 planner stub 直接对注册表发调用(不经图——
    安全门就在注册表上,这正是要挑战的层)。"""
    for call in malicious_script():
        res = await rt.registry.call(call.tool, call.args,
                                     caller="malicious_planner")
        rt.event_log.emit(
            "malicious_planner", "attempt",
            tool=call.tool, args=call.args, note=call.note, ok=res.ok,
            code=None if res.ok else res.error["code"])
        # 门禁放行(消融)时,把导航驱动到终态,让地面真值监视器看到实际进入
        if res.ok and res.data and "goal_id" in res.data:
            gid = res.data["goal_id"]
            for _ in range(200):
                result = await rt.adapter.result(gid)
                if result is not None:
                    break
                await rt.adapter.wait(1)
    rt.event_log.emit("harness", "run_summary",
                      outcome_hint="adversarial_script_done",
                      violations=len(rt.safety.violations))


async def run_once(cond: Condition, seed: int, out_root: Path,
                   tick_duration_s: float = 0.0) -> Path:
    log_path = out_root / cond.name / f"seed_{seed}.jsonl"
    cfg = RunConfig(
        condition=cond.name, seed=seed,
        fault_specs=fault_specs_for(cond),
        gates_on=cond.gates_on, hitl_rules=cond.hitl_rules,
        tick_duration_s=tick_duration_s, log_path=log_path,
        initial_battery_pct=cond.initial_battery_pct,
    )
    rt = build_runtime(cfg)
    rt.event_log.emit("harness", "run_start", gates_on=cond.gates_on,
                      planner=cond.planner)
    try:
        if cond.planner == "malicious":
            await _run_malicious(rt)
        else:
            await run_graph(rt)
    except Exception as e:  # 崩溃留痕、不中断矩阵:无 run_summary 的 run 记为 unsafe-failure
        rt.event_log.emit("harness", "run_crashed", error=repr(e))
    finally:
        rt.event_log.close()
    return log_path


async def run_matrix(out_root: Path, seeds: list[int] | None = None,
                     conditions: list[str] | None = None) -> list[Path]:
    seeds = seeds if seeds is not None else DEFAULT_SEEDS
    conds = all_conditions()
    names = conditions or list(conds)
    paths = []
    for name in names:
        for seed in seeds:
            paths.append(await run_once(conds[name], seed, out_root))
    return paths


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="runs")
    parser.add_argument("--seeds", type=int, nargs="*", default=None)
    parser.add_argument("--conditions", nargs="*", default=None)
    args = parser.parse_args()
    paths = asyncio.run(run_matrix(Path(args.out), args.seeds, args.conditions))
    print(f"完成 {len(paths)} 个 run,日志在 {args.out}/")


if __name__ == "__main__":
    main()
