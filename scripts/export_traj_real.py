#!/usr/bin/env python3
"""把 Day-4【真实 Nav2】run 的事件日志(real_mission_events.jsonl)转成 povgen 的 traj.json。

与 export_traj.py 的区别:真实 run 没有 mock 的 node_entered/goal_finished 事件(SafetyMonitor/
MockNavServer 在 real_runtime 里被置空),只有 navigate 的 step_completed(到达目标)+ recovery 事件。
所以这里的轨迹锚点来自:起点 dock@0 + 每个 `observer step_completed {kind:navigate,target:X}`;
锚点之间的走廊路径用拓扑最短路(embodied_agent.world,与 Nav2 实际走的自由走廊一致)补全,
按像素距离分配 tick。故障/恢复(a3 不可达→替换 a3_alt)转成 HUD 字幕。

用法(项目 venv):.venv\\Scripts\\python scripts\\export_traj_real.py phase_b\\real_mission_events.jsonl povgen\\traj_day4.json
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from embodied_agent.world import default_map  # noqa: E402

LAYOUT = {
    "dock": (80, 430), "c1": (210, 430), "c2": (340, 430), "a1": (480, 430),
    "a2": (580, 330), "a3": (660, 220), "a3_alt": (520, 200), "b1": (250, 290),
    "b2": (420, 240), "r1": (480, 510), "f1": (130, 300),
}
ACCESS = {"dock": "dock", "r1": "restricted", "f1": "forbidden"}
NAMES = {"dock": "DOCK", "c1": "走廊C1", "c2": "走廊C2", "a1": "A区-1", "a2": "A区-2",
         "a3": "A区-3", "a3_alt": "A区-3替代", "b1": "B区-1", "b2": "B区-2",
         "r1": "受限捷径", "f1": "禁入配电室"}


def dist(p, q):
    return ((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2) ** 0.5


def main():
    src = Path(sys.argv[1]); dst = Path(sys.argv[2])
    topo = default_map()
    ev = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]

    # 锚点:起点 dock@0 + 每个到达事件(observer step_completed navigate)
    anchors = [(0.0, "dock")]
    for e in ev:
        if e["event_type"] == "step_completed" and e["actor"] == "observer":
            step = e["payload"].get("step", {})
            if step.get("kind") == "navigate":
                anchors.append((float(e["tick"]), step["target"]))
    # 去重相邻同点
    dedup = [anchors[0]]
    for t, n in anchors[1:]:
        if n != dedup[-1][1]:
            dedup.append((t, n))
    anchors = dedup

    # 锚点间用拓扑最短路补全走廊路径,按像素距离分配 tick
    waypoints = []
    for i in range(len(anchors) - 1):
        t0, na = anchors[i]; t1, nb = anchors[i + 1]
        route = topo.route(na, nb) or [na, nb]
        segs = [dist(LAYOUT[route[j]], LAYOUT[route[j + 1]]) for j in range(len(route) - 1)]
        total = sum(segs) or 1.0
        acc = 0.0
        waypoints.append({"t": round(t0, 2), "x": LAYOUT[na][0], "y": LAYOUT[na][1]})
        for j, seg in enumerate(segs):
            acc += seg
            tt = t0 + (t1 - t0) * (acc / total)
            nd = route[j + 1]
            waypoints.append({"t": round(tt, 2), "x": LAYOUT[nd][0], "y": LAYOUT[nd][1]})
    # 末锚点
    tlast, nlast = anchors[-1]
    waypoints.append({"t": round(tlast, 2), "x": LAYOUT[nlast][0], "y": LAYOUT[nlast][1]})

    # 故障/恢复字幕(真实事件驱动)
    # HUD 字幕用【英文/ASCII 且去术语】(povgen 字体无 CJK 字形;中文靠底部烧录字幕)——
    # 让不懂 ROS 的英文观众也能读懂:不裸露 nav_unreachable / substitute / a3_alt 等内部枚举。
    _FAULT_HUD = {"nav_unreachable": "destination unreachable (path blocked)",
                  "nav_blocked": "path blocked ahead", "low_battery": "battery low",
                  "sensor_fault": "sensor fault", "tool_failure": "tool failed"}
    captions = []
    for e in ev:
        et, p, t = e["event_type"], e["payload"], e["tick"]
        if et == "plan_built":
            captions.append({"tick": t, "text": "MISSION: patrol A-2 to A-3 (real navigation)"})
        elif et == "fault_classified":
            captions.append({"tick": t, "text": "FAULT DETECTED: "
                             + _FAULT_HUD.get(p.get("fclass"), p.get("fclass", "fault"))})
        elif et == "candidate_chosen":
            captions.append({"tick": t, "text": "AUTO-RECOVERY: reroute to backup observation point"})
        elif et == "recovery_applied":
            a = p.get("action", {})
            if a.get("type") == "substitute":
                captions.append({"tick": t, "text": f"NEW TARGET: {a['old']} -> {a['new']} (backup point)"})
        elif et == "run_summary":
            captions.append({"tick": t, "text": "MISSION COMPLETE - returned to dock (zero orchestration change)"})

    # a3 不可达故障标记(视觉提示:a2 处朝 a3 方向,障碍在 a3)
    blocked = [{"tick": 66.5, "edge": ["a2", "a3"], "pos": list(LAYOUT["a3"])}]

    out = {
        "run_id": ev[0]["run_id"],
        "max_tick": max(e["tick"] for e in ev),
        "layout": {k: list(v) for k, v in LAYOUT.items()},
        "access": ACCESS,
        "names": NAMES,
        "edges": [list(k) for k in sorted(topo.edges.keys())],
        "waypoints": waypoints,
        "blocked": blocked,
        "anomaly_node": "a2",
        "perceives": [],          # 真实 loopback 无相机,perceive 返回空(诚实:不造假 VLM 检测)
        "battery": [],            # loopback 无耗电模型
        "captions": captions,
        "violations": [],
        "substitution": {"old": "a3", "new": "a3_alt"},
    }
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"traj -> {dst}  锚点={[(round(t),n) for t,n in anchors]}  waypoints={len(waypoints)} "
          f"max_tick={out['max_tick']}")


if __name__ == "__main__":
    main()
