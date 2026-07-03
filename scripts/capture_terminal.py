"""终端演示截图:rich 录制 → SVG → playwright 截成 PNG(docs 用)。

用全局 python 跑:python scripts/capture_terminal.py
前置:项目 venv 可用(demo 在 venv 里跑,截图工具在全局)。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "docs" / "screenshots"
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"

CAPTURE_SNIPPET = r"""
import asyncio, sys
sys.argv = ["run_demo.py", "--scenario", "{scenario}", "--tick", "0"]
import run_demo
from rich.console import Console
run_demo.console = Console(record=True, width=104, force_terminal=True)
import argparse
args = argparse.Namespace(scenario="{scenario}", seed=0, tick=0.0,
                          interactive=False, nl=None, llm=False)
if "{scenario}" == "restricted":
    asyncio.run(run_demo.run_restricted_demo(args))
else:
    asyncio.run(run_demo.run_scenario(args))
run_demo.console.save_html(r"{svg}")
"""


def main() -> None:
    import re
    SHOTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for scenario in ("blocked", "battery", "restricted"):
            html = SHOTS / f"terminal_{scenario}.html"
            code = CAPTURE_SNIPPET.format(scenario=scenario, svg=str(html))
            subprocess.run([str(VENV_PY), "-c", code], cwd=ROOT, check=True,
                           env={"PYTHONUTF8": "1", "PATH": "", "SYSTEMROOT": r"C:\Windows"})
            page = browser.new_page(viewport={"width": 1360, "height": 900})
            page.goto(html.resolve().as_uri())
            page.wait_for_timeout(200)
            # rich save_html 输出 <pre>;元素截图自动取全高,HTML 不会整体缩放
            page.locator("pre").screenshot(
                path=str(SHOTS / f"terminal_{scenario}.png"))
            page.close()
            html.unlink()
            print(f"terminal_{scenario}.png ok")
        browser.close()


if __name__ == "__main__":
    main()
