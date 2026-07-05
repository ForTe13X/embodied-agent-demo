"""演示录屏流水线(local-first-ai-app skill 管线,用全局 python 跑):

  python scripts/recording/build_video.py --all
  (或分步 --tts --slides --clips --assemble)

产出:docs/recording/demo.mp4(≥3min,中英双语字幕烧录,zh-CN-XiaoxiaoNeural 女声)+ demo.srt

管线要点(skill gotchas):edge-tts 无词级时间 → 逐句 mp3 + 实测时长累计打轴;
烧字幕 cd 进 media 目录用相对路径躲 Windows 盘符冒号;时长解析用 ffmpeg stderr(无 ffprobe 依赖)。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SP = Path(__file__).resolve().parent
MEDIA = SP / "media"
SHOTS = ROOT / "docs" / "screenshots"
OUT_DIR = ROOT / "docs" / "recording"
FF = shutil.which("ffmpeg") or "ffmpeg"
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
VOICE = "zh-CN-XiaoxiaoNeural"
RATE = "-6%"
W, H = 1280, 720
GAP, SCENE_PAD = 0.35, 0.9

# (scene, zh, en) —— 每句一条字幕,scene 决定画面
# 旁白经 GPT-5.5 Pro 润色 + 人工编辑合并(保留三视图/术语,修正过度压缩)
LINES = [
    (1, "这是具身智能体任务编排层的可评测参考实现",
        "An embodied-agent orchestration layer, built to be measurable."),
    (1, "大模型管高层意图,确定性内核管安全和恢复",
        "The LLM handles intent only; a deterministic core owns safety and recovery."),
    (1, "接下来四分钟,看它怎么被验证",
        "In the next four minutes, watch how it is verified."),
    (2, "自然语言先做意图解析,本地大模型可选,规则兜底",
        "Natural language goes through intent parsing: local LLM optional, rules as fallback."),
    (2, "整个任务按一套固定循环推进:先规划,再执行、观察;一旦出状况就判别原因、重新规划,最后汇报",
        "The whole task follows one fixed loop: plan, then act and observe; when something goes wrong it diagnoses the cause, replans, and reports."),
    (2, "每个动作都要先过一道统一的安全关卡:只放行清单内的动作、检查参数合法、出错自动断电、危险操作要凭审批令牌",
        "Every action must clear one safety gate: only listed actions pass, inputs are checked, faults auto-cut power, and risky moves need an approval token."),
    (2, "底盘下方有个独立\"裁判\":它不听机器人自己怎么说,只客观记录它每一次越界",
        "Beneath the base sits an independent referee: it ignores what the robot claims and objectively logs every time it crosses a line."),
    (3, "先看第一个场景,路被挡住:进行到第12步时,我们人为制造一次故障,封死机器人前方的这段路",
        "First scene — the road ahead is blocked: at step 12 we deliberately trigger a fault that seals off the path in front of the robot."),
    (3, "底盘不会主动上报,只会原地停滞",
        "The base never reports 'blocked' — it just stalls."),
    (3, "编排层察觉机器人卡住不动,先自动重试一次,不行就绕开这段路重新规划",
        "The orchestration layer notices the robot has stalled, retries once, then replans a route around the blockage."),
    (3, "改走另一条通道绕到目标,顺手把挡路的异常物体拍照上报,全程留痕",
        "It reroutes down another corridor to the goal, photographs the blocking object to report it, and logs every step."),
    (4, "同一次任务在指挥台上三个画面同步回放:机器人第一视角、路线拓扑图、以及它每一步的决策记录",
        "The same run replays in mission control, three views on one timeline: the robot's first-person view, a route map, and a log of every decision."),
    (4, "拓扑图上,红色虚线是被阻断的边,青色圆点是机器人",
        "On the map, the red dashed line is the blocked edge; the teal dot is the robot."),
    (4, "轨迹来自地面真值事件,不是智能体自述",
        "The trajectory comes from ground-truth events, not the agent's own account."),
    (4, "右下角事件流,是评测指标的唯一数据源",
        "The event feed is the only data source for all metrics."),
    (5, "再看第一人称:同一任务,仓库由同一张拓扑图程序化生成",
        "Now in first person — a warehouse generated from the same topology."),
    (5, "镜头严格沿着裁判记录的真实轨迹移动,和事件日志一步一步精确对齐",
        "The camera moves exactly along the referee's recorded true path, matched step by step with the log."),
    (5, "前方这堆箱子,就是我们故意设下的路障:机器人停住、倒车、再绕行",
        "The crate stack ahead is the roadblock we planted on purpose: the robot stalls, backs off, then detours."),
    (5, "机器人观察时,视觉AI在画面上圈出它看到的东西,标上名称和把握有多大——它只负责\"看清并报告\",不替机器人做决定",
        "When the robot looks, the vision AI boxes what it sees on screen — a label and how confident it is. It only reports; it never decides the robot's next move."),
    (6, "低电量场景:巡检中,电量跌破百分之二十红线",
        "Low battery: mid-patrol, charge drops below the 20 percent red line."),
    (6, "系统在任务进行到一半时立刻接管,先把没做完的任务清单原样存下来",
        "The system takes over right in the middle of the task, first saving the unfinished to-do list exactly as it is."),
    (6, "回坞充电后,从断点继续,原队列一步不丢",
        "The robot docks, recharges, and resumes the original queue — nothing lost."),
    (6, "这条红线是写死的规则,AI 只能把它调得更严,绝不能放松",
        "That red line is a hard-coded rule: the AI can only make it stricter, never relax it."),
    (7, "进入受限区,必须人工审批",
        "Entering a restricted zone requires human approval."),
    (7, "批准后签发一次性令牌:限范围、限时效、用完即废",
        "Approval mints a single-use token — scoped, expiring, spent on use."),
    (7, "没有这张令牌,安全关卡会直接拦下这个动作;而绝对禁入的区域,连令牌也进不去",
        "Without that token, the safety gate blocks the action outright; and truly off-limits zones accept no token at all."),
    (8, "安全声明要有证据:我们让恶意规划器攻击门禁",
        "Safety claims need evidence — so a malicious planner attacks the gates."),
    (8, "门禁开启:六次越权尝试,六次都拦截,零违规",
        "Gates on: six rogue attempts, six interceptions, zero violations."),
    (8, "消融实验关掉门禁:每次运行恰好五次真实违规",
        "The ablation turns gates off: exactly five real violations per run."),
    (8, "红圈闪烁处,就是地面真值记下的每次越界",
        "Each flashing red ring is a violation recorded by the ground truth."),
    (9, "评测前我们先把预测写下来存档、盖上时间戳,再去跑实验;时间戳证明我们没有事后改口",
        "Before testing, we lock in our predictions with a timestamp — then run the experiments. Proof we didn't rewrite them afterward."),
    (9, "九种场景,每种换十组随机起始条件,共九十次运行,三十一条预测全部命中",
        "Nine scenarios, each rerun ten times from a different random starting point — ninety runs in all, and all thirty-one predictions held."),
    (9, "最难的多重故障场景里,有三次机器人电量彻底耗尽——我们照实报失败,并附完整事件日志",
        "In the hardest multi-failure scenario, three runs ran clean out of power — we report the failures honestly, with full logs attached."),
    (9, "代码复审查出三十五个缺陷;修好后按事先定下的规则重跑,结论依旧不变",
        "A code review found 35 defects; after fixing them, we reran by the rules fixed in advance, and every prediction still held."),
    (10, "这是仿真演示,没有实机;边界写在文档第一行",
        "This is a simulation demo, no real robot — stated in line one of the docs."),
    (10, "要换成真正的机器人,只需替换一个对接模块;它们怎么连、边界在哪,我们已经提前定死了",
        "To move to a real robot, you only swap one connector — how the pieces plug together is already locked in."),
    (10, "可复现、可回放、说错了能被验证推翻——这就是这段演示要证明的",
        "Reproducible, replayable, and provable wrong if we're wrong — that is the whole point."),
]

# scene → 画面来源:("img", path) 静态图 | ("clip", key) viewer/POV 实录
SCENE_MEDIA = {
    1: ("slide", "title"), 2: ("slide", "arch"),
    3: ("img", SHOTS / "terminal_blocked.png"),
    4: ("clip", "blocked"), 5: ("clip", "pov"), 6: ("clip", "battery"),
    7: ("img", SHOTS / "terminal_restricted.png"),
    8: ("clip", "ablation"), 9: ("slide", "results"), 10: ("slide", "outro"),
}
CLIPS = {  # key → (condition, seed, tick/s, 起始tick)
    "blocked": ("nav_blocked", 0, 6, 0),
    "battery": ("low_battery", 0, 6, 0),
    "ablation": ("ablation_gates_off", 0, 2, 0),
}


def dur_of(path: Path) -> float:
    p = subprocess.run([FF, "-i", str(path), "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", p.stderr)
    h, mn, s = m.groups()
    return int(h) * 3600 + int(mn) * 60 + float(s)


# ---------- step 1: TTS ----------

async def gen_tts() -> None:
    import edge_tts
    MEDIA.mkdir(parents=True, exist_ok=True)
    out = []
    for i, (scene, zh, en) in enumerate(LINES):
        mp3 = MEDIA / f"line_{i:02d}.mp3"
        await edge_tts.Communicate(zh, VOICE, rate=RATE).save(str(mp3))
        d = dur_of(mp3)
        out.append({"i": i, "scene": scene, "zh": zh, "en": en,
                    "mp3": mp3.name, "dur": round(d, 3)})
        print(f"  line {i:02d} s{scene} {d:5.2f}s  {zh}")
    (MEDIA / "narration_lines.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"== 共 {len(out)} 句,语音合计 {sum(x['dur'] for x in out):.1f}s ==")


# ---------- step 2: slides ----------

_SLIDE_CSS = """
body{margin:0;width:1280px;height:720px;background:#0f1419;color:#d8e1ea;
 font-family:'Microsoft YaHei','Segoe UI';display:flex;flex-direction:column;
 justify-content:center;padding:0 90px;box-sizing:border-box}
