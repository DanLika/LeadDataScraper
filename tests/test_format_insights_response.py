import pytest
from backend.main import _format_insights_response

def test_format_insights_response_empty():
    assert _format_insights_response({}) is None
    assert _format_insights_response({"insights": [], "top_priorities": []}) is None
    assert _format_insights_response({"summary": "This is a summary but no insights or priorities."}) is None

def test_format_insights_response_only_summary_and_insights():
    result = {
        "summary": "Overall summary.",
        "insights": ["Insight 1", "Insight 2"]
    }
    expected = "Overall summary.\n\n1. Insight 1\n\n2. Insight 2"
    assert _format_insights_response(result) == expected

def test_format_insights_response_only_priorities_dict():
    result = {
        "top_priorities": [
            {"name": "Fix bugs", "reason": "High priority"},
            {"name": "Add features"}  # Missing reason
        ]
    }
    expected = "Top priorities:\n\n- Fix bugs: High priority\n\n- Add features"
    assert _format_insights_response(result) == expected

def test_format_insights_response_priorities_string():
    result = {
        "top_priorities": [
            "Just a string priority",
            {"name": "Dict priority"}
        ]
    }
    expected = "Top priorities:\n\n- Just a string priority\n\n- Dict priority"
    assert _format_insights_response(result) == expected

def test_format_insights_response_priorities_limit():
    result = {
        "top_priorities": [
            {"name": "P1"},
            {"name": "P2"},
            {"name": "P3"},
            {"name": "P4"},
            {"name": "P5"},
            {"name": "P6"},  # Should be ignored
        ]
    }
    expected = "Top priorities:\n\n- P1\n\n- P2\n\n- P3\n\n- P4\n\n- P5"
    assert _format_insights_response(result) == expected

def test_format_insights_response_all_fields():
    result = {
        "summary": "Complete review.",
        "insights": ["Good architecture."],
        "top_priorities": [{"name": "Refactoring", "reason": "Technical debt"}]
    }
    expected = "Complete review.\n\n1. Good architecture.\n\nTop priorities:\n\n- Refactoring: Technical debt"
    assert _format_insights_response(result) == expected
