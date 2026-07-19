"""replay viewer 的只读后端(stdlib,无依赖)。

GET /                     → viewer 静态页
GET /api/runs             → [{condition, seed}] 扫描 runs/ 目录
GET /api/log?condition=&seed=  → 该 run 的事件数组(JSON)

只读、路径白名单(condition/seed 均校验,不接受任意路径)——viewer 是审计工具,
不是控制面;所有写操作仍只能走 Tool Registry。
用法:python viewer/serve.py [--port 8777] [--runs runs]
"""
from __future__ import annotations

import argparse
import json
import re
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
RUNS_DIR = ROOT.parent / "runs"

_COND_RE = re.compile(r"^[a-z0-9_]+$")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        # /pov/*.mp4 需要 HTTP Range(206):Chromium 的 <video> 寻址依赖字节范围,
        # SimpleHTTPRequestHandler 不支持会导致 seek 永远落回 0(复现于三视图同步)
        if url.path.startswith("/pov/") and url.path.endswith(".mp4"):
            return self._serve_ranged(url.path)
        if url.path == "/api/runs":
            runs = []
            if RUNS_DIR.is_dir():
                for cond_dir in sorted(RUNS_DIR.iterdir()):
                    if not cond_dir.is_dir():
                        continue
                    for f in sorted(cond_dir.glob("seed_*.jsonl"),
                                    key=lambda p: int(p.stem.split("_")[1])):
                        runs.append({"condition": cond_dir.name,
                                     "seed": int(f.stem.split("_")[1])})
            return self._json(runs)
        if url.path == "/api/log":
            q = parse_qs(url.query)
            cond = (q.get("condition") or [""])[0]
            seed = (q.get("seed") or [""])[0]
            if not _COND_RE.match(cond) or not seed.isdigit():
                return self._json({"error": "bad params"}, 400)
            path = RUNS_DIR / cond / f"seed_{seed}.jsonl"
            if not path.is_file():
                return self._json({"error": "not found"}, 404)
            events = [json.loads(line)
                      for line in path.read_text(encoding="utf-8").splitlines()
                      if line.strip()]
            return self._json(events)
        if url.path == "/api/summary":
            # 权威终态:复用 metrics.analyze_run 的分类(与 90-run 评测同一口径),避免 viewer
            # 只读 outcome_hint 造成 degraded_complete / safe_abort / 有违规的 run 被误显示成
            # 普通"完成"(codex 复核 PR#10:31/90 run 语义不一致)。
            q = parse_qs(url.query)
            cond = (q.get("condition") or [""])[0]
            seed = (q.get("seed") or [""])[0]
            if not _COND_RE.match(cond) or not seed.isdigit():
                return self._json({"error": "bad params"}, 400)
            path = RUNS_DIR / cond / f"seed_{seed}.jsonl"
            if not path.is_file():
                return self._json({"error": "not found"}, 404)
            try:
                import sys
                sys.path.insert(0, str(ROOT.parent))
                from embodied_agent.evaluation.metrics import analyze_run
                r = analyze_run(path)
                return self._json({"normalized_outcome": r["outcome"],
                                   "violations": r["violations"]})
            except Exception as e:   # 分类失败不阻断 viewer(前端回退到事件推断)
                return self._json({"error": str(e)}, 500)
        return super().do_GET()

    def _serve_ranged(self, path: str) -> None:
        name = path.rsplit("/", 1)[-1]
        if "/" in name or "\\" in name or ".." in name:
            return self._json({"error": "bad path"}, 400)
        f = ROOT / "pov" / name
        if not f.is_file():
            self.send_response(404)
            self.end_headers()
            return
        size = f.stat().st_size
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        status = 200
        if rng and rng.startswith("bytes="):
            spec = rng[6:].split(",")[0].strip()
            s, _, e = spec.partition("-")
            if s:
                start = int(s)
                end = int(e) if e else size - 1
            elif e:  # suffix: bytes=-N
                start = max(0, size - int(e))
            end = min(end, size - 1)
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            status = 206
        self.send_response(status)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(f, "rb") as fh:
            fh.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = fh.read(min(65536, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (ConnectionAbortedError, BrokenPipeError):
                    break
                remaining -= len(chunk)

    def log_message(self, fmt, *args):  # 安静
        pass


def main() -> None:
    global RUNS_DIR
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8777)
    parser.add_argument("--runs", default=str(RUNS_DIR))
    args = parser.parse_args()
    RUNS_DIR = Path(args.runs).resolve()
    # 线程化:POV 视频是长连接 Range 流,单线程 HTTPServer 会被它堵死其余请求(/api/*)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"replay viewer: http://127.0.0.1:{args.port}  (runs: {RUNS_DIR})")
    server.serve_forever()


if __name__ == "__main__":
    main()
