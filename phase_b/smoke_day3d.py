#!/usr/bin/env python3
"""Day3-D:在真实栈上【复现 mock 的 blocked/停滞语义】+ base 帧连通断言(评审 PLAUSIBLE 两项做实)。

与 Day3-B/2(默认裸 BT → 自动改道)对照:这里用【不重规划】的 no-replan BT + 局部代价地图也装
keepout。导航 dock→a1,计划算好后(no-replan 不再改)中途封死 c2-a1;机器人跟到该段无法前进、
velocity→0、distance_remaining 停滞,stall_ticks 越过 observer 水位(STAGNATION_THRESHOLD_TICKS=6)。
→ 这就是 mock 里 blocked 的表现,证明"blocked→停滞→上层水位判定"在真实 Nav2(无自主重规划的
底盘)上 1:1 成立。

另:bootstrap 后断言 base_link↔base_footprint static TF 存在(评审:该连通靠 tb3 URDF 隐式提供,
换机器人描述会断链;这里显式断言,缺失即报错而非神秘失败)。
"""
import sys
import time

import rclpy
import rclpy.time
from nav2_msgs.srv import LoadMap

from rclpy_adapter import RclpyAdapter

BLOCK_MASK = "/hostpb/world/keepout_c2_a1.yaml"
STAGNATION_THRESHOLD_TICKS = 6     # 与 embodied_agent.runtime 同值
INJECT_AFTER_S = 12


def assert_base_frames(adapter, tries=40):
    """断言 base_link 与 base_footprint 在 TF 树里连通(靠 tb3 URDF 的 static TF)。"""
    for _ in range(tries):
        rclpy.spin_once(adapter.nav, timeout_sec=0.1)
        for a, b in (("base_link", "base_footprint"), ("base_footprint", "base_link")):
            try:
                adapter.tf.lookup_transform(a, b, rclpy.time.Time())
                return True
            except Exception:
                continue
    return False


def inject_block(adapter, mask_url):
    cli = adapter.nav.create_client(LoadMap, "/filter_mask_server/load_map")
    if not cli.wait_for_service(timeout_sec=6.0):
        return False
    req = LoadMap.Request(); req.map_url = mask_url
    fut = cli.call_async(req)
    rclpy.spin_until_future_complete(adapter.nav, fut, timeout_sec=6.0)
    return fut.result() is not None


async def run():
    import asyncio  # noqa: F401
    rclpy.init()
    adapter = RclpyAdapter()
    adapter.bootstrap("dock")
    print("[bootstrap] Nav2 active(no-replan BT + 局部 keepout)")

    frames_ok = assert_base_frames(adapter)
    print(f"[frame 断言] base_link↔base_footprint 连通 = {frames_ok}")

    r = await adapter.send_goal("a1")
    gid = r["goal_id"]
    print("[nav] dock -> a1(no-replan)")

    injected = False
    max_stall = 0
    t0 = time.time()
    stalled_dist = None
    while True:
        fb = await adapter.feedback(gid)
        if fb is None:
            break
        status = fb["status"]
        if status != "executing":
            print(f"[terminal] status={status} reason={fb.get('reason')}")
            break
        if not injected and time.time() - t0 >= INJECT_AFTER_S:
            ok = inject_block(adapter, BLOCK_MASK)
            injected = True
            print(f"    >>> 注入受阻边 c2-a1(load_map={'ok' if ok else 'fail'}) "
                  f"当前 dist={fb['distance_remaining']}")
        max_stall = max(max_stall, fb["stall_ticks"])
        if injected and fb["stall_ticks"] >= 2 and stalled_dist is None:
            stalled_dist = fb["distance_remaining"]
        if int(time.time() - t0) % 4 == 0:
            print(f"    fb: node={fb['current_node']} dist={fb['distance_remaining']} "
                  f"v={fb['velocity']} stall={fb['stall_ticks']}")
        if max_stall >= STAGNATION_THRESHOLD_TICKS:
            print(f"    >>> stall_ticks 越过水位 {STAGNATION_THRESHOLD_TICKS} "
                  f"(dist 停在 {fb['distance_remaining']}m,v={fb['velocity']})")
            break
        if time.time() - t0 > 120:
            print("    观察超时(120s)")
            break
        await asyncio.sleep(1.0)

    await adapter.cancel(gid)
    adapter.shutdown()
    try:
        rclpy.shutdown()
    except Exception:
        pass

    ok = frames_ok and injected and max_stall >= STAGNATION_THRESHOLD_TICKS
    if ok:
        print(f"\nPASS(Day3-D): base 帧连通断言通过;no-replan 底盘下受阻边致机器人停滞,"
              f"stall_ticks 达 {max_stall}≥{STAGNATION_THRESHOLD_TICKS} 越过 observer 水位 —— "
              f"mock 的 blocked/停滞语义在真实 Nav2 上复现(对照 Day3-B/2 默认裸 BT 的自动改道)。")
        return 0
    reasons = []
    if not frames_ok:
        reasons.append("base_link↔base_footprint 未连通")
    if not injected:
        reasons.append("未注入受阻边")
    if max_stall < STAGNATION_THRESHOLD_TICKS:
        reasons.append(f"stall_ticks 峰值 {max_stall}<{STAGNATION_THRESHOLD_TICKS}"
                       f"(机器人没停下?可能仍改道或注入时机不对)")
    print("\nFAIL(Day3-D): " + "; ".join(reasons))
    return 3


if __name__ == "__main__":
    import asyncio
    try:
        code = asyncio.run(run())
    except Exception:
        import traceback
        traceback.print_exc()
        code = 4
    sys.exit(code)
