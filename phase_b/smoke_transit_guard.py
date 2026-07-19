#!/usr/bin/env python3
"""F-01 运行期访问围栏 —— 真实 Nav2 上的 transit 强制端到端验证(需容器:rclpy + Nav2)。

场景根据:`r1` 是"受限区-捷径",几何上是 c2↔a3 之间的近路;它的"受限"只是拓扑/注册表层规则,
**不在 costmap keepout 里**。所以真实 Nav2 规划 dock→a3 时会走这条近路穿过 r1——目标(a3)是
自由的、被门禁放行,但轨迹越过了未授权的受限区。这正是 Phase B FINDINGS 第 2 条披露的 access-盲
重规划。本脚本验证新的运行期围栏会在踏入 r1 时安全停。

判定逻辑本身(TransitGuard)已在宿主 venv 纯逻辑单测 + mock 控制环端到端单测覆盖
(tests/test_transit_geofence.py);本脚本补的是【真实 Nav2 + 真实 TF 位置流】这一段,只在容器内可跑。

期望:
  [A] 围栏开 + 无 r1 授权,导航 a3 → 轨迹经 r1 近路 → 终态 aborted,reason 前缀 transit_violation。
  [B] 对照:围栏关(模拟旧行为)→ 同一导航穿过 r1 抵达 a3 succeeded(暴露被修复前的 gap)。
  [C] 带 r1 授权(token scope=r1)导航到 r1 → 进入 r1 被授权,围栏放行 → succeeded。
"""
import asyncio
import sys
import time

import rclpy
from rclpy_adapter import RclpyAdapter


async def drive(ad, gid, timeout_s=120):
    t0 = time.time()
    while True:
        # feedback 在飞时跑围栏判定;terminal 一出即可从 result 取
        await ad.feedback(gid)
        res = await ad.result(gid)
        if res is not None:
            return res
        if time.time() - t0 > timeout_s:
            await ad.cancel(gid)
            return {"status": "timeout", "reason": f">{timeout_s}s"}
        await asyncio.sleep(0.5)


async def run():
    fails = []
    rclpy.init()

    # [A] 围栏开:导航到自由目标 a3,但 Nav2 会走 r1 近路 → 应被围栏拦停
    ad = RclpyAdapter()
    ad.bootstrap("dock")
    print("[A] 围栏开 + 无 r1 授权,send_goal(a3)【Nav2 走 r1 近路】→ 期望 aborted/transit_violation")
    r = await ad.send_goal("a3", geofence_on=True, restricted_ok_nodes=frozenset())
    resA = await drive(ad, r["goal_id"])
    print(f"    a3 -> {resA}")
    if not (resA.get("status") == "aborted"
            and str(resA.get("reason", "")).startswith("transit_violation")):
        fails.append(f"[A] 围栏未拦停 r1 过境: {resA}")
    ad.shutdown()

    # [B] 对照:围栏关 → 暴露修复前的 access-盲穿越(穿过 r1 抵达 a3)
    ad = RclpyAdapter()
    ad.bootstrap("dock")
    print("[B] 围栏关(模拟旧行为),send_goal(a3) → 期望 succeeded(穿过 r1,暴露 gap)")
    r = await ad.send_goal("a3", geofence_on=False, restricted_ok_nodes=frozenset())
    resB = await drive(ad, r["goal_id"])
    print(f"    a3 -> {resB}")
    if resB.get("status") != "succeeded":
        fails.append(f"[B] 对照未 succeeded(无法证明 gap 存在): {resB}")
    ad.shutdown()

    # [C] 授权穿越:带 r1 授权导航到 r1 → 围栏放行
    ad = RclpyAdapter()
    ad.bootstrap("dock")
    print("[C] 围栏开 + 授权 r1,send_goal(r1) → 期望 succeeded(授权进入放行)")
    r = await ad.send_goal("r1", authorized=True, geofence_on=True,
                           restricted_ok_nodes=frozenset({"r1"}))
    resC = await drive(ad, r["goal_id"])
    print(f"    r1 -> {resC}")
    if resC.get("status") != "succeeded":
        fails.append(f"[C] 授权穿越被误拦: {resC}")
    ad.shutdown()

    try:
        rclpy.shutdown()
    except Exception:
        pass
    if fails:
        print("FAIL:\n  - " + "\n  - ".join(fails))
        sys.exit(3)
    print("PASS(F-01): 真实 Nav2 上,运行期围栏在未授权 r1 过境时安全停 [A];关闭时暴露旧 gap [B];"
          "授权穿越正常放行 [C]。")
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(run())
