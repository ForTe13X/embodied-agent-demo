"""从事件日志重建逐 tick 位姿轨迹 → traj.json(POV 渲染的唯一数据源)。

轨迹 = 地面真值 node_entered 锚点之间的分段线性,受阻间奏(fault_activated +
canceled goals)插入脚本化子路点:推进到受阻边 65% 处 → 原地停滞 → 倒回起点 → 绕行。
这样 POV 里"撞上箱堆-僵持-倒车-绕行"与事件 tick 严格对齐。

用法:.venv\\Scripts\\python scripts\\export_traj.py runs\\nav_blocked\\seed_0.jsonl out\\traj.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 与 viewer/index.html 完全一致的布局(canvas px);3D 侧再除以 SCALE 变米
LAYOUT = {
    "dock": (80, 430), "c1": (210, 430), "c2": (340, 430), "a1": (480, 430),
    "a2": (580, 330), "a3": (660, 220), "a3_alt": (520, 200), "b1": (250, 290),
    "b2": (420, 240), "r1": (480, 510), "f1": (130, 300),
}
ACCESS = {"dock": "dock", "r1": "restricted", "f1": "forbidden"}
NAMES = {"dock": "DOCK", "c1": "C1", "c2": "C2", "a1": "A1", "a2": "A2",
         "a3": "A3", "a3_alt": "A3-ALT", "b1": "B1", "b2": "B2",
         "r1": "RESTRICTED", "f1": "FORBIDDEN"}
BLOCK_FRAC = 0.45      # 机器人停滞点在边上的位置
OBSTACLE_FRAC = 0.82   # 箱堆位置:停滞点前约 1.3m,正对凝视不穿模


def load(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def lerp(a, b, f):
    return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)


def main() -> None:
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    ev = load(src)

    anchors = [(0.0, LAYOUT["dock"])]
    for e in ev:
        if e["event_type"] == "node_entered":
            anchors.append((float(e["tick"]), LAYOUT[e["payload"]["node"]]))

    blocked = [(e["tick"], tuple(e["payload"]["edge"]))
               for e in ev if e["event_type"] == "fault_activated"
               and e["payload"].get("edge")]
    cancels = [e["tick"] for e in ev if e["event_type"] == "goal_finished"
               and e["payload"].get("status") == "canceled"]

    # 受阻间奏:两锚点之间发生了 fault_activated + ≥1 次 cancel → 插脚本化子路点
    waypoints: list[tuple[float, tuple]] = []
    for i, (t0, p0) in enumerate(anchors):
        waypoints.append((t0, p0))
        if i + 1 >= len(anchors):
            break
        t1 = anchors[i + 1][0]
        faults = [(ft, edge) for ft, edge in blocked if t0 <= ft < t1]
        cs = [c for c in cancels if t0 < c <= t1]
        if faults and cs:
            ft, edge = faults[0]
            a, b = LAYOUT[edge[0]], LAYOUT[edge[1]]
            # 起点取离 p0 近的一端,推进方向指向另一端
            if (a[0]-p0[0])**2 + (a[1]-p0[1])**2 > (b[0]-p0[0])**2 + (b[1]-p0[1])**2:
                a, b = b, a
            bp = lerp(a, b, BLOCK_FRAC)
            last_cancel = max(cs)
            waypoints.append((min(ft + 1.5, last_cancel - 1), bp))  # 撞上
            waypoints.append((last_cancel, bp))                     # 僵持
            waypoints.append((min(last_cancel + 1.5, t1 - 0.5), p0))  # 倒回

    # 事件通道
    perceives = []
    for e in ev:
        if (e["event_type"] == "tool_result" and e["payload"].get("ok")
                and isinstance(e["payload"].get("data"), dict)
                and "objects" in e["payload"]["data"]):
            for o in e["payload"]["data"]["objects"]:
                perceives.append({"tick": e["tick"],
                                  "node": e["payload"]["data"]["at_node"],
                                  "label": o["label"], "conf": o["confidence"]})
    battery = [{"tick": e["tick"], "pct": e["payload"]["battery_pct"]}
               for e in ev if e["payload"].get("battery_pct") is not None]

    captions = []
    CAP = {
        "goal_accepted": lambda p: f"NAV -> {p['target'].upper()}",
        "fault_activated": lambda p: "!! FAULT INJECTED: EDGE BLOCKED",
        "watchdog_triggered": lambda p: f"WATCHDOG: {p['kind'].upper()}",
        "fault_classified": lambda p: f"RECOVERY: {p['stage'].upper()}",
        "finding_reported": lambda p: f"REPORT: {p['label']} @ {p['node_id'].upper()}",
        "queue_snapshot": lambda p: "SNAPSHOT QUEUE -> DOCK & RECHARGE",
        "queue_resumed": lambda p: "RESUME ORIGINAL QUEUE",
    }
    for e in ev:
        fn = CAP.get(e["event_type"])
        if fn:
            try:
                captions.append({"tick": e["tick"], "text": fn(e["payload"])})
            except KeyError:
                pass

    out = {
        "run_id": ev[0]["run_id"],
        "max_tick": max(e["tick"] for e in ev),
        "layout": {k: list(v) for k, v in LAYOUT.items()},
        "access": ACCESS, "names": NAMES,
        "edges": [["dock","c1"],["c1","c2"],["c2","a1"],["a1","a2"],["a2","a3"],
                  ["c1","b1"],["b1","b2"],["b2","a2"],["c2","r1"],["r1","a3"],
                  ["a2","a3_alt"],["a3_alt","a3"],["c1","f1"]],
        "waypoints": [{"t": t, "x": p[0], "y": p[1]} for t, p in waypoints],
        "blocked": [{"tick": ft, "edge": list(edge),
                     "pos": list(lerp(LAYOUT[edge[0]], LAYOUT[edge[1]], OBSTACLE_FRAC))}
                    for ft, edge in blocked],
        "anomaly_node": "a2",
        "perceives": perceives, "battery": battery, "captions": captions,
    }
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"traj: {len(waypoints)} waypoints, {len(perceives)} perceives, "
          f"{len(captions)} captions, max_tick={out['max_tick']} -> {dst}")


if __name__ == "__main__":
    main()
