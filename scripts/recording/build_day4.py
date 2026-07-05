#!/usr/bin/env python3
"""Day-4 真实集成 POV 演示视频构建器(复用 build_video.py 的 edge-tts + 双语 SRT + ffmpeg 机器)。
结构:标题 → 架构(同一套图·换 adapter)→ POV 真实任务(含故障恢复)→ 结果 → 收尾。
POV 片段来自 povgen 渲染的 pov_day4.mp4(真实 run 的轨迹 + 事件驱动 HUD)。
用法:.venv\\Scripts\\python scripts\\recording\\build_day4.py --all
输出:docs/recording/day4_demo.mp4(≥3min,中英字幕烧录 + edge-tts 女声)
"""
import argparse
import asyncio
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SP = Path(__file__).resolve().parent
MEDIA = SP / "media"
OUT_DIR = ROOT / "docs" / "recording"
FF = shutil.which("ffmpeg") or "ffmpeg"
VOICE = "zh-CN-XiaoxiaoNeural"
RATE = "-6%"
W, H = 1280, 720
GAP, SCENE_PAD = 0.35, 0.9

# (scene, 中文, English)——文本口语化、非机翻腔
LINES = [
    (1, "同一套编排图,这次跑在真实的 ROS 2 Nav2 上。",
        "The same orchestration graph — this time on real ROS 2 Nav2."),

    (2, "Phase A 在仿真底盘上验证过:大模型只出高层意图,安全和恢复是确定性查表。",
        "Phase A proved it on a mock chassis: the model only sets high-level intent; safety and recovery are deterministic lookups."),
    (2, "现在我们把那张一模一样的 LangGraph 图接到真实 Nav2,只换掉 adapter,编排代码一行没动。",
        "Now we wire that identical LangGraph to real Nav2 — swapping only the adapter, with zero changes to the orchestration code."),
    (2, "而且这是结构性保证:这个 adapter 节点只暴露导航目标接口,枚举它的发布话题里没有任何速度或力矩通道——大模型拿不到底盘的速度控制。",
        "And it's a structural guarantee: the adapter node exposes only a navigation-goal interface. Enumerate its publishers and there's no velocity or torque channel — the model simply cannot command the chassis's speed."),

    (3, "任务从充电坞出发,巡检 A 区的两个观测点。这个第一人称视角,就是机器人此刻真正看到的仓库。",
        "The mission leaves the dock to patrol two points in zone A. This first-person view is the warehouse the robot actually sees."),
    (3, "真实的 Nav2 规划器和 MPPI 控制器驱动机器人满速沿走廊前进,到达第一个观测点 a2。",
        "Real Nav2 — planner and MPPI controller — drives it down the corridor at full speed to the first point, a2."),
    (3, "接着它要去 a3。但我们用 keepout 代价地图滤镜把 a3 封成了禁区——真实规划器找不到有效路径,返回 ABORTED。",
        "Next it heads for a3. But a keepout costmap filter has sealed a3 off — the real planner finds no valid path and returns ABORTED."),
    (3, "这个故障不是模拟出来的:是 Nav2 的 costmap 滤镜服务在运行时把 a3 周围的栅格改成致死代价,真实规划器据此拒绝了目标。",
        "The fault isn't faked: a Nav2 costmap-filter service marks the cells around a3 as lethal at runtime, and the real planner rejects the goal on that basis."),
    (3, "编排层的 observer 用同一套水位检测,把这次失败判成‘节点不可达’——和仿真语义逐字一致。",
        "The observer applies the same watchdog and classifies it as node-unreachable — byte-for-byte the mock's semantics."),
    (3, "异常管理器做确定性查表:从 a3 的合法邻接闭集里枚举替代点,选中专门的替代观测点 a3_alt。",
        "The exception manager does a deterministic lookup: from a3's legal neighbors it enumerates substitutes and picks the dedicated alternate, a3_alt."),
    (3, "重规划器改写任务队列,机器人转向 a3_alt——真实 Nav2 这一次规划成功、平稳到达。",
        "The replanner rewrites the queue; the robot turns to a3_alt, and this time real Nav2 plans a path and arrives cleanly."),
    (3, "整段导航还被录进了 MCAP 审计包——轨迹、规划路径、控制指令,还有故障注入本身,逐条留痕、可回放。",
        "The whole run is also recorded to an MCAP audit bag — trajectory, planned paths, control commands, and the fault injection itself, all traceable and replayable."),
    (3, "观测完成,机器人沿走廊返回充电坞,任务闭环。",
        "Observation done, it returns down the corridor to the dock — mission closed."),

    (4, "整段跑下来 672 条事件,日志格式和仿真逐字段一致——同一个回放器、同一套指标脚本,直接就能用。",
        "The run logged 672 events in a schema identical to the mock — the same replayer and metrics scripts just work."),
    (4, "从异步 goal-handle 契约,到白名单、审批 token、停滞水位,mock 和真实 Nav2 可换,是在真机栈上核对通过的事实。",
        "From the async goal-handle contract to the whitelist, approval tokens and stall watchdog — mock and real Nav2 are interchangeable, verified on the real stack."),
    (4, "这套真实适配器上线前,先过了一轮多 agent 对抗评审:26 条发现里 22 条经独立验证,两个会让集成崩溃的高危缺陷当场修掉。",
        "Before this real adapter shipped, it passed an adversarial multi-agent review: 22 of 26 findings survived verification, and two integration-breaking bugs were fixed on the spot."),

    (5, "不是设计口号,是跑通的闭环:大模型负责想法,确定性内核负责安全,底盘可以随时替换。",
        "Not a slogan but a working loop: the model owns the ideas, the deterministic core owns safety, and the chassis is swappable."),
]

