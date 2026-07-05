#!/usr/bin/env python3
"""Day-4 编排整合演示:【同一套 LangGraph 编排图】驱动【真实 Nav2】跑一次含故障恢复的任务。

场景:巡检 a2 → a3;运行前用 keepout 掩码把 a3 隔离(注入 NAV_UNREACHABLE 故障)。
预期(与 mock 同构):
  navigate(a2) 成功 → perceive → navigate(a3) 被真实 Nav2 判 ABORTED/unreachable
  → observer 水位判 NAV_UNREACHABLE → exception_manager 查表 substitute_target
  → 确定性枚举 a3 的合法替代点(闭集)→ RuleSelector 选 a3_alt → replanner 改写队列
  → navigate(a3_alt) 成功 → perceive → 归坞。全程编排代码【一行未改】,只换了 adapter。

判据:substitutions 含 a3→a3_alt ∧ visited 含 a3_alt ∧ outcome_hint 为空(干净完成)∧ 末位姿=dock。
"""
import asyncio
import sys
import time
from pathlib import Path

import rclpy
import rclpy.time
from nav2_msgs.srv import LoadMap

from embodied_agent.graph import run_graph
from embodied_agent.intent import Intent
from real_runtime import build_real_runtime
from rclpy_adapter import RclpyAdapter

ISOLATE_MASK = "/hostpb/world/keepout_isolate_a3.yaml"
LOG_PATH = Path("/hostpb/real_mission_events.jsonl")


def inject_isolate(adapter, mask_url):
    """用 filter_mask_server 的 load_map 热替换成隔离 a3 的 keepout 掩码。"""
    cli = adapter.nav.create_client(LoadMap, "/filter_mask_server/load_map")
    if not cli.wait_for_service(timeout_sec=8.0):
        print("WARN: load_map 服务不可用,未注入隔离掩码"); return False
    req = LoadMap.Request(); req.map_url = mask_url
    fut = cli.call_async(req)
    rclpy.spin_until_future_complete(adapter.nav, fut, timeout_sec=8.0)
    ok = fut.result() is not None and fut.result().result == LoadMap.Response.RESULT_SUCCESS
    print(f"注入隔离掩码 a3: {'成功' if ok else '返回码非成功(但已尝试)'}")
    time.sleep(2)  # 让 keepout 滤镜把新掩码灌进 global_costmap
    return ok


async def run():
    rclpy.init()
    adapter = RclpyAdapter()
    adapter.bootstrap("dock")
    print("[bootstrap] Nav2 active,机器人在 dock")

    inject_isolate(adapter, ISOLATE_MASK)

    intent = Intent(
        mission="巡检 a2、a3 两点并在每点感知异常;a3 若不可达则改到合法替代观测点。",
        patrol_nodes=["a2", "a3"], perceive_at_each=True)
    rt = build_real_runtime(adapter, intent=intent, log_path=LOG_PATH)

    print("[graph] 启动 LangGraph 编排(同一套图,adapter=RclpyAdapter)...")
    final = await run_graph(rt)

    subs = final.get("substitutions", [])
    visited = final.get("visited", [])
    outcome = final.get("outcome_hint")
    st = await adapter.get_state()
    print("\n===== 编排结果 =====")
    print(f"  substitutions = {subs}")
    print(f"  visited       = {visited}")
    print(f"  outcome_hint  = {outcome}")
    print(f"  末位姿 pose    = {st.get('pose')} (docked={st.get('docked')})")
    print(f"  事件日志       = {LOG_PATH}")

    adapter.shutdown()
    try:
        rclpy.shutdown()
    except Exception:
        pass

    substituted = any(s.get("old") == "a3" and s.get("new") == "a3_alt" for s in subs)
    ok = (substituted and "a3_alt" in visited and outcome is None
          and st.get("pose") == "dock")
    if ok:
        print("\nPASS(Day4): 同一 LangGraph 编排 + Tool Registry 在【真实 Nav2】上完成"
              "「故障→确定性恢复→替代点→归坞」闭环;a3 不可达被 a3_alt 替代,编排代码零改动。")
        return 0
    reasons = []
    if not substituted:
        reasons.append(f"未发生 a3→a3_alt 替代(subs={subs})")
    if "a3_alt" not in visited:
        reasons.append(f"未巡检到 a3_alt(visited={visited})")
    if outcome is not None:
        reasons.append(f"非干净完成(outcome_hint={outcome})")
    if st.get("pose") != "dock":
        reasons.append(f"末位姿非 dock(={st.get('pose')})")
    print("\nFAIL(Day4): " + "; ".join(reasons))
    return 3


if __name__ == "__main__":
    try:
        code = asyncio.run(run())
    except Exception as e:
        import traceback
        traceback.print_exc()
        code = 4
    sys.exit(code)
