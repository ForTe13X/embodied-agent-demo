"""可选 LLM 意图解析(live demo 层,不在评测环路内 —— 见 EVAL_PREREG.md 确定性边界)。

Provider 链(local-first,LLM 永不承重):
  1. lmstudio  本地 LM Studio(http://localhost:1234/v1,OpenAI 兼容,免费/离线/无 key)
  2. anthropic 云端 Claude(需 pip install anthropic + ANTHROPIC_API_KEY)
  3. rule      确定性规则解析(永远可用,评测唯一允许的路径)

任何一级失败(服务未启动 / 超时 / 输出不合 schema / 拒答)→ 顺位降级,demo 不因
模型或 Wi-Fi 挂掉。LLM 只产出结构化意图;安全约束(电量红线/受限区/白名单)是静态
配置,Intent.model_post_init 强制"只能收紧不能放宽",且节点白名单式后校验(不信任,验证)。
"""
from __future__ import annotations

import json
import math
import os
import re
import urllib.error
import urllib.request

from .intent import Intent, rule_parse
from .world import TopoMap

LMSTUDIO_BASE = os.environ.get("LMSTUDIO_BASE", "http://localhost:1234/v1")
LMSTUDIO_MODEL = os.environ.get("LMSTUDIO_MODEL", "qwen2.5-3b")
LMSTUDIO_TIMEOUT_S = 20  # 本地小模型 CPU 推理偏慢,给足但有限(gotcha:it will be slow)
ANTHROPIC_MODEL = "claude-opus-4-8"

_SYSTEM = (
    "你是巡检机器人任务编排层的意图解析器。把用户的自然语言任务解析成 JSON,"
    "只输出 JSON 本体,不要 markdown 代码块,不要解释。字段:"
    '{"patrol_nodes": [节点id数组], "report_anomalies": bool, "battery_floor_pct": number}。'
    "patrol_nodes 只能从给出的 free 节点里选;battery_floor_pct 不得低于 20"
    "(静态安全红线,只能收紧)。"
)


def _free_nodes(topo: TopoMap) -> list[str]:
    return [n.id for n in topo.nodes.values() if n.access == "free" and n.id != "dock"]


def _coerce_bool(v, default: bool) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):                       # "false"/"0"/"no" 都不能被 bool() 当成 True
        return v.strip().lower() not in ("false", "0", "no", "off", "")
    return default


def _coerce_float(v, default: float) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError, OverflowError):  # None/"not-a-number"/超大 JSON 整数 → 退回默认,不崩
        # OverflowError:json.loads 把超长整数字面量解析成 Python int,float() 溢出会抛它;
        # 漏接会穿透 _validate→parse_intent 崩掉 live demo,规则兜底反而没机会跑
        return default
    # 拒绝 NaN/inf:否则 "NaN" 会被当电量阈值,破坏"红线只能收紧"的不变量(codex 复核 PR#10)
    return f if math.isfinite(f) else default


def _validate(raw: dict, text: str, topo: TopoMap) -> Intent | None:
    """白名单式后校验:不信任模型输出,逐字段【类型防御】验证——合法 JSON 但字段类型不对
    (patrol_nodes=null、battery_floor_pct="x"、report_anomalies="false")不再崩,退回 None
    → 上层走规则兜底(codex 评审 F-13)。"""
    if not isinstance(raw, dict):
        return None
    pn = raw.get("patrol_nodes")
    if not isinstance(pn, list):                 # null / 非列表 → 视作空,交给下面的 not nodes 兜底
        pn = []
    nodes = [n for n in pn
             if isinstance(n, str) and topo.has(n) and topo.access(n) == "free"
             and n != "dock"]
    if not nodes:
        return None
    return Intent(
        mission=text, patrol_nodes=nodes,
        report_anomalies=_coerce_bool(raw.get("report_anomalies"), True),
        battery_floor_pct=_coerce_float(raw.get("battery_floor_pct"), 20.0),
    )


def _extract_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)  # 容忍模型包一层废话/代码块
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


# ---- provider: LM Studio(OpenAI 兼容,stdlib 实现,零新依赖) -----------------

def lmstudio_parse(text: str, topo: TopoMap) -> Intent | None:
    payload = {
        "model": LMSTUDIO_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user",
             "content": f"可用 free 节点:{_free_nodes(topo)}\n任务:{text}"},
        ],
        "temperature": 0,
        "max_tokens": 300,
    }
    req = urllib.request.Request(
        f"{LMSTUDIO_BASE}/chat/completions",
        # gotcha:非 ASCII JSON 必须显式 UTF-8 编码 + charset 头
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=LMSTUDIO_TIMEOUT_S) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
    except (urllib.error.URLError, OSError, KeyError, IndexError,
            json.JSONDecodeError, TimeoutError):
        return None
    raw = _extract_json(content)
    return _validate(raw, text, topo) if raw else None


def lmstudio_available() -> bool:
    try:
        with urllib.request.urlopen(f"{LMSTUDIO_BASE}/models", timeout=2):
            return True
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


# ---- provider: Anthropic(可选云端) -------------------------------------------

def anthropic_available() -> bool:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        return False


def anthropic_parse(text: str, topo: TopoMap) -> Intent | None:
    try:
        import anthropic

        client = anthropic.Anthropic()
        response = client.messages.parse(
            model=ANTHROPIC_MODEL,
            max_tokens=2048,
            system=_SYSTEM,
            messages=[{
                "role": "user",
                "content": f"可用 free 节点:{_free_nodes(topo)}\n任务:{text}",
            }],
            output_format=Intent,
        )
        intent = response.parsed_output
        return _validate(intent.model_dump(), text, topo)
    except Exception:
        return None


# ---- 对外入口:provider 链 ------------------------------------------------------

def parse_intent(text: str, topo: TopoMap) -> tuple[Intent, str]:
    """返回 (Intent, 来源)。来源 ∈ {'lmstudio', 'anthropic', 'rule_fallback'}。"""
    if lmstudio_available():
        intent = lmstudio_parse(text, topo)
        if intent is not None:
            return intent, "lmstudio"
    if anthropic_available():
        intent = anthropic_parse(text, topo)
        if intent is not None:
            return intent, "anthropic"
    return rule_parse(text, topo), "rule_fallback"
