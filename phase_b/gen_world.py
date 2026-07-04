#!/usr/bin/env python3
"""从 embodied_agent 的拓扑图(单一真值)生成 Nav2 世界:
  map.pgm + map.yaml   占据栅格:走廊(边)自由,其余货架/墙占据
  keepout.pgm + ...     keepout 掩码:受限区 r1 / 禁入区 f1(受阻边故障运行时改这张)
  waypoints.yaml        node_id -> {x, y, yaw}(map 米坐标)
用法(项目 venv):.venv\\Scripts\\python phase_b\\gen_world.py
只用 stdlib(手写 P5 PGM + 纯文本 yaml),不依赖 PIL/pyyaml。
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from embodied_agent.world import default_map  # noqa: E402

OUT = Path(__file__).resolve().parent / "world"
RES = 0.05                 # m/cell(Nav2 标准)
S = 0.06                   # m per layout-px(整体尺度)
MARGIN_M = 1.2             # 地图边缘留白
LANE_HW_M = 1.0            # 走廊半宽 → 2m 宽通道;减 inflation 0.70m 后仍留 0.30m 零代价中线
NODE_R_M = 1.0             # 节点自由圆盘半径
ZONE_R_M = 1.1             # 受限/禁入区掩码半径

FREE, OCC, UNKNOWN = 254, 0, 205


def build():
    topo = default_map()
    nodes = {n.id: (topo.nodes[n.id]) for n in topo.nodes.values()}
    # 布局 px(与 viewer/POV 一致)
    LAYOUT = {
        "dock": (80, 430), "c1": (210, 430), "c2": (340, 430), "a1": (480, 430),
        "a2": (580, 330), "a3": (660, 220), "a3_alt": (520, 200), "b1": (250, 290),
        "b2": (420, 240), "r1": (480, 510), "f1": (130, 300),
    }
    # 边拓扑从 default_map() 派生(单一真值),不再手抄——px 布局才是本文件的几何职责
    edges = sorted(topo.edges.keys())

    # 布局 px -> 世界米(y 翻转:图像 y 向下,map y 向上)
    xs = [p[0] for p in LAYOUT.values()]
    ys = [p[1] for p in LAYOUT.values()]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)

    def to_world(px, py):
        wx = (px - minx) * S + MARGIN_M
        wy = (maxy - py) * S + MARGIN_M   # 翻转 y
        return wx, wy

    world = {nid: to_world(*p) for nid, p in LAYOUT.items()}
    W_m = (maxx - minx) * S + 2 * MARGIN_M
    H_m = (maxy - miny) * S + 2 * MARGIN_M
    W = int(math.ceil(W_m / RES))
    H = int(math.ceil(H_m / RES))

    def w2cell(wx, wy):
        # 世界米 -> 图像像素,与 map_server worldToMap 逐字一致(origin=[0,0]):
        #   栅格列 = floor(wx/RES);栅格行 my = floor(wy/RES);图像 row0 在顶部 = (H-1)-my。
        # 用离散 H(而非连续 H_m)+ 截断(而非 round),消除系统性 -1 行偏移与 round/floor 分歧。
        col = int(wx / RES)
        row = (H - 1) - int(wy / RES)
        return col, row

    # ---- 占据栅格:全占据(货架),再刻出走廊与节点圆盘 ----
    grid = bytearray([OCC]) * (W * H)

    def setc(col, row, val):
        if 0 <= col < W and 0 <= row < H:
            grid[row * W + col] = val

    def disc(wx, wy, r_m, val):
        rc = int(math.ceil(r_m / RES))
        c0, r0 = w2cell(wx, wy)
        for dr in range(-rc, rc + 1):
            for dc in range(-rc, rc + 1):
                if dc * dc + dr * dr <= rc * rc:
                    setc(c0 + dc, r0 + dr, val)

    def lane(w1, w2, hw_m, val):
        c1, r1 = w2cell(*w1)
        c2, r2 = w2cell(*w2)
        steps = int(max(abs(c2 - c1), abs(r2 - r1))) + 1
        hw = int(math.ceil(hw_m / RES))
        for i in range(steps + 1):
            t = i / steps
            cc = c1 + (c2 - c1) * t
            rr = r1 + (r2 - r1) * t
            for dr in range(-hw, hw + 1):
                for dc in range(-hw, hw + 1):
                    if dc * dc + dr * dr <= hw * hw:
                        setc(int(round(cc)) + dc, int(round(rr)) + dr, val)

    for a, b in edges:
        lane(world[a], world[b], LANE_HW_M, FREE)
    for nid, (wx, wy) in world.items():
        disc(wx, wy, NODE_R_M, FREE)

    # 周界墙(最外一圈占据)
    for c in range(W):
        setc(c, 0, OCC); setc(c, H - 1, OCC)
    for r in range(H):
        setc(0, r, OCC); setc(W - 1, r, OCC)

    # ---- keepout 掩码:遵循 map_server 约定(白 255=可通行、黑 0=禁行 keepout)。
    # 默认全 255 可通行,禁入区涂黑 0。KeepoutFilter(scale 模式)把黑区映为致死代价,
    # 规划器绕行/拒穿。运行时故障注入就是把某条边的走廊涂黑(见 inject_keepout.py)。----
    mask = bytearray([255]) * (W * H)

    def mask_disc(wx, wy, r_m, val):
        rc = int(math.ceil(r_m / RES))
        c0, r0 = w2cell(wx, wy)
        for dr in range(-rc, rc + 1):
            for dc in range(-rc, rc + 1):
                if dc * dc + dr * dr <= rc * rc and 0 <= c0 + dc < W and 0 <= r0 + dr < H:
                    mask[(r0 + dr) * W + (c0 + dc)] = val

    # 初始 keepout:仅禁入区 f1 涂黑(受限 r1 需 HITL token 才通,基础掩码不封;受阻边运行时再加)
    mask_disc(*world["f1"], ZONE_R_M, 0)

    OUT.mkdir(parents=True, exist_ok=True)
    write_pgm(OUT / "map.pgm", grid, W, H)
    write_pgm(OUT / "keepout.pgm", mask, W, H, maxval=255)
    write_map_yaml(OUT / "map.yaml", "map.pgm", RES, MARGIN_M_origin(H_m))
    write_mask_yaml(OUT / "keepout.yaml", "keepout.pgm", RES, MARGIN_M_origin(H_m))
    write_waypoints(OUT / "waypoints.yaml", world, topo)
    write_topo(OUT / "topo.yaml", topo)
    write_preview(OUT / "map_preview.pgm", grid, mask, world, W, H, w2cell)
    print(f"map {W}x{H} cells ({W_m:.1f}x{H_m:.1f}m) @ {RES} -> {OUT}")
    print(f"nodes: " + ", ".join(f"{k}=({v[0]:.1f},{v[1]:.1f})" for k, v in world.items()))


def MARGIN_M_origin(H_m):
    # map.yaml origin = 图像左下角在 map 帧的世界坐标 = (0, 0)(我们把最小节点偏到 +MARGIN)
    return (0.0, 0.0)


def write_pgm(path: Path, data: bytearray, w: int, h: int, maxval=255):
    with open(path, "wb") as f:
        f.write(f"P5\n{w} {h}\n{maxval}\n".encode())
        f.write(bytes(data))


def write_map_yaml(path, image, res, origin):
    path.write_text(
        f"image: {image}\nmode: trinary\nresolution: {res}\n"
        f"origin: [{origin[0]}, {origin[1]}, 0.0]\nnegate: 0\n"
        f"occupied_thresh: 0.65\nfree_thresh: 0.25\n", encoding="utf-8")


def write_mask_yaml(path, image, res, origin):
    # keepout 掩码:scale 模式,像素值 0..100 直接映射代价
    path.write_text(
        f"image: {image}\nmode: scale\nresolution: {res}\n"
        f"origin: [{origin[0]}, {origin[1]}, 0.0]\nnegate: 0\n"
        f"occupied_thresh: 0.65\nfree_thresh: 0.25\n", encoding="utf-8")


def write_waypoints(path, world, topo):
    lines = ["# node_id -> map 米坐标(RclpyAdapter 查此表把 node 转 pose)", "waypoints:"]
    for nid, (wx, wy) in world.items():
        acc = topo.nodes[nid].access
        lines.append(f"  {nid}: {{x: {wx:.3f}, y: {wy:.3f}, yaw: 0.0, access: {acc}}}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_topo(path, topo):
    """拓扑(节点 access/neighbors + 边)——容器侧 RclpyAdapter 读它,无需导入包。
    与 waypoints.yaml 同源(gen_world 从 embodied_agent.world.default_map() 派生)。"""
    lines = ["# 拓扑真值(派生自 embodied_agent.world.default_map)", "nodes:"]
    for nid in sorted(topo.nodes):
        spec = topo.nodes[nid]
        nbrs = [nb for nb, _ in topo.neighbors(nid)]
        lines.append(f"  {nid}: {{name: {spec.name}, access: {spec.access}, "
                     f"neighbors: [{', '.join(nbrs)}]}}")
    lines.append("edges:")
    for a, b in sorted(topo.edges):
        lines.append(f"  - [{a}, {b}, {topo.edges[(a, b) if (a, b) in topo.edges else (b, a)]}]")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_preview(path, grid, mask, world, w, h, w2cell):
    # 灰度预览:占据栅格 + keepout(暗红近似为中灰)+ 节点标记
    prev = bytearray(grid)
    for i, m in enumerate(mask):
        if m < 128 and prev[i] == 254:   # keepout(黑)叠在自由格上,预览显示为中灰
            prev[i] = 120
    for nid, (wx, wy) in world.items():
        c0, r0 = w2cell(wx, wy)
        for dr in range(-2, 3):
            for dc in range(-2, 3):
                idx = (r0 + dr) * w + (c0 + dc)
                if 0 <= idx < len(prev):
                    prev[idx] = 30
    write_pgm(path, prev, w, h)


if __name__ == "__main__":
    build()
