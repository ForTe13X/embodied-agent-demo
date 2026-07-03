"""HITL 闸:高危动作唯一通道 ask_human_confirmation 背后的应答策略。

评测模式用脚本化策略(评审 M5:每个评测条件预注册一张 模式→应答 表,无人值守可跑);
demo 模式用交互式控制台。超时语义统一:无应答 = deny + 安全停。
"""
from __future__ import annotations

import re
from typing import Protocol


class HITLPolicy(Protocol):
    def decide(self, message: str, scope: str) -> dict: ...


class ScriptedHITLPolicy:
    """rules: [(regex_pattern, 'approve'|'deny'|'timeout'), ...] 首条命中生效,无命中走 default。"""

    def __init__(self, rules: list[tuple[str, str]], default: str = "deny"):
        self.rules = [(re.compile(p), d) for p, d in rules]
        self.default = default

    def decide(self, message: str, scope: str) -> dict:
        for pattern, decision in self.rules:
            if pattern.search(message) or pattern.search(scope):
                return {"decision": decision, "source": "scripted"}
        return {"decision": self.default, "source": "scripted_default"}


class InteractiveHITLPolicy:
    def decide(self, message: str, scope: str) -> dict:
        print(f"\n[HITL] {message}")
        answer = input("  批准吗?(y/N): ").strip().lower()
        decision = "approve" if answer in ("y", "yes") else "deny"
        return {"decision": decision, "source": "console"}
