"""记忆:短期记忆 = LangGraph 状态通道(见 graph.py 的 AgentState);
本模块只放"run 内长期记忆"(历史不可达区/受阻边/点位别名)。

评审 M-minor:评测 harness 每个 run 重新实例化(跨 run 不携带),
否则 run 之间互相污染,seed 不再独立;跨 run 记忆留作显式实验条件(未预注册,不做)。
写入方:exception manager(确认 unreachable / blocked 时);
读取方:planner 的候选枚举(降权/排除历史不可达节点)与 executor 的 avoid_edges。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RunMemory:
    blocked_edges: set = field(default_factory=set)       # EdgeKey
    unreachable_nodes: set = field(default_factory=set)   # node_id
    aliases: dict = field(default_factory=dict)           # 别名 -> node_id(demo 用)

    def avoid_edge_pairs(self) -> list[list[str]]:
        return sorted([list(e) for e in self.blocked_edges])
