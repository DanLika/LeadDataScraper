"""Real-behavior tests for `extract_json_from_response` — the helper that
pulls a JSON object out of a raw LLM reply (markdown fences, surrounding
prose, greedy-brace edge cases). Every assertion checks an actual
parse outcome, not a coverage line.
"""

from src.utils.json_helper import extract_json_from_response


def test_none_and_empty_return_none():
    assert extract_json_from_response(None) is None
    assert extract_json_from_response("") is None


def test_plain_json_object_parses():
    assert extract_json_from_response('{"task": "STATUS_CHECK"}') == {
        "task": "STATUS_CHECK"
    }


def test_json_inside_markdown_json_fence():
    text = '```json\n{"a": 1, "b": 2}\n```'
    assert extract_json_from_response(text) == {"a": 1, "b": 2}


def test_json_inside_bare_triple_backtick_fence():
    text = '```\n{"x": true}\n```'
    assert extract_json_from_response(text) == {"x": True}


def test_json_embedded_in_prose_extracted_by_brace_balance():
    text = 'Sure, here is the plan: {"task": "DISCOVERY_SEARCH", "n": 3} — let me know.'
    assert extract_json_from_response(text) == {"task": "DISCOVERY_SEARCH", "n": 3}


def test_nested_braces_balanced_correctly():
    text = 'noise {"outer": {"inner": {"deep": 9}}, "k": "v"} trailing'
    assert extract_json_from_response(text) == {
        "outer": {"inner": {"deep": 9}},
        "k": "v",
    }


def test_first_valid_object_wins_when_multiple_present():
    text = '{"first": 1} and then {"second": 2}'
    out = extract_json_from_response(text)
    assert out == {"first": 1}


def test_malformed_json_returns_none():
    assert extract_json_from_response("{not valid json at all") is None
    assert extract_json_from_response("just prose, no braces") is None


def test_unbalanced_then_balanced_recovers():
    # A stray `}` then a real object — the scanner must not abort.
    text = 'oops } then the real one {"ok": 1}'
    assert extract_json_from_response(text) == {"ok": 1}


def test_broken_object_skipped_then_good_object_found():
    # First brace group is invalid JSON (trailing comma is fine in... no,
    # Python json rejects it) → scanner resets `start` and finds the next.
    text = '{"bad": ,} {"good": 42}'
    assert extract_json_from_response(text) == {"good": 42}
