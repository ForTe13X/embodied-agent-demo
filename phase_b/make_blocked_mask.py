#!/usr/bin/env python3
"""生成"受阻边"keepout 掩码:在基础 keepout.pgm 上把某条边的走廊段涂黑(禁行),
供 Day3-B 运行时故障注入(filter_mask_server 的 load_map 服务热替换)。
用法(宿主 venv):.venv\\Scripts\\python phase_b\\make_blocked_mask.py c2 a1
产出:world/keepout_<a>_<b>.pgm + .yaml。只用 stdlib。
"""
import sys
from pathlib import Path

RES = 0.05
HW_M = 0.9          # 涂黑走廊半宽(略窄于地图走廊 1.0m,确保封死中线但不外溢太多)
OUT = Path(__file__).resolve().parent / "world"
_WP_KEYS = ("x", "y")


def load_wp(path):
    wp = {}
    for line in open(path, encoding="utf-8"):
        s = line.strip()
        if ":" not in s or "{" not in s:
            continue
        nid = s.split(":", 1)[0].strip()
        try:
            body = s[s.index("{") + 1:s.rindex("}")]
            kv = {}
            for part in body.split(","):
                if ":" in part:
                    k, v = part.split(":", 1)
                    kv[k.strip()] = v.strip()
            wp[nid] = (float(kv["x"]), float(kv["y"]))
        except Exception:
            continue
    return wp


def read_pgm(path):
    data = open(path, "rb").read()
    # P5\n{w} {h}\n{maxval}\n<bytes>
    assert data[:2] == b"P5", "not P5 PGM"
    idx = 2
    fields = []
    while len(fields) < 3:
        # skip whitespace
        while data[idx:idx + 1].isspace():
            idx += 1
        start = idx
        while not data[idx:idx + 1].isspace():
            idx += 1
        fields.append(int(data[start:idx]))
    idx += 1  # single whitespace after maxval
    w, h, mv = fields
    return w, h, mv, bytearray(data[idx:idx + w * h])


def write_pgm(path, data, w, h, mv=255):
    with open(path, "wb") as f:
        f.write(f"P5\n{w} {h}\n{mv}\n".encode())
        f.write(bytes(data))


ISO_R_M = 1.6       # 隔离节点的涂黑半径(> 规划器 tolerance 0.5m,确保目标不可达)


def main():
    args = [x for x in sys.argv[1:] if not x.startswith("-")]
    wp = load_wp(OUT / "waypoints.yaml")
    w, h, mv, mask = read_pgm(OUT / "keepout.pgm")

    def w2cell(wx, wy):
        return int(wx / RES), (h - 1) - int(wy / RES)

    # 单节点 → 隔离(涂一个圆盘,使该节点不可达);两节点 → 封边(涂中段走廊)
    if len(args) == 1:
        n = args[0]
        nx, ny = wp[n]
        c0, r0 = w2cell(nx, ny)
        rr = int(round(ISO_R_M / RES))
        for dr in range(-rr, rr + 1):
            for dc in range(-rr, rr + 1):
                if dc * dc + dr * dr <= rr * rr:
                    col, row = c0 + dc, r0 + dr
                    if 0 <= col < w and 0 <= row < h:
                        mask[row * w + col] = 0
        stem = f"keepout_isolate_{n}"
        write_pgm(OUT / f"{stem}.pgm", mask, w, h, mv)
        (OUT / f"{stem}.yaml").write_text(
            f"image: {stem}.pgm\nmode: scale\nresolution: {RES}\n"
            f"origin: [0.0, 0.0, 0.0]\nnegate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.25\n",
            encoding="utf-8")
        print(f"isolate node {n} -> {OUT / (stem + '.yaml')} ({w}x{h})")
        return

    a = args[0] if len(args) > 0 else "c2"
    b = args[1] if len(args) > 1 else "a1"
    ax, ay = wp[a]; bx, by = wp[b]
    c1, r1 = w2cell(ax, ay); c2, r2 = w2cell(bx, by)
    steps = int(max(abs(c2 - c1), abs(r2 - r1))) + 1
    hw = int(round(HW_M / RES))
    # 只封中段(0.25~0.75),避免连端点节点圆盘一起封,让"这条边不通、但两端节点还在"
    for i in range(steps + 1):
        t = i / steps
        if t < 0.25 or t > 0.75:
            continue
        cc = round(c1 + (c2 - c1) * t); rr = round(r1 + (r2 - r1) * t)
        for dr in range(-hw, hw + 1):
            for dc in range(-hw, hw + 1):
                if dc * dc + dr * dr <= hw * hw:
                    col, row = cc + dc, rr + dr
                    if 0 <= col < w and 0 <= row < h:
                        mask[row * w + col] = 0   # 涂黑=禁行

    stem = f"keepout_{a}_{b}"
    write_pgm(OUT / f"{stem}.pgm", mask, w, h, mv)
    (OUT / f"{stem}.yaml").write_text(
        f"image: {stem}.pgm\nmode: scale\nresolution: {RES}\n"
        f"origin: [0.0, 0.0, 0.0]\nnegate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.25\n",
        encoding="utf-8")
    print(f"blocked mask for edge {a}-{b} -> {OUT / (stem + '.yaml')} ({w}x{h})")


if __name__ == "__main__":
    main()
