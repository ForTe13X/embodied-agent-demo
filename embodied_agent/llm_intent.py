"""可选 LLM 意图解析(live demo 层,不在评测环路内 —— 见 EVAL_PREREG.md 确定性边界)。

- 依赖 `anthropic` SDK(可选安装:pip install anthropic)+ ANTHROPIC_API_KEY;
- 结构化输出:client.messages.parse + pydantic Intent,保证解析结果过 schema;
- LLM 只产出结构化意图,安全约束(电量红线/受限区/白名单)是静态配置,
  Intent.model_post_init 强制"只能收紧不能放宽";
- 任何失败(未装 SDK / 无 key / 网络 / 拒答)→ 回退确定性规则解析,demo 不因 Wi-Fi 挂掉。
"""
from __future__ import annotations

import os

from .intent import Intent, rule_parse
from .world import TopoMap

MODEL = "claude-opus-4-8"

_SYSTEM = (
    "你是巡检机器人任务编排层的意图解析器。把用户的自然语言任务解析成结构化 Intent。"
    "只输出结构化字段,不输出动作或计划。patrol_nodes 只能从拓扑图给出的 free 节点里选;"
    "battery_floor_pct 不得低于 20(那是静态安全红线,只能收紧)。"
)


def llm_available() -> bool:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        return False


def parse_intent(text: str, topo: TopoMap) -> tuple[Intent, str]:
    """返回 (Intent, 来源)。来源 ∈ {'llm', 'rule_fallback'}。"""
    if not llm_available():
        return rule_parse(text, topo), "rule_fallback"
    try:
        import anthropic

        client = anthropic.Anthropic()
        free_nodes = [n.id for n in topo.nodes.values() if n.access == "free"]
        response = client.messages.parse(
            model=MODEL,
            max_tokens=2048,
            system=_SYSTEM,
            messages=[{
                "role": "user",
                "content": f"可用 free 节点:{free_nodes}\n任务:{text}",
            }],
            output_format=Intent,
        )
        intent = response.parsed_output
        # 白名单式后校验:LLM 给出的节点必须真实存在且 free(不信任,验证)
        intent.patrol_nodes = [n for n in intent.patrol_nodes
                               if topo.has(n) and topo.access(n) == "free"]
        if not intent.patrol_nodes:
            return rule_parse(text, topo), "rule_fallback"
        return intent, "llm"
    except Exception:
        return rule_parse(text, topo), "rule_fallback"
