"""意图解析健壮性回归(codex 评审 F-10 / F-13)。

F-10:规则兜底解析器对紧邻中文的节点 id 要能识别;report_anomalies 表达真实、非恒真。
F-13:LLM 后校验对合法 JSON 的错误类型不崩,退回 None → 上层走规则兜底。
"""
from embodied_agent.intent import rule_parse
from embodied_agent.llm_intent import _validate
from embodied_agent.world import default_map

TOPO = default_map()


# ---- F-10:\b → ASCII lookaround,识别紧邻中文的节点 id ----

def test_rule_parse_nodes_adjacent_to_chinese():
    # "巡检a1和a3":a1/a3 紧邻中文,旧 \b 会失配退回默认全巡检
    intent = rule_parse("去A区巡检a1和a3", TOPO)
    assert intent.patrol_nodes == ["a1", "a3"]


def test_rule_parse_a3_alt_adjacent_to_chinese():
    intent = rule_parse("去a3_alt看看", TOPO)
    assert "a3_alt" in intent.patrol_nodes


def test_rule_parse_does_not_grab_partial_from_longer_token():
    # "abc123" 不应被误当成节点 a1;正常巡检句仍退回默认
    intent = rule_parse("系统 abc123 巡检", TOPO)
    assert intent.patrol_nodes == ["a1", "a2", "a3"]   # 无有效显式节点 → 巡检默认


def test_rule_parse_report_anomalies_true_when_patrol():
    # 巡检即上报:有巡检点 → report_anomalies True(替代原恒真 `or True`,语义等价但可证伪)
    assert rule_parse("去A区巡检a1和a3", TOPO).report_anomalies is True


# ---- F-13:类型防御,合法 JSON 错误类型不崩 ----

def test_validate_patrol_nodes_null_returns_none():
    assert _validate({"patrol_nodes": None}, "去巡检", TOPO) is None     # 曾 TypeError


def test_validate_patrol_nodes_not_list_returns_none():
    assert _validate({"patrol_nodes": "a1"}, "去巡检", TOPO) is None      # 字符串非列表


def test_validate_bad_battery_floor_falls_back():
    intent = _validate({"patrol_nodes": ["a1"], "battery_floor_pct": "not-a-number"}, "x", TOPO)
    assert intent is not None and intent.battery_floor_pct == 20.0        # 曾 ValueError


def test_validate_string_false_is_false():
    intent = _validate({"patrol_nodes": ["a1"], "report_anomalies": "false"}, "x", TOPO)
    assert intent is not None and intent.report_anomalies is False        # 曾被 bool() 当 True


def test_validate_raw_not_dict_returns_none():
    assert _validate([], "x", TOPO) is None
    assert _validate(None, "x", TOPO) is None


def test_validate_nan_battery_floor_rejected():
    # "NaN"/inf 不能当电量阈值(否则破坏"红线只能收紧"不变量;codex 复核 PR#10)
    for bad in ("NaN", "inf", float("nan"), float("inf")):
        intent = _validate({"patrol_nodes": ["a1"], "battery_floor_pct": bad}, "x", TOPO)
        assert intent is not None and intent.battery_floor_pct == 20.0


def test_validate_huge_int_battery_floor_falls_back():
    # 超大 JSON 整数字面量:json.loads 得 Python int,float() 抛 OverflowError——
    # 必须被兜底而非穿透崩溃(post-merge 审计:_coerce_float 漏接 OverflowError)
    intent = _validate({"patrol_nodes": ["a1"], "battery_floor_pct": 10 ** 400}, "x", TOPO)
    assert intent is not None and intent.battery_floor_pct == 20.0


def test_rule_parse_underscore_suffix_not_grabbed_as_node():
    # "a1_extra" 不应被当成节点 a1(下划线边界;codex 复核 PR#10)
    intent = rule_parse("去 a1_extra 巡检", TOPO)
    assert intent.patrol_nodes == ["a1", "a2", "a3"]   # 无有效显式节点 → 巡检默认