SCENE_MEDIA = {
    1: ("slide", "d4_title"),
    2: ("slide", "d4_arch"),
    3: ("clip", "pov_day4"),
    4: ("slide", "d4_results"),
    5: ("slide", "d4_outro"),
}

_SLIDE_CSS = """
body{margin:0;width:1280px;height:720px;background:#0f1419;color:#d8e1ea;
 font-family:'Microsoft YaHei','Segoe UI';display:flex;flex-direction:column;
 justify-content:center;padding:0 90px;box-sizing:border-box}
h1{font-size:44px;margin:0 0 10px;color:#4ec9b0} h2{font-size:28px;margin:0 0 26px;color:#8194a7;font-weight:normal}
li{font-size:26px;line-height:2.0;color:#d8e1ea} b{color:#4ec9b0} .bad{color:#e06c75} .warn{color:#e5c07b}
.flow{font-family:Consolas;font-size:22px;line-height:1.95;color:#d8e1ea;background:#0c1116;
 border:1px solid #2a3542;border-radius:10px;padding:24px 32px}
.foot{position:absolute;bottom:28px;left:90px;color:#54657a;font-size:17px}
"""
SLIDES = {
    "d4_title": """<h1>同一套编排图 · 真实 Nav2</h1>
<h2>The same orchestration graph, now driving real ROS 2 Nav2</h2>
<ul><li>Phase A 的 <b>LangGraph 六节点图 + Tool Registry</b> 一行未改</li>
<li>只换 adapter:<b>MockAdapter → RclpyAdapter</b>(真实 Nav2 · tb3 loopback)</li>
<li>故障用 <b>keepout 代价地图滤镜</b>注入 · 恢复由确定性内核查表</li></ul>
<div class="foot">phase_b/real_runtime.py · run_real_mission.py · real_mission_events.jsonl</div>""",
    "d4_arch": """<h1>换 adapter,不换编排</h1>
<div class="flow">planner → executor ⇄ observer → exception_mgr → replanner → reporter<br>
&nbsp;&nbsp;└ 同一张 LangGraph 图、同一个 Tool Registry(白名单·schema·token·水位)<br><br>
RobotAdapter 契约(异步 goal-handle):<br>
&nbsp;&nbsp;Phase A → <b>MockAdapter</b>(虚拟时钟 + mock server)<br>
&nbsp;&nbsp;Phase B → <b>RclpyAdapter</b>(BasicNavigator · 真实 <span class="warn">/navigate_to_pose</span>)<br><br>
shim 三件套:RealClock(墙钟秒)· RealWorld(topo/robot_node)· NoopInjector</div>""",
    "d4_results": """<h1>真实 run · 故障恢复闭环</h1>
<div class="flow">t=  0  planner   计划:巡检 a2 → a3<br>
t= 66  observer  navigate a2 <b>成功</b><br>
t= 67  exception 枚举 a3 替代闭集 = [<b>a3_alt</b>] → 选中<br>
t= 67  replanner substitute  a3 → a3_alt<br>
t= 91  observer  navigate a3_alt <b>成功</b><br>
t=178  reporter  归坞 · visited=[a2, a3_alt, dock] · <span class="bad">编排零改动</span></div>
<ul><li>672 条事件 · schema 与 mock 逐字段一致 · 同一 viewer/metrics 可用</li></ul>""",
    "d4_outro": """<h1>可换 · 可复现 · 可证伪</h1>
<ul><li>大模型只出<b>高层意图 + 闭集选择</b>;安全与恢复是<b>确定性查表</b></li>
<li>受阻/不可达经真实 Nav2 触发,恢复语义与 mock <b>1:1</b></li>
<li>诚实边界:battery/sensor 仍 mock-only;90 条评测仍用 mock adapter</li>
<li>“同一接口 mock ⇄ 真实 Nav2 可换” —— 在真机栈上核对通过的事实</li></ul>
<div class="foot">github: embodied-agent-demo · phase_b/FINDINGS.md</div>""",
}


