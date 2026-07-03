"""正式评测入口:检查 prereg 已提交 → 跑全矩阵 → 生成 RESULTS.md。

预注册协议(prereg.yaml / EVAL_PREREG.md):本脚本会在 prereg.yaml 尚未 commit 时拒绝跑,
保证"预测先于结果"可由 git 历史验证。
"""
from __future__ import annotations

import argparse
import asyncio
import io
import subprocess
import sys
from pathlib import Path

from embodied_agent.evaluation.harness import run_matrix
from embodied_agent.evaluation.metrics import render_results

ROOT = Path(__file__).resolve().parent


def prereg_committed() -> bool:
    r = subprocess.run(["git", "status", "--porcelain", "--", "prereg.yaml",
                        "faults.yaml"],
                       cwd=ROOT, capture_output=True, text=True)
    return r.returncode == 0 and r.stdout.strip() == ""


def main() -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="runs")
    parser.add_argument("--allow-dirty-prereg", action="store_true",
                        help="仅调试用:跳过 prereg 提交检查")
    args = parser.parse_args()

    if not args.allow_dirty_prereg and not prereg_committed():
        print("拒绝:prereg.yaml / faults.yaml 有未提交修改。"
              "预注册必须先 commit 再跑评测(见 EVAL_PREREG.md)。")
        sys.exit(1)

    paths = asyncio.run(run_matrix(Path(args.out)))
    print(f"完成 {len(paths)} 个 run。")
    render_results(Path(args.out), ROOT / "prereg.yaml", ROOT / "RESULTS.md")
    print("结果表已写入 RESULTS.md")


if __name__ == "__main__":
    main()
