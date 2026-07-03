"""append-only 事件日志(评审 minor:关联 ID + 可回放 schema)。

每条事件:{seq, tick, run_id, condition, seed, actor, event_type, payload}。
不写墙钟时间,保证同 seed 事件流逐字节可复现(含跨平台:换行固定 LF);
指标由独立脚本只读日志计算,不读 agent 内存。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .clock import SimClock


class EventLog:
    def __init__(
        self,
        clock: SimClock,
        run_id: str,
        condition: str,
        seed: int,
        path: Optional[Path] = None,
    ):
        self.clock = clock
        self.run_id = run_id
        self.condition = condition
        self.seed = seed
        self.events: list[dict] = []
        self._seq = 0
        self._fh = None
        self.on_emit = None  # demo 叙述钩子:callable(event) -> None,不影响日志内容
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            # GBK 陷阱:显式 utf-8(评审 M10);newline 固定 LF,跨平台字节一致
            self._fh = open(path, "w", encoding="utf-8", newline="\n")

    def emit(self, actor: str, event_type: str, **payload) -> dict:
        self._seq += 1
        event = {
            "seq": self._seq,
            "tick": self.clock.tick,
            "run_id": self.run_id,
            "condition": self.condition,
            "seed": self.seed,
            "actor": actor,
            "event_type": event_type,
            "payload": payload,
        }
        self.events.append(event)
        if self._fh:
            self._fh.write(json.dumps(event, ensure_ascii=False) + "\n")
            self._fh.flush()
        if self.on_emit:
            self.on_emit(event)
        return event

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None
