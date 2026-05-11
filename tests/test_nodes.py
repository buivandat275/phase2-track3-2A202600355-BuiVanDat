from langgraph_agent_lab.nodes import classify_node
from langgraph_agent_lab.state import Route


def test_classify_extended_risky_keywords():
    result = classify_node({"query": "Cancel and remove this account"})
    assert result["route"] == Route.RISKY.value
    assert result["risk_level"] == "high"


def test_classify_missing_info_uses_words_not_substrings():
    vague = classify_node({"query": "Can you fix it?"})
    item_lookup = classify_node({"query": "Find item status"})

    assert vague["route"] == Route.MISSING_INFO.value
    assert item_lookup["route"] == Route.TOOL.value


def test_classify_error_keywords():
    result = classify_node({"query": "Service crash and unavailable"})
    assert result["route"] == Route.ERROR.value
