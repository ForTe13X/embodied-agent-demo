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
    # 整棵工作树必须干净:预注册的行为大半在代码里,只查两个 YAML 挡不住
    # "先改代码再跑分"(复审 finding)
    r = subprocess.run(["git", "status", "--porcelain"],
                       cwd=ROOT, capture_output=True, text=True)
    dirty = [line for line in r.stdout.splitlines()
             if line.strip() and not line.split()[-1].startswith("runs/")]
    return r.returncode == 0 and not dirty


def main() -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="runs")
    parser.add_argument("--allow-dirty-prereg", action="store_true",
                        help="仅调试用:跳过 prereg 提交检查")
    args = parser.parse_args()

    if not args.allow_dirty_prereg and not prereg_committed():
        print("拒绝:工作树有未提交修改(runs/ 除外)。"
              "预注册协议要求先 commit 再跑评测(见 EVAL_PREREG.md)。")
        sys.exit(1)

    paths = asyncio.run(run_matrix(Path(args.out)))
    print(f"完成 {len(paths)} 个 run。")
    render_results(Path(args.out), ROOT / "prereg.yaml", ROOT / "RESULTS.md")
    print("结果表已写入 RESULTS.md")


if __name__ == "__main__":
    main()
