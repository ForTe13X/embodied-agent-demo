"""意图层:自然语言 → 结构化 Intent。

确定性边界(评审 B2):评测 harness 不调用 LLM——场景意图是 fixture,直接给结构化 Intent;
规则解析器用于离线 demo;LLM 解析器(可选,见 llm_intent.py)只在 live demo 用。
安全约束来源规则:电量红线/受限区/白名单是静态配置,Intent 里的约束只能收紧、不能放宽。
"""
from __future__ import annotations

import re

from pydantic import BaseModel

from .safety import BATTERY_FLOOR_PCT
from .world import TopoMap


class Intent(BaseModel):
    mission: str
    patrol_nodes: list[str]
    perceive_at_each: bool = True
    report_anomalies: bool = True
    battery_floor_pct: float = BATTERY_FLOOR_PCT   # 只能 >= 静态红线
    resume_battery_pct: float = 80.0

    def model_post_init(self, __context) -> None:
        # 单调收紧:LLM/规则解析给的红线低于静态配置时,以静态配置为准
        if self.battery_floor_pct < BATTERY_FLOOR_PCT:
            object.__setattr__(self, "battery_floor_pct", BATTERY_FLOOR_PCT)


DEFAULT_MISSION = "去 A 区巡检(a1→a2→a3),发现异常物体拍照上报;电量低于 20% 先回充。"


def default_intent() -> Intent:
    """评测 fixture(预注册,不经任何解析器)。"""
    return Intent(mission=DEFAULT_MISSION, patrol_nodes=["a1", "a2", "a3"])


def rule_parse(text: str, topo: TopoMap) -> Intent:
    """确定性关键词解析:离线 demo 用,能处理本 demo 场景句式;解析不出时退回默认巡检。"""
    explicit = re.findall(r"\b([a-z]\d(?:_alt)?)\b", text.lower())
    patrol = [n for n in explicit if topo.has(n) and topo.access(n) == "free"]
    if not patrol and ("巡检" in text or "patrol" in text.lower()):
        patrol = [n for n in ("a1", "a2", "a3") if topo.has(n)]
    if not patrol:
        patrol = ["a1", "a2", "a3"]
    report = ("上报" in text) or ("拍照" in text) or ("report" in text.lower())
    floor = BATTERY_FLOOR_PCT
    m = re.search(r"(\d{1,2})\s*%", text)
    if m:
        floor = max(BATTERY_FLOOR_PCT, float(m.group(1)))  # 只能收紧
    return Intent(mission=text, patrol_nodes=patrol,
                  report_anomalies=report or True, battery_floor_pct=floor)