def dur_of(path: Path) -> float:
    p = subprocess.run([FF, "-i", str(path), "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", p.stderr)
    h, mn, s = m.groups()
    return int(h) * 3600 + int(mn) * 60 + float(s)


async def gen_tts() -> None:
    import edge_tts
    MEDIA.mkdir(parents=True, exist_ok=True)
    out = []
    for i, (scene, zh, en) in enumerate(LINES):
        mp3 = MEDIA / f"d4_line_{i:02d}.mp3"
        d = 0.0
        for attempt in range(4):        # edge-tts 偶发网络抖动 → 重试 + 校验非空可解
            try:
                await edge_tts.Communicate(zh, VOICE, rate=RATE).save(str(mp3))
                if mp3.stat().st_size > 800:
                    d = dur_of(mp3)
                    if d > 0.3:
                        break
            except Exception as e:
                print(f"    line {i:02d} tts 重试 {attempt+1}: {e}")
            await asyncio.sleep(1.2)
        else:
            raise RuntimeError(f"line {i:02d} edge-tts 连续失败,mp3 无效")
        out.append({"i": i, "scene": scene, "zh": zh, "en": en, "mp3": mp3.name,
                    "dur": round(d, 3)})
        print(f"  line {i:02d} s{scene} {d:5.2f}s  {zh[:28]}")
    (MEDIA / "d4_narration.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"== {len(out)} 句,语音合计 {sum(x['dur'] for x in out):.1f}s ==")


def make_slides() -> None:
    from playwright.sync_api import sync_playwright
    MEDIA.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": W, "height": H})
        for name, body in SLIDES.items():
            page.set_content(f"<style>{_SLIDE_CSS}</style>{body}")
            page.wait_for_timeout(150)
            page.screenshot(path=str(MEDIA / f"slide_{name}.png"))
            print(f"  slide_{name}.png")
        browser.close()


def _ts(sec: float) -> str:
    ms = int(round(max(0.0, sec) * 1000))
    return "%02d:%02d:%02d,%03d" % (ms // 3600000, ms % 3600000 // 60000,
                                    ms % 60000 // 1000, ms % 1000)


def assemble() -> None:
    lines = json.loads((MEDIA / "d4_narration.json").read_text(encoding="utf-8"))
    cursor = 0.6
    scenes: dict = {}
    for ln in lines:
        s = ln["scene"]
        if s not in scenes:
            if scenes:
                cursor += SCENE_PAD
            scenes[s] = {"start": cursor, "lines": []}
        ln["t"] = cursor
        scenes[s]["lines"].append(ln)
        cursor += ln["dur"] + GAP
    total = cursor + 1.2
    for s, info in scenes.items():
        nxt = [x["start"] for x in scenes.values() if x["start"] > info["start"]]
        info["dur"] = (min(nxt) if nxt else total) - info["start"]
    print(f"总时长 {total:.1f}s,{len(scenes)} 场景")

    vf_fit = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
              f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x0f1419,fps=30,format=yuv420p")
    segs = []
    for s in sorted(scenes):
        info = scenes[s]
        kind, src = SCENE_MEDIA[s]
        seg = MEDIA / f"d4_seg_{s}.mp4"
        if kind == "slide":
            img = MEDIA / f"slide_{src}.png"
            cmd = [FF, "-y", "-loglevel", "error", "-loop", "1",
                   "-t", f"{info['dur']:.3f}", "-i", str(img), "-vf", vf_fit,
                   "-c:v", "libx264", "-preset", "fast", "-crf", "20", str(seg)]
        else:
            clip = MEDIA / f"clip_{src}.mp4"
            ratio = info["dur"] / dur_of(clip)
            cmd = [FF, "-y", "-loglevel", "error", "-i", str(clip),
                   "-vf", f"setpts={ratio:.5f}*PTS,{vf_fit}",
                   "-t", f"{info['dur']:.3f}", "-an",
                   "-c:v", "libx264", "-preset", "fast", "-crf", "20", str(seg)]
        subprocess.run(cmd, check=True)
        segs.append(seg)
        print(f"  d4_seg_{s}.mp4 {dur_of(seg):.1f}s ({kind}:{src})")

    (MEDIA / "d4_concat.txt").write_text(
        "".join(f"file '{s.name}'\n" for s in segs), encoding="utf-8")
    subprocess.run([FF, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                    "-i", "d4_concat.txt", "-c", "copy", "d4_video_full.mp4"],
                   check=True, cwd=MEDIA)

    srt = []
    for k, ln in enumerate(lines):
        end = min(ln["t"] + ln["dur"] + 0.25,
                  lines[k + 1]["t"] - 0.05 if k + 1 < len(lines) else total)
        srt.append(f"{k+1}\n{_ts(ln['t'])} --> {_ts(end)}\n{ln['zh']}\n{ln['en']}\n")
    (MEDIA / "d4_subs.srt").write_text("\n".join(srt), encoding="utf-8")

    inputs, fc = [], []
    for k, ln in enumerate(lines):
        inputs += ["-i", str(MEDIA / ln["mp3"])]
        fc.append(f"[{k}:a]aresample=44100,aformat=channel_layouts=stereo,"
                  f"adelay={int(round(ln['t']*1000))}:all=1[a{k}]")
    fc.append("".join(f"[a{k}]" for k in range(len(lines)))
              + f"amix=inputs={len(lines)}:normalize=0:dropout_transition=0[m]")
    fc.append(f"[m]apad,atrim=0:{total:.3f},aresample=44100[outa]")
    subprocess.run([FF, "-y", "-loglevel", "error", *inputs,
                    "-filter_complex", ";".join(fc), "-map", "[outa]",
                    "-c:a", "aac", "-b:a", "160k", "d4_narration.m4a"],
                   check=True, cwd=MEDIA)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    style = ("FontName=Microsoft YaHei,FontSize=15,PrimaryColour=&H00FFFFFF,"
             "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,"
             "MarginV=24,Alignment=2")
    subprocess.run([FF, "-y", "-loglevel", "error",
                    "-i", "d4_video_full.mp4", "-i", "d4_narration.m4a",
                    "-filter_complex",
                    f"[0:v]subtitles=d4_subs.srt:force_style='{style}'[v]",
                    "-map", "[v]", "-map", "1:a", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "21",
                    "-c:a", "aac", "-shortest", "-movflags", "+faststart",
                    "day4_demo.mp4"], check=True, cwd=MEDIA)
    shutil.copy(MEDIA / "day4_demo.mp4", OUT_DIR / "day4_demo.mp4")
    shutil.copy(MEDIA / "d4_subs.srt", OUT_DIR / "day4_demo.srt")
    d = dur_of(OUT_DIR / "day4_demo.mp4")
    print(f"== docs/recording/day4_demo.mp4  {d:.1f}s ({d/60:.1f}min) "
          f"{'OK >=3min' if d >= 180 else '!! <3min'} ==")


def main() -> None:
    parser = argparse.ArgumentParser()
    for step in ("tts", "slides", "assemble", "all"):
        parser.add_argument(f"--{step}", action="store_true")
    args = parser.parse_args()
    if args.all or args.tts:
        asyncio.run(gen_tts())
    if args.all or args.slides:
        make_slides()
    if args.all or args.assemble:
        assemble()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
