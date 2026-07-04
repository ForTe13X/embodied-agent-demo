#!/usr/bin/env python3
"""Day2-C 冒烟:用 RclpyAdapter 对着真实 Nav2 跑一遍 RobotAdapter 契约,逐项核对。
判定 PASS 需全部满足:
  ① assert_no_velocity_interface() 通过(本节点无 cmd_vel/velocity/torque 发布器)
  ② send_goal 立即返回 goal_id(非阻塞)
  ③ feedback 在飞给出 current_node 沿拓扑推进(dock→c1→c2)、edges_done 递增
  ④ result 终态 succeeded,末位姿 = 目标节点
  ⑤ 未知节点 send_goal 返回 aborted/unknown_node(不崩)
  ⑥ get_map(含 name)/ get_state(battery_pct 为 float)形状与 mock 一致
  ⑦ cancel 成功路径:在飞取消返回 True、result=canceled、cancel 后 feedback 不误报 aborted
未覆盖(诚实清单,留 Day-3):aborted+unreachable(隔离节点/受阻边)与 blocked 停滞水位需接入
keepout 掩码后才能在真实 Nav2 上触发,本冒烟未验证。
"""
import asyncio
import os
import sys

import rclpy
from rclpy_adapter import RclpyAdapter, VelocityInterfaceLeak


async def poll_to_done(ad, gid, timeout_s=200):
    import time
    t0 = time.time()
    last = None
    while True:
        fb = await ad.feedback(gid)
        if fb and fb != last:
            print(f"    fb: node={fb['current_node']} edge={fb['current_edge']} "
                  f"{fb['edges_done']}/{fb['edges_total']} v={fb['velocity']} "
                  f"stall={fb['stall_ticks']} dist_rem={fb['distance_remaining']}")
            last = fb
        res = await ad.result(gid)
        if res is not None:
            return res
        if time.time() - t0 > timeout_s:
            await ad.cancel(gid)
            return {"status": "timeout", "reason": f">{timeout_s}s", "ticks": -1}
        await asyncio.sleep(1.0)


async def run():
    start = os.environ.get("START", "dock")
    goal = os.environ.get("GOAL", "c2")
    fails = []

    rclpy.init()
    ad = RclpyAdapter()

    print("① 结构性断言:无速度接口")
    try:
        topics = ad.assert_no_velocity_interface()
        print(f"   OK,本节点 publishers({len(topics)}): {sorted(topics)}")
    except VelocityInterfaceLeak as e:
        fails.append(f"速度接口泄漏: {e}")
        print(f"   FAIL {e}")

    print(f"② bootstrap({start}) + waitUntilNav2Active")
    ad.bootstrap(start)

    print("⑥ get_map / get_state 形状")
    m = await ad.get_map()
    st = await ad.get_state()
    print(f"   get_map: {len(m['nodes'])} 节点, 首个={m['nodes'][0]}")
    print(f"   get_state: {st}")
    n0 = m["nodes"][0] if m.get("nodes") else {}
    if not (isinstance(m, dict) and "nodes" in m and st.get("pose") == start):
        fails.append(f"get_map/get_state 形状不符: pose={st.get('pose')}")
    if not all(k in n0 for k in ("id", "name", "access", "neighbors")):
        fails.append(f"get_map 节点缺键(应含 name,与 world.to_dict 一致): {n0}")
    if not isinstance(st.get("battery_pct"), (int, float)):  # 契约类型:float,非 None
        fails.append(f"get_state battery_pct 非数值(会令上层电量比较崩): {st.get('battery_pct')}")

    print("⑤ 未知节点 send_goal → aborted/unknown_node")
    r_unknown = await ad.send_goal("NOPE")
    gid_u = r_unknown["goal_id"]
    res_u = await ad.result(gid_u)
    print(f"   {r_unknown} -> {res_u}")
    if not (res_u and res_u["status"] == "aborted" and res_u["reason"] == "unknown_node"):
        fails.append(f"未知节点未按契约: {res_u}")

    print(f"②③④ send_goal({goal}) 非阻塞 → 轮询 feedback → result")
    r = await ad.send_goal(goal)
    print(f"   send_goal 立即返回: {r}")
    if "goal_id" not in r:
        fails.append(f"send_goal 未返回 goal_id: {r}")
        _finish(ad, fails); return
    res = await poll_to_done(ad, r["goal_id"])
    print(f"   result: {res}")
    if res.get("status") != "succeeded":
        fails.append(f"导航未 succeeded: {res}")

    st2 = await ad.get_state()
    print(f"   末态 get_state: {st2}")
    if st2.get("pose") != goal:
        fails.append(f"末位姿≠目标: pose={st2.get('pose')} 期望 {goal}")

    print("⑦ cancel 成功路径:send_goal(远目标)→ 动起来后 cancel → 断言 canceled 自洽")
    cancel_goal = os.environ.get("CANCEL_GOAL", "a3")
    rc = await ad.send_goal(cancel_goal)
    gidc = rc.get("goal_id")
    moved = False
    for _ in range(6):                     # 等它真的在飞(有位移)再取消
        await asyncio.sleep(1.0)
        fb = await ad.feedback(gidc)
        if fb and fb["status"] == "executing" and (fb.get("velocity") or 0) > 0.05:
            moved = True
            break
    ok_cancel = await ad.cancel(gidc)
    res_c = await ad.result(gidc)
    fb_after = await ad.feedback(gidc)     # 回归:cancel 后 feedback 不得误报 aborted
    print(f"   moved={moved} cancel={ok_cancel} result={res_c} feedback_after={fb_after['status']}")
    if not (ok_cancel is True and res_c and res_c["status"] == "canceled"):
        fails.append(f"cancel 成功路径未按契约(应 True + canceled): cancel={ok_cancel} result={res_c}")
    if fb_after and fb_after["status"] != "canceled":
        fails.append(f"cancel 后 feedback 误报 {fb_after['status']}(应 canceled;line198 回归)")

    _finish(ad, fails)


def _finish(ad, fails):
    ad.shutdown()
    try:
        rclpy.shutdown()
    except Exception:
        pass
    if fails:
        print("FAIL:\n  - " + "\n  - ".join(fails))
        sys.exit(3)
    print("PASS(Day2-C): RclpyAdapter 与 RobotAdapter 契约逐项对齐,真实 Nav2 上闭环 —— "
          "同一接口 mock⇄rclpy 可换成立。")
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(run())
