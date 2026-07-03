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
LINES = [
    (1, "具身智能体任务编排层,一个可评测的参考实现",
        "An embodied-agent orchestration layer, built to be measurable."),
    (1, "大模型只做高层意图,确定性内核负责安全与恢复",
        "The LLM handles intent only; a deterministic core owns safety and recovery."),
    (1, "接下来四分钟,看它如何被验证",
        "In the next four minutes, watch how it is verified."),
    (2, "自然语言先经过意图解析,本地大模型可选,规则兜底",
        "Natural language goes through intent parsing: local LLM optional, rules as fallback."),
    (2, "六节点状态图驱动任务:规划、执行、观察、异常、重规划、报告",
        "A six-node graph runs the task: plan, execute, observe, classify, replan, report."),
    (2, "所有动作只能通过工具注册表:白名单、模式校验、熔断、审批令牌",
        "Every action passes the Tool Registry: whitelist, schemas, circuit breaker, approval tokens."),
    (2, "底盘之下,地面真值监视器独立记录每一次越界",
        "Below the base, a ground-truth monitor independently records every violation."),
    (3, "先看导航受阻:第十二拍,故障注入阻断了前方的边",
        "First, a blocked route: at tick 12, fault injection cuts the edge ahead."),
    (3, "底盘不会自己上报,只是原地停滞",
        "The base never reports 'blocked' — it just stalls."),
    (3, "编排层用停滞水位检测发现异常,先重试,再避障重规划",
        "The orchestrator detects stagnation, retries once, then replans around the edge."),
    (3, "绕行B区抵达目标,异常物体拍照上报,全程留痕",
        "It detours through zone B, reports the anomaly, and logs every decision."),
    (4, "同一个任务,在回放器里逐拍重放",
        "The same run, replayed tick by tick in the viewer."),
    (4, "红色虚线是被阻断的边,青色圆点是机器人",
        "The red dashed line is the blocked edge; the teal dot is the robot."),
    (4, "轨迹来自地面真值事件,不是智能体的自述",
        "The trajectory comes from ground-truth events, not the agent's own account."),
    (4, "右侧事件流,就是评测指标的唯一数据源",
        "The event feed on the right is the only data source for all metrics."),
    (5, "再换第一人称:同一次任务,仓库由同一张拓扑图程序化生成",
        "Now in first person — a warehouse generated from the same topology."),
    (5, "相机沿地面真值轨迹运动,与事件日志逐拍对齐",
        "The camera follows the ground-truth trajectory, tick-aligned with the log."),
    (5, "前方箱堆,就是那条被注入的受阻边:停滞、倒车、绕行",
        "The crate stack ahead is the injected blocked edge: stall, back off, detour."),
    (5, "感知时刻,视觉模型的结构化观测叠加在画面上:标签加置信度,绝不返回动作",
        "At perceive, the VLM overlay shows label and confidence — structured observation, never actions."),
    (6, "低电量场景:巡检途中电量跌破百分之二十红线",
        "Low battery: mid-patrol, charge drops below the 20 percent red line."),
    (6, "水位检测在飞行中抢占,任务队列先快照",
        "The watchdog preempts mid-flight; the task queue is snapshotted first."),
    (6, "回坞充电,然后从断点继续,原队列一步不丢",
        "The robot docks, recharges, and resumes the original queue — nothing lost."),
    (6, "红线是静态配置,大模型只能收紧,不能放宽",
        "The red line is static config; the LLM may tighten it, never loosen it."),
    (7, "进入受限区,必须人工审批",
        "Entering a restricted zone requires human approval."),
    (7, "批准会签发一次性令牌:限范围、限时效、用完即废",
        "Approval mints a single-use token — scoped, expiring, spent on use."),
    (7, "没有令牌,注册表直接拦截;禁入区连令牌都不认",
        "Without it, the registry blocks the call; forbidden zones accept no token at all."),
    (8, "安全声明需要证据:我们让一个恶意规划器攻击门禁",
        "Safety claims need evidence — so a malicious planner attacks the gates."),
    (8, "门禁开启:六次越权尝试,六次全部拦截,零违规",
        "Gates on: six rogue attempts, six interceptions, zero violations."),
    (8, "消融实验把门禁关掉:每次运行恰好五次真实违规",
        "The ablation turns gates off: exactly five real violations per run."),
    (8, "红圈闪烁的,就是地面真值记下的每一次越界",
        "Each flashing red ring is a violation recorded by the ground truth."),
    (9, "评测先注册预测,再跑结果,顺序由提交历史作证",
        "Predictions are committed before results — git history is the timestamp."),
    (9, "九个条件乘以十个种子,九十次运行,三十一条预测全部命中",
        "Nine conditions by ten seeds: ninety runs, all thirty-one predictions hit."),
    (9, "复合故障有三次电量耗尽,原样报告,附事件日志",
        "The compound fault killed three runs — reported as-is, logs attached."),
    (9, "代码复审证实三十五个缺陷,修复后按政策重跑,结论不变",
        "A code review confirmed 35 defects; after fixes, the rerun upheld every prediction."),
    (10, "这是仿真演示,没有实机,边界写在文档第一行",
        "This is a simulation demo, no real robot — stated in line one of the docs."),
    (10, "换上真实底盘,只需替换一个适配器,契约已经写好",
        "Swapping in a real base means one adapter — the contract is already written."),
    (10, "可复现、可回放、可证伪:这就是这份演示想说的",
        "Reproducible, replayable, falsifiable — that is the whole point."),
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
<ul><li>LLM 只做高层意图 · <b>确定性内核</b>负责安全与恢复</li>
<li>预注册故障注入 × 10 seed × 指标表,<b>未恢复 case 原样报</b></li>
<li>mock 底座(仿真,无实机)· adapter 契约可换真实 Nav2</li></ul>
<div class="foot">run_demo.py · run_eval.py · replay viewer · 31/31 pre-registered predictions</div>""",
    "arch": """<h1>架构:一条唯一通道</h1>
<div class="flow">自然语言 → Intent 解析(<b>LM Studio 本地</b> → 云 → 规则兜底)<br>
&nbsp;&nbsp;→ LangGraph 六节点:planner → executor ⇄ observer → exception_mgr → replanner → reporter<br>
&nbsp;&nbsp;→ <b>Tool Registry</b>:白名单 · schema(extra=forbid) · 幂等重试 · 熔断 · 审批 token · 电量闸<br>
&nbsp;&nbsp;→ RobotAdapter(异步 goal-handle,mock ⇄ rclpy)→ MockNavServer<br>
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;└ <span class="bad">地面真值 SafetyMonitor</span>(注册表之下,违规不自评)<br>
旁路:append-only Event Log —— 每个决策可回放,指标只读日志</div>""",
    "results": """<h1>评测:预测先于结果</h1>
<table><tr><th>条件 × 10 seed</th><th>结果(x/N)</th><th>预注册命中</th></tr>
<tr><td>baseline / blocked / battery</td><td>completed_full 10/10 ×3</td><td>✓</td></tr>
<tr><td>unreachable / sensor / tool</td><td>degraded_complete 10/10 ×3</td><td>✓</td></tr>
<tr><td class="warn">compound(受阻+低电量)</td><td class="warn">6 完成 · 1 安全弃 · <span class="bad">3 电量耗尽(原样报)</span></td><td>✓</td></tr>
<tr><td>adversarial(门禁开)</td><td>6/6 拦截 · 0 违规</td><td>✓</td></tr>
<tr><td class="bad">ablation(门禁关)</td><td class="bad">恰好 5 违规/run(地面真值)</td><td>✓</td></tr></table>
<ul><li>git 历史作证:prereg.yaml 先 commit,结果后跑分;seed 固定,禁止挑种子</li>
<li>多 agent 复审证实 35 缺陷 → 修复 → 按政策重跑,31/31 仍全部命中</li></ul>""",
    "outro": """<h1>可复现 · 可回放 · 可证伪</h1>
<ul><li>同 seed 事件流<b>逐字节一致</b>(回归测试锁定)</li>
<li>违规由<b>地面真值监视器</b>记账,完成率不读 agent 自报</li>
<li>诚实边界:仿真 demo,无实机;mock-only 故障注入如实标注</li>
<li>Phase B:换一个 adapter 接真实 Nav2 —— 契约与止损线已预先写死</li></ul>
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
