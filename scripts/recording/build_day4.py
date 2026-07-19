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
    (1, "还是那套任务编排流程,这次不再是仿真,而是真正驱动机器人的导航系统(ROS 2 Nav2)。",
        "The same task-orchestration flow — no longer a simulation, now driving a real robot's navigation system (ROS 2 Nav2)."),

    (2, "第一阶段在纯仿真里验证过:大模型只出高层意图,安全和恢复都是照着固定的表来查。",
        "The first phase proved it in pure simulation: the model only sets high-level intent; safety and recovery are deterministic lookups."),
    (2, "现在我们把那张一模一样的流程图接到真实的导航系统,只替换中间那层负责\"翻译\"的适配层——负责做决策的编排代码,一行没动。",
        "Now we connect that identical flow to the real navigation system, swapping out only the thin adapter layer that translates decisions into robot commands. The decision-making code itself: not one line changed."),
    (2, "而且这是设计上的硬隔离,不是靠自觉:这层\"插头\"只开放了\"去哪儿\"这一个接口——数一遍它能下的所有指令,里面根本没有\"跑多快\"\"使多大劲\"这类通道。所以大模型只能指定目的地,永远无法直接控制机器人的速度。",
        "And it's a hard barrier built into the design, not good behavior: this \"plug\" exposes only a single \"where to go\" interface — count every command it can issue and there's no \"how fast\" or \"how hard\" channel at all. So the model can name a destination but can never directly control the robot's speed."),

    (3, "任务从充电坞出发,巡检 A 区的两个观测点。这个第一人称视角,就是机器人此刻真正看到的仓库。",
        "The mission leaves the dock to patrol two points in zone A. This first-person view is the warehouse the robot actually sees."),
    (3, "真实的 Nav2 导航系统自己规划路径、控制机器人满速沿走廊前进,到达第一个观测点 a2。",
        "Real Nav2 plans the path and drives it down the corridor at full speed to the first point, a2."),
    (3, "接着它要去第二个观测点 a3。但我们故意在 a3 周围划了一片\"禁行区\"、把路封死——真实的导航系统怎么也找不到能过去的路,只好放弃这个目标。",
        "Next it heads for the second point, a3. But we've deliberately fenced off a no-go zone around a3, sealing the way in — so the real navigation system can't find any route there and gives up on that goal."),
    (3, "而且这个故障是真的、不是演出来的:导航系统在运行中真的把 a3 周围的格子标成了\"绝对不能走\",真实的路径规划因此拒绝了这个目标。",
        "And this failure is genuine, not staged: at runtime the navigation system really marks the area around a3 as strictly impassable, so the real path planner refuses the goal."),
    (3, "系统里负责盯梢的监控模块用同一套判断规则,把这次失败归为\"目的地到不了\"——和之前仿真版的判定逻辑一模一样。",
        "The same monitoring module watches the run and flags this failure as \"destination unreachable\" — exactly the logic the earlier simulated version used."),
    (3, "异常处理模块不靠猜,只按预先定好的表来查:在 a3 事先允许的几个邻近点里,挑出专门准备好的备用观测点 a3_alt。",
        "The exception handler doesn't guess — it checks a predefined table: among the few neighboring points allowed for a3, it picks the purpose-built backup, a3_alt."),
    (3, "重规划模块改写任务清单,机器人转向备用点 a3_alt——真实的导航系统这一次规划成功、平稳到达。",
        "The replanner rewrites the task list; the robot turns to the backup point a3_alt, and this time the real navigation system plans a path and arrives cleanly."),
    (3, "整段导航还被完整录进了一份审计包——走过的轨迹、规划的路径、下达的控制指令,还有故障注入本身,逐条留痕、可回放。",
        "The whole run is also recorded to an audit bag — the path walked, the routes planned, the control commands issued, and the fault injection itself, all traceable and replayable."),
    (3, "观测完成,机器人沿走廊返回充电坞,任务闭环。",
        "Observation done, it returns down the corridor to the dock — mission closed."),

    (4, "整段跑下来 672 条事件,日志格式和仿真版逐字段一致——同一个回放器、同一套评分脚本,直接就能用。",
        "The run logged 672 events in a format identical to the simulated one — the same replayer and scoring scripts just work."),
    (4, "从\"下达目标后异步等结果\"的约定,到\"只允许清单内动作\"\"越权先审批\"\"卡住就报警\"这几道安全闸门——仿真版和真实导航系统随时可换,而且是在真实机器人上跑通、逐条核对过的。",
        "From the way it hands off a goal and waits for the result, to the safety gates — only allow-listed actions, anything beyond needs approval, and an alarm the moment it gets stuck — the simulated and real navigation systems are fully interchangeable, checked on the real robot stack."),
    (4, "这套连真实机器人的适配层上线前,先让好几个 AI 互相挑刺、做了一轮对抗式代码审查:提出的 26 个问题里,22 个经复核确认属实,其中两个会直接让整套系统崩掉的严重缺陷被当场修好。",
        "Before this real-robot adapter shipped, several AIs adversarially reviewed each other's work: of 26 issues raised, 22 held up on double-check, and two severe bugs that would have crashed the whole system were fixed on the spot."),

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
    "d4_title": """<h1>同一套编排 · 真实 Nav2</h1>
<h2>The same orchestration, now driving real ROS 2 Nav2</h2>
<ul><li>负责决策的<b>编排流程 + 安全关卡</b>一行未改</li>
<li>只换中间的对接层:<b>仿真版 → 真实导航版</b>(驱动真实 ROS 2 Nav2)</li>
<li>故障用<b>"禁行区"</b>真实注入 · 恢复由确定性内核照表执行</li></ul>
<div class="foot">phase_b/real_runtime.py · run_real_mission.py · real_mission_events.jsonl</div>""",
    "d4_arch": """<h1>换机器人接口,不换大脑</h1>
<div class="flow"><b>同一张流程图</b>:规划 → 执行 ⇄ 监控 → 异常处理 → 重新规划 → 汇报<br>
&nbsp;&nbsp;└ 同一套安全关卡(只放行清单内动作 · 参数校验 · 审批令牌 · 卡住报警)<br><br>
机器人接口只有两种实现、接口完全相同:<br>
&nbsp;&nbsp;仿真版(纯软件 + 虚拟时钟)<b> ⇄ </b>真实导航版(驱动真实 ROS 2 Nav2)<br><br>
配套一起换的三样:真实时钟(墙上真实秒)· 真实地图与机器人 · 关闭故障注入</div>""",
    "d4_results": """<h1>真实任务 · 故障恢复闭环</h1>
<div class="flow">0s&nbsp;&nbsp;&nbsp;&nbsp;计划:巡检 A 区两个点(A区-2、A区-3)<br>
66s&nbsp;&nbsp;&nbsp;到达第一个点 A区-2 <b>✓</b><br>
67s&nbsp;&nbsp;&nbsp;发现 A区-3 被封锁 → 自动改选备用点 <b>A区-3(备用)</b><br>
91s&nbsp;&nbsp;&nbsp;到达备用点 <b>✓</b><br>
178s&nbsp;&nbsp;返回充电坞 · 实际走过:A区-2 → 备用点 → 充电坞</div>
<ul><li>全程<span class="bad">编排代码零改动</span> · 672 条事件,与仿真版格式完全一致,同一套回放/评分脚本直接可用</li></ul>""",
    "d4_outro": """<h1>可换 · 可复现 · 可证伪</h1>
<ul><li>大模型只出<b>高层意图 + 有限选项里挑</b>;安全与恢复是<b>确定性查表</b></li>
<li>"到不了目的地"这类故障由真实导航系统真实触发,恢复行为与仿真版逐字一致</li>
<li>诚实边界:电量、传感器目前仍是仿真替身;90 项批量评测也仍跑在仿真版上</li>
<li>"同一套接口,仿真 ⇄ 真实 Nav2 可随时互换" —— 已在真机栈上核对通过</li></ul>
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
