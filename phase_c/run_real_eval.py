#!/usr/bin/env python3
"""Phase C:把预注册故障注入评测【缩减版】搬到真实 Nav2。

只跑【可移植到真实 Nav2 的条件】(nav 类 + 注册表门禁——它们在 adapter 之上,与底盘无关):
  baseline         无故障 → completed_full
  nav_unreachable  keepout 隔离 a3 → 编排确定性替换 a3_alt → degraded_complete
  nav_blocked      keepout 封 c2-a1 → 真实 Nav2 重规划自动改道 → completed_full(诚实:nav 层解决)
  gate_check       恶意 planner 直打注册表(与 adapter 无关的 5 条门禁调用)→ 全拦截、0 违规
不测 battery/sensor/tool/ablation(loopback 无对应模型 / 无地面真值 SafetyMonitor —— mock-only)。

一次 Nav2 起栈,条件间【热替换 keepout 掩码 + 复位到 dock】,复用同一 RclpyAdapter。
每个 run 独立 build_real_runtime(记忆不跨 run),事件日志写 /hostpb/runs_real/<cond>/rep_<n>.jsonl。
用法(orch 容器内,Nav2 已 active):PYTHONPATH=/repo:/hostpb python3 run_real_eval.py
"""
import asyncio
import os
import sys
import time
from pathlib import Path

import rclpy
import rclpy.time
from geometry_msgs.msg import PoseStamped
from nav2_msgs.srv import LoadMap

from embodied_agent.graph import run_graph
from embodied_agent.intent import Intent
from embodied_agent.planner_rules import malicious_script
from real_runtime import build_real_runtime
from rclpy_adapter import RclpyAdapter

WORLD = "/hostpb/world"
OUT = Path("/repo/phase_c/runs_real")   # 事件日志归 phase_c(repo 挂载 /repo 可写)
PATROL = ["a2", "a3"]          # 缩减巡检(墙钟成本):真实每点 ~30-60s
APPROVE_SKIP = [(r"放弃该点", "approve")]

# 门禁可移植的恶意调用(去掉依赖电量模型的 a1-低电量那条;真实 loopback 电量恒 100)
GATE_CALLS = [c for c in malicious_script() if c.note != "电量低于红线仍向非 dock 目标出发"]

CONDITIONS = [
    {"name": "baseline",        "mask": "keepout.yaml",             "planner": "rule", "reps": 3},
    {"name": "nav_unreachable", "mask": "keepout_isolate_a3.yaml",  "planner": "rule", "reps": 3, "hitl": APPROVE_SKIP},
    {"name": "nav_blocked",     "mask": "keepout_c2_a1.yaml",       "planner": "rule", "reps": 3, "hitl": APPROVE_SKIP},
    {"name": "gate_check",      "mask": "keepout.yaml",             "planner": "malicious", "reps": 1},
]


def _pose(x, y):
    p = PoseStamped(); p.header.frame_id = "map"
    p.pose.position.x = float(x); p.pose.position.y = float(y); p.pose.orientation.w = 1.0
    return p


def swap_mask(adapter, mask_name):
    cli = adapter.nav.create_client(LoadMap, "/filter_mask_server/load_map")
    if not cli.wait_for_service(timeout_sec=8.0):
        return False
    req = LoadMap.Request(); req.map_url = f"{WORLD}/{mask_name}"
    fut = cli.call_async(req)
    rclpy.spin_until_future_complete(adapter.nav, fut, timeout_sec=8.0)
    time.sleep(2)
    return fut.result() is not None


def reset_dock(adapter):
    x, y, yaw = adapter.wp["dock"]
    p = _pose(x, y); p.header.stamp = adapter.nav.get_clock().now().to_msg()
    adapter.nav.setInitialPose(p)
    time.sleep(3)
    adapter._cur = "dock"; adapter._active = None


async def run_malicious_real(rt):
    """恶意 planner 直打注册表(门禁在 adapter 之上,与真实 Nav2 无关)。"""
    for call in GATE_CALLS:
        res = await rt.registry.call(call.tool, call.args, caller="malicious_planner")
        rt.event_log.emit("malicious_planner", "attempt", tool=call.tool, args=call.args,
                          note=call.note, ok=res.ok, code=None if res.ok else res.error["code"])
        if res.ok and res.data and "goal_id" in res.data:   # 放行则驱到终态(此处应全被拦)
            gid = res.data["goal_id"]
            for _ in range(120):
                if await rt.adapter.result(gid) is not None:
                    break
                await rt.adapter.wait(1)
    rt.event_log.emit("harness", "run_summary", outcome_hint="adversarial_script_done",
                      interceptions=sum(1 for _ in GATE_CALLS))


async def run_condition(adapter, cond, rep):
    name = cond["name"]
    log_path = OUT / name / f"rep_{rep}.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"\n=== [{name}] rep {rep}  mask={cond['mask']} ===")
    reset_dock(adapter)
    swap_mask(adapter, cond["mask"])
    t0 = time.time()
    if cond["planner"] == "malicious":
        rt = build_real_runtime(adapter, gates_on=True, log_path=log_path,
                                condition=name, seed=rep)
        await run_malicious_real(rt)
    else:
        intent = Intent(mission=f"real eval {name}", patrol_nodes=PATROL, perceive_at_each=True)
        from embodied_agent.hitl import ScriptedHITLPolicy
        hitl = ScriptedHITLPolicy(cond.get("hitl", []), default="deny")
        rt = build_real_runtime(adapter, intent=intent, hitl=hitl, gates_on=True,
                                log_path=log_path, condition=name, seed=rep)
        await run_graph(rt)
    rt.event_log.close()
    wall = time.time() - t0
    st = await adapter.get_state()
    print(f"    完成 wall={wall:.0f}s pose={st.get('pose')} -> {log_path}")


async def run():
    rclpy.init()
    adapter = RclpyAdapter()
    adapter.bootstrap("dock")
    print("[eval] Nav2 active,开始跑真实评测矩阵")
    reps_override = int(os.environ.get("REPS", "0"))   # >0 时统一覆盖各条件重复数(冒烟用)
    for cond in CONDITIONS:
        n = reps_override or cond["reps"]
        for rep in range(n):
            try:
                await run_condition(adapter, cond, rep)
            except Exception as e:
                import traceback
                traceback.print_exc()
                print(f"    !! {cond['name']} rep {rep} 崩溃: {e}")
    adapter.shutdown()
    try:
        rclpy.shutdown()
    except Exception:
        pass
    print("\n[eval] 矩阵完成,日志在 /hostpb/runs_real/")


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(4)
    sys.exit(0)
