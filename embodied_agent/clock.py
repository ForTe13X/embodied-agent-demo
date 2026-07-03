"""虚拟时钟:所有超时/电量衰减/故障触发都以 tick 为单位定义。

tick_duration_s == 0 → 评测模式,瞬时推进(同 seed 产生逐字节一致的事件流);
tick_duration_s > 0  → demo 模式,每 tick 映射为真实 sleep,便于观看。
"""
from __future__ import annotations

import asyncio


class SimClock:
    def __init__(self, tick_duration_s: float = 0.0):
        self.tick = 0
        self.tick_duration_s = tick_duration_s

    async def advance(self, n: int = 1) -> int:
        for _ in range(n):
            self.tick += 1
            if self.tick_duration_s > 0:
                await asyncio.sleep(self.tick_duration_s)
        return self.tick
