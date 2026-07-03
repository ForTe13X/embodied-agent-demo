"""LLM provider 链:兜底与后校验(不依赖 LM Studio 在线,专测降级路径)。"""
from embodied_agent import llm_intent
from embodied_agent.world import default_map


def test_falls_back_to_rule_when_no_provider(monkeypatch):
    monkeypatch.setattr(llm_intent, "LMSTUDIO_BASE", "http://127.0.0.1:1")  # 死端口
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    intent, source = llm_intent.parse_intent("去 A 区巡检", default_map())
    assert source == "rule_fallback"
    assert intent.patrol_nodes == ["a1", "a2", "a3"]


def test_validate_rejects_untrusted_nodes():
    """模型给出的节点必须白名单式验证:图外/受限/dock 一律剔除。"""
    topo = default_map()
    raw = {"patrol_nodes": ["a1", "z9", "r1", "f1", "dock", 42],
           "battery_floor_pct": 5}  # 5 < 静态红线,应被抬回 20
    intent = llm_intent._validate(raw, "x", topo)
    assert intent.patrol_nodes == ["a1"]
    assert intent.battery_floor_pct >= 20


def test_validate_returns_none_when_nothing_valid():
    assert llm_intent._validate({"patrol_nodes": ["z9"]}, "x", default_map()) is None
