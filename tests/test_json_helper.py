import pytest
from src.utils.json_helper import extract_json_from_response

@pytest.mark.parametrize("input_text, expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"b": 2}\n```', {"b": 2}),
    ('Here is the json: {"c": 3}', {"c": 3}),
    ('} {"d": 4}', {"d": 4}),
    ('} { "e": 5 } {', {"e": 5}),
    ('{"f": 6} }', {"f": 6}),
    ('```{"g": 7}```', {"g": 7}),
    ('} } { "h": 8 } }', {"h": 8}),
    ('Some text } \n {"i": {"j": 9}}', {"i": {"j": 9}}),
    ('{"k": 10', None),
    ('', None),
    (None, None),
])
def test_extract_json_from_response(input_text, expected):
    """
    Test extraction of JSON from string with various edge cases,
    such as markdown formatting and unbalanced braces around the JSON string.
    """
    assert extract_json_from_response(input_text) == expected
