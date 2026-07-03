"""真 VLM 实跑:把 POV 渲染的干净帧(无叠加)喂给本地 LM Studio 视觉模型,
拿真实结构化观测,与评测里的 perceive mock 输出对照。

用法:.venv\\Scripts\\python scripts\\vlm_annotate.py <clean_frame.png> [--model qwen/qwen3-vl-4b]
输出:stdout JSON + docs/screenshots/vlm_live_annotated.png(把真实结果画回帧上)

诚实边界:这是"本地 VLM 对渲染帧的真实推理";评测环路仍然 0 次 LLM/VLM 调用
(确定性叙事不变),本脚本属于 live-demo 层。
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import urllib.request
from pathlib import Path

BASE = "http://localhost:1234/v1"
PROMPT = (
    "这是仓库巡检机器人的第一人称画面(仿真渲染)。地面上的蓝色椭圆盘和金色直线是"
    "导航标记,不算物体;高大的方柜是货架。请列出**放置在通道地面上的实体物品**"
    "(如箱子、散落的小物件),巡检规则规定通道地面不允许摆放任何物品,逐个判断是否"
    "违规占道。只输出 JSON,不要解释:"
    '{"objects":[{"label":"<英文snake_case>","confidence":<0到1>,'
    '"blocking_aisle":<true/false>,"reason":"<一句中文>"}]}'
)


def annotate(image_path: Path, model: str) -> dict:
    b64 = base64.standard_b64encode(image_path.read_bytes()).decode()
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": PROMPT},
            ],
        }],
        "temperature": 0,
        "max_tokens": 500,
    }
    req = urllib.request.Request(
        f"{BASE}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=180) as resp:  # 首次 JIT 加载模型较慢
        body = json.loads(resp.read().decode("utf-8"))
    content = body["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", content, re.DOTALL)
    return json.loads(m.group(0)) if m else {"objects": [], "raw": content}


def draw_result(image_path: Path, result: dict, out_path: Path, model: str) -> None:
    from PIL import Image, ImageDraw, ImageFont
    im = Image.open(image_path).convert("RGB")
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("msyh.ttc", 22)
        small = ImageFont.truetype("msyh.ttc", 17)
    except OSError:
        font = small = ImageFont.load_default()
    lines = [f"LIVE VLM: {model} @ LM Studio (localhost)"]
    for o in result.get("objects", []):
        lines.append(f"- {o.get('label')}  conf={o.get('confidence')}")
        if o.get("reason"):
            lines.append(f"   {o['reason']}")
    y = 640 - len(lines) * 26
    d.rectangle([12, y - 10, 720, 700], fill=(8, 12, 16))
    for ln in lines:
        d.text((24, y), ln, fill=(80, 255, 160),
               font=font if ln.startswith(("LIVE", "-")) else small)
        y += 26
    im.save(out_path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--model", default="qwen/qwen3-vl-4b")
    parser.add_argument("--out", type=Path,
                        default=Path("docs/screenshots/vlm_live_annotated.png"))
    args = parser.parse_args()
    try:
        result = annotate(args.image, args.model)
    except Exception as e:
        print(json.dumps({"error": f"VLM 不可用,优雅跳过: {e}"},
                         ensure_ascii=False))
        sys.exit(0)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    try:
        draw_result(args.image, result, args.out, args.model)
        print(f"annotated -> {args.out}")
    except ImportError:
        print("(未装 pillow,跳过画图;JSON 结果如上)")


if __name__ == "__main__":
    main()
