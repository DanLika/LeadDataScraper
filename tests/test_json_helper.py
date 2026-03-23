import pytest
from src.utils.json_helper import extract_json_from_response

@pytest.mark.parametrize("input_text,expected", [
    ('{"key": "value"}', {"key": "value"}),
    ('   {"key": "value"}   ', {"key": "value"}),
    ('```json\n{"key": "value"}\n```', {"key": "value"}),
    ('```\n{"key": "value"}\n```', {"key": "value"}),
    ('Some prefix text {"key": "value"} some suffix text', {"key": "value"}),
    ('{"key": {"nested": "value"}}', {"key": {"nested": "value"}}),
    ('{"key": "value"', None),
    ('{"key": {"nested": "value"}', None),
    ('{"key": "value"}}', {"key": "value"}),
    ('{"key": "value"} {', {"key": "value"}),
    ('', None),
    (None, None),
    ('No JSON here', None),
    ('{"key": "value", }', None),
    ('} {"key": "value"}', {"key": "value"}),
    ('} } {"a": 1} {', {"a": 1}),
])
def test_extract_json_from_response(input_text, expected):
    assert extract_json_from_response(input_text) == expected
