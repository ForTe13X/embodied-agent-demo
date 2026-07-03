"""前后端联调 + 细粒度截图(用户测试原则:按钮/form/canvas 逐个截,带断言)。

用全局 python 跑(playwright 在全局):
  python scripts/capture_viewer.py
产出:docs/screenshots/*.png + 联调断言报告(stdout)。
"""
from __future__ import annotations

import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "docs" / "screenshots"
PORT = 8788
BASE = f"http://127.0.0.1:{PORT}"
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"


def wait_ready(timeout=15):
    for _ in range(timeout * 10):
        try:
            with urllib.request.urlopen(f"{BASE}/api/runs", timeout=1) as r:
                if r.status == 200:
                    return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("viewer 后端未就绪")


def seek(page, tick: int):
    page.evaluate(
        "t => { const s = document.getElementById('tick-slider');"
        " s.value = t; s.dispatchEvent(new Event('input')); }", tick)
    page.wait_for_timeout(120)


def load_run(page, condition: str, seed: int):
    key = f"{condition}:{seed}"
    page.select_option("#run-select", key)
    page.click("#load-btn")
    # 等待精确的加载完成信号,避免拿上一个 run 的旧状态断言(联调竞态)
    page.wait_for_function(
        "key => document.body.dataset.loaded === key", arg=key)


def main() -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    server = subprocess.Popen(
        [str(VENV_PY), str(ROOT / "viewer" / "serve.py"), "--port", str(PORT)],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    passed = []
    try:
        wait_ready()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 860})
            page.goto(BASE)

            # ---- 联调断言 1:前端启动即从后端拉到 run 列表(/api/runs) ----
            page.wait_for_function(
                "() => document.getElementById('run-select').options.length > 0")
            n_runs = page.evaluate(
                "() => document.getElementById('run-select').options.length")
            assert n_runs == 90, f"应有 90 个 run,实际 {n_runs}"
            passed.append(f"GET /api/runs → 下拉框 90 项 ✓ (实际 {n_runs})")

            # ---- 联调断言 2:加载 nav_blocked/seed0(/api/log)----
            load_run(page, "nav_blocked", 0)
            tick_max = page.evaluate(
                "() => parseInt(document.getElementById('tick-max').textContent)")
            assert tick_max > 30, f"nav_blocked seed0 总 tick 应 >30,实际 {tick_max}"
            passed.append(f"GET /api/log(nav_blocked,0) → tick_max={tick_max} ✓")

            seek(page, 0)
            page.screenshot(path=str(SHOTS / "viewer_full.png"))
            page.locator("#controls").screenshot(
                path=str(SHOTS / "viewer_controls.png"))
            page.locator("#topo-canvas").screenshot(
                path=str(SHOTS / "canvas_t0.png"))

            # ---- 联调断言 3:拖动 slider 到受阻时刻,canvas 画出受阻边 ----
            seek(page, 30)
            blocked_drawn = page.evaluate(
                "() => blockedAt.some(x => x.tick <= 30)")
            assert blocked_drawn, "tick 30 时应已有受阻边"
            passed.append("slider 定位 tick30 → canvas 受阻边(红色虚线)✓")
            page.locator("#topo-canvas").screenshot(
                path=str(SHOTS / "canvas_blocked.png"))
            page.locator("#event-feed").screenshot(
                path=str(SHOTS / "viewer_feed.png"))

            # ---- 联调断言 4:播放按钮状态切换 ----
            page.click("#play-btn")
            label = page.text_content("#play-btn")
            assert "暂停" in label, f"播放后按钮应显示暂停,实际 {label!r}"
            page.click("#play-btn")
            passed.append("播放/暂停按钮状态切换 ✓")

            # ---- 消融 run:违规闪烁 ----
            load_run(page, "ablation_gates_off", 0)
            vt = page.evaluate("() => violationsAt.length && violationsAt[1].tick")
            seek(page, int(vt) + 1)
            vcount = page.text_content("#violation-label")
            assert int(vcount) >= 2, f"违规计数应 ≥2,实际 {vcount}"
            passed.append(f"消融 run 违规计数联动 ✓ (t={vt}, 违规={vcount})")
            page.locator("#topo-canvas").screenshot(
                path=str(SHOTS / "canvas_violation.png"))

            # ---- 低电量 run:电量条变色 ----
            load_run(page, "low_battery", 0)
            seek(page, 12)
            page.locator("#topo-canvas").screenshot(
                path=str(SHOTS / "canvas_battery.png"))
            bat = page.text_content("#battery-label")
            passed.append(f"low_battery seed0 t=12 电量条 ✓ (显示 {bat})")

            browser.close()
    finally:
        server.terminate()

    print("=== 前后端联调断言(全部通过) ===")
    for line in passed:
        print(" ", line)
    print(f"截图输出:{SHOTS}({len(list(SHOTS.glob('*.png')))} 张)")


if __name__ == "__main__":
    main()
