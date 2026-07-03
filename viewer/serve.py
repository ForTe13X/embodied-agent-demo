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
from http.server import HTTPServer, SimpleHTTPRequestHandler
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
        return super().do_GET()

    def log_message(self, fmt, *args):  # 安静
        pass


def main() -> None:
    global RUNS_DIR
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8777)
    parser.add_argument("--runs", default=str(RUNS_DIR))
    args = parser.parse_args()
    RUNS_DIR = Path(args.runs).resolve()
    server = HTTPServer(("127.0.0.1", args.port), Handler)
    print(f"replay viewer: http://127.0.0.1:{args.port}  (runs: {RUNS_DIR})")
    server.serve_forever()


if __name__ == "__main__":
    main()
