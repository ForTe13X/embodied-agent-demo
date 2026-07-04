#!/usr/bin/env python3
"""Day3-B(第一部分):用 RclpyAdapter 在【真实 Nav2 + keepout】上核对评审指出的未覆盖契约分支
  · aborted + reason=unreachable(隔离/禁区目标):send_goal 到 keepout 里的 f1
    → result.status=='aborted' 且 reason=='unreachable'(_map_result 把 FAILED 上抛为不可达)
  · 对照:send_goal 到自由的 c2 → succeeded
这补上 Day2-C 只测了 succeeded/canceled/unknown_node、未在真实 Nav2 触发 unreachable 的缺口。
"""
import asyncio
import sys

import rclpy
from rclpy_adapter import RclpyAdapter


async def drive(ad, gid, timeout_s=90):
    import time
    t0 = time.time()
    while True:
        res = await ad.result(gid)
        if res is not None:
            return res
        if time.time() - t0 > timeout_s:
            await ad.cancel(gid)
            return {"status": "timeout", "reason": f">{timeout_s}s"}
        await asyncio.sleep(1.0)


async def run():
    fails = []
    rclpy.init()
    ad = RclpyAdapter()
    ad.bootstrap("dock")

    print("[A] send_goal(f1)【keepout 禁区】→ 期望 aborted / unreachable")
    r = await ad.send_goal("f1")
    res_f1 = await drive(ad, r["goal_id"])
    print(f"    f1 -> {res_f1}")
    if not (res_f1.get("status") == "aborted" and res_f1.get("reason") == "unreachable"):
        fails.append(f"f1 未按契约 aborted/unreachable: {res_f1}")

    print("[B] send_goal(c2)【自由】→ 期望 succeeded")
    r2 = await ad.send_goal("c2")
    res_c2 = await drive(ad, r2["goal_id"])
    print(f"    c2 -> {res_c2}")
    if res_c2.get("status") != "succeeded":
        fails.append(f"c2 未 succeeded: {res_c2}")

    st = await ad.get_state()
    print(f"    末态 get_state: pose={st.get('pose')} nav_status={st.get('nav_status')}")

    ad.shutdown()
    try:
        rclpy.shutdown()
    except Exception:
        pass
    if fails:
        print("FAIL:\n  - " + "\n  - ".join(fails))
        sys.exit(3)
    print("PASS(Day3-B/1): 真实 Nav2 + keepout 上,unreachable 契约分支(aborted/unreachable)核对通过;"
          "对照 c2 succeeded —— Day2-C 的覆盖缺口已补。")
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(run())