h1{font-size:44px;margin:0 0 10px;color:#4ec9b0} h2{font-size:30px;margin:0 0 26px;color:#8194a7;font-weight:normal}
li{font-size:26px;line-height:2.1;color:#d8e1ea} b{color:#4ec9b0} .bad{color:#e06c75} .warn{color:#e5c07b}
table{border-collapse:collapse;font-size:23px;margin-top:8px}
td,th{border:1px solid #2a3542;padding:8px 20px;text-align:left} th{color:#8194a7}
.flow{font-family:Consolas;font-size:23px;line-height:1.9;color:#d8e1ea;background:#0c1116;
 border:1px solid #2a3542;border-radius:10px;padding:26px 34px}
.foot{position:absolute;bottom:28px;left:90px;color:#54657a;font-size:17px}
"""
SLIDES = {
    "title": """<h1>具身 Agent 任务编排层</h1>
<h2>Embodied-Agent Orchestration Layer — a measurable reference</h2>
<ul><li>大模型只出高层意图 · <b>确定性内核</b>负责安全与恢复</li>
<li>预先登记好的故障注入 × 每种 10 次随机 × 指标表,<b>没恢复成功的也原样报</b></li>
<li>跑在纯软件仿真里(没有真机)· 换一个对接模块即可驱动真实机器人的导航(Nav2)</li></ul>
<div class="foot">一键演示 · 一键评分 · 过程可回放 · 31 项预测提前登记,全部命中</div>""",
    "arch": """<h1>架构:一条唯一通道</h1>
<div class="flow">自然语言 → 意图解析(<b>本地大模型</b> → 云 → 规则兜底)<br>
&nbsp;&nbsp;→ 固定循环:规划 → 执行 ⇄ 监控 → 异常处理 → 重新规划 → 汇报<br>
&nbsp;&nbsp;→ <b>Tool Registry(工具关卡)</b>:仅放行清单内动作 · 参数严格校验 · 失败可安全重试 · 故障自动断电 · 危险操作需审批 · 低电量强制拦截<br>
&nbsp;&nbsp;→ 对接层向机器人下发目标点、跟踪执行进度(现接模拟机器人;同一接口可切换真实机器人)<br>
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;└ <span class="bad">独立裁判(不听机器人自报,只客观记录违规)</span><br>
旁路留痕:全程记一份不可篡改的流水账,每步决策都能倒带重放,分数只从这份流水账里算</div>""",
    "results": """<h1>评测:预测先于结果</h1>
<table><tr><th>场景 × 每种 10 次随机</th><th>结果(成功数/总数)</th><th>预注册命中</th></tr>
<tr><td>正常 / 走廊被封 / 低电量</td><td>全部圆满完成 10/10 ×3 组</td><td>✓</td></tr>
<tr><td>目标进不去 / 传感器坏 / 工具失败</td><td>降级也完成 10/10 ×3(改走可达替代目标)</td><td>✓</td></tr>
<tr><td class="warn">复合故障(受阻+低电量)</td><td class="warn">6 完成 · 1 主动放弃(安全) · <span class="bad">3 电量耗尽(如实计入)</span></td><td>✓</td></tr>
<tr><td>对抗攻击测试(安全门禁开启)</td><td>危险指令 6/6 全拦下 · 0 违规</td><td>✓</td></tr>
<tr><td class="bad">消融对照:故意关掉安全闸</td><td class="bad">每次跑都恰好 5 次违规(独立裁判实测)</td><td>✓</td></tr></table>
<ul><li>有公开时间戳存档为证:预测先写死存档,再跑出结果;随机条件全程固定,不许专挑好看的那次</li>
<li>多个 AI 审查员交叉复查,主动查出并修复 35 处问题;之后按同一套规则重跑,先前写死的 31 条预测依旧条条命中</li></ul>""",
    "outro": """<h1>可复现 · 可回放 · 可证伪</h1>
<ul><li>同样条件下每次跑出来<b>分毫不差、完全一致</b>(有自动测试守住)</li>
<li>违规和完成率都由<b>独立裁判</b>实测记录,绝不采信机器人自报的成绩</li>
<li>诚实划清边界:纯软件仿真演示、没有真实机器人;只能在仿真里模拟的故障都明确标注</li>
<li>换一个对接模块就能驱动真实机器人导航(ROS 2 Nav2)——接口标准和"失败即叫停"的红线提前定死,<b>已在真机栈上跑通验证</b></li></ul>
<div class="foot">github: embodied-agent-demo · docs/ 全套 API·手册·测试用例·产品文档</div>""",
}


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


# ---------- step 3: viewer 实录片段 ----------

def capture_clips() -> None:
    from playwright.sync_api import sync_playwright
    MEDIA.mkdir(parents=True, exist_ok=True)
    port = 8799
    server = subprocess.Popen(
        [str(VENV_PY), str(ROOT / "viewer" / "serve.py"), "--port", str(port)],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/runs",
                                       timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for key, (cond, seed, tps, start) in CLIPS.items():
                ctx = browser.new_context(
                    viewport={"width": W, "height": H},
                    record_video_dir=str(MEDIA / "cap"),
                    record_video_size={"width": W, "height": H})
                page = ctx.new_page()
                page.goto(f"http://127.0.0.1:{port}")
                page.wait_for_function(
                    "() => document.getElementById('run-select').options.length > 0")
                k = f"{cond}:{seed}"
                page.select_option("#run-select", k)
                page.click("#load-btn")
                page.wait_for_function(
                    "key => document.body.dataset.loaded === key", arg=k)
                page.select_option("#speed-select", str(tps))
                # 三视图:等 POV 视频就绪(有的话)再开播,录进同轴画面
                try:
                    page.wait_for_function(
                        "() => !document.body.classList.contains('no-pov')",
                        timeout=8_000)
                except Exception:
                    pass  # 该 run 无 POV,双栏布局照录
                if start:
                    page.evaluate(
                        "t => { const s = document.getElementById('tick-slider');"
                        " s.value = t; s.dispatchEvent(new Event('input')); }", start)
                page.click("#play-btn")
                page.wait_for_function(
                    "() => document.getElementById('play-btn').textContent.includes('播放')",
                    timeout=120_000)  # 播完自动复位
                page.wait_for_timeout(600)
                video = page.video
                ctx.close()
                path = Path(video.path())
                dest = MEDIA / f"clip_{key}.webm"
                shutil.move(path, dest)
                print(f"  clip_{key}.webm  {dur_of(dest):.1f}s")
            browser.close()
    finally:
        server.terminate()


# ---------- step 4: assemble ----------

def _ts(sec: float) -> str:
    ms = int(round(max(0.0, sec) * 1000))
    return "%02d:%02d:%02d,%03d" % (ms // 3600000, ms % 3600000 // 60000,
                                    ms % 60000 // 1000, ms % 1000)


def assemble() -> None:
    lines = json.loads((MEDIA / "narration_lines.json").read_text(encoding="utf-8"))
    # 时间轴:逐句 cue(scene 内 GAP,scene 间 SCENE_PAD)
    cursor = 0.6
    scenes: dict[int, dict] = {}
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
    print(f"总时长 {total:.1f}s,{len(scenes)} 个场景")

    # 视频段:统一 1280x720/30fps/yuv420p
    vf_fit = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
              f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x0f1419,"
              f"fps=30,format=yuv420p")
    segs = []
    for s in sorted(scenes):
        info = scenes[s]
        kind, src = SCENE_MEDIA[s]
        seg = MEDIA / f"seg_{s}.mp4"
        if kind in ("slide", "img"):
            img = MEDIA / f"slide_{src}.png" if kind == "slide" else Path(src)
            cmd = [FF, "-y", "-loglevel", "error", "-loop", "1",
                   "-t", f"{info['dur']:.3f}", "-i", str(img),
                   "-vf", vf_fit, "-c:v", "libx264", "-preset", "fast",
                   "-crf", "20", str(seg)]
        else:
            clip = MEDIA / f"clip_{src}.webm"
            if not clip.exists():
                clip = MEDIA / f"clip_{src}.mp4"  # POV 片段由 Godot 帧序列合成
            ratio = info["dur"] / dur_of(clip)
            cmd = [FF, "-y", "-loglevel", "error", "-i", str(clip),
                   "-vf", f"setpts={ratio:.5f}*PTS,{vf_fit}",
                   "-t", f"{info['dur']:.3f}", "-an",
                   "-c:v", "libx264", "-preset", "fast", "-crf", "20", str(seg)]
        subprocess.run(cmd, check=True)
        segs.append(seg)
        print(f"  seg_{s}.mp4 {dur_of(seg):.1f}s ({kind}:{src})")
    concat_txt = MEDIA / "concat.txt"
    concat_txt.write_text("".join(f"file '{s.name}'\n" for s in segs),
                          encoding="utf-8")
    video = MEDIA / "video_full.mp4"
    subprocess.run([FF, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                    "-i", "concat.txt", "-c", "copy", str(video)],
                   check=True, cwd=MEDIA)

    # 双语 SRT(zh 上 en 下,逐句打轴)
    srt = []
    for k, ln in enumerate(lines):
        end = min(ln["t"] + ln["dur"] + 0.25,
                  lines[k + 1]["t"] - 0.05 if k + 1 < len(lines) else total)
        srt.append(f"{k+1}\n{_ts(ln['t'])} --> {_ts(end)}\n{ln['zh']}\n{ln['en']}\n")
    (MEDIA / "subs.srt").write_text("\n".join(srt), encoding="utf-8")

    # 音轨:逐句 adelay 落位
    inputs, fc = [], []
    for k, ln in enumerate(lines):
        inputs += ["-i", str(MEDIA / ln["mp3"])]
        fc.append(f"[{k}:a]aresample=44100,aformat=channel_layouts=stereo,"
                  f"adelay={int(round(ln['t']*1000))}:all=1[a{k}]")
    fc.append("".join(f"[a{k}]" for k in range(len(lines)))
              + f"amix=inputs={len(lines)}:normalize=0:dropout_transition=0[m]")
    fc.append(f"[m]apad,atrim=0:{total:.3f},aresample=44100[outa]")
    narr = MEDIA / "narration.m4a"
    subprocess.run([FF, "-y", "-loglevel", "error", *inputs,
                    "-filter_complex", ";".join(fc), "-map", "[outa]",
                    "-c:a", "aac", "-b:a", "160k", str(narr)], check=True)

    # 烧字幕 + 合音轨(cwd=MEDIA,相对路径躲盘符冒号)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    style = ("FontName=Microsoft YaHei,FontSize=15,PrimaryColour=&H00FFFFFF,"
             "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,"
             "MarginV=24,Alignment=2")
    mp4 = MEDIA / "demo.mp4"
    subprocess.run([FF, "-y", "-loglevel", "error",
                    "-i", "video_full.mp4", "-i", "narration.m4a",
                    "-filter_complex",
                    f"[0:v]subtitles=subs.srt:force_style='{style}'[v]",
                    "-map", "[v]", "-map", "1:a", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "21",
                    "-c:a", "aac", "-shortest", "-movflags", "+faststart",
                    "demo.mp4"], check=True, cwd=MEDIA)
    shutil.copy(mp4, OUT_DIR / "demo.mp4")
    shutil.copy(MEDIA / "subs.srt", OUT_DIR / "demo.srt")
    d = dur_of(OUT_DIR / "demo.mp4")
    print(f"== docs/recording/demo.mp4  {d:.1f}s ({d/60:.1f}min) "
          f"{'✓ ≥3min' if d >= 180 else '✗ 不足 3min!'} ==")


def main() -> None:
    parser = argparse.ArgumentParser()
    for step in ("tts", "slides", "clips", "assemble", "all"):
        parser.add_argument(f"--{step}", action="store_true")
    args = parser.parse_args()
    if args.all or args.tts:
        asyncio.run(gen_tts())
    if args.all or args.slides:
        make_slides()
    if args.all or args.clips:
        capture_clips()
    if args.all or args.assemble:
        assemble()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
