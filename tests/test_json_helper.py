import pytest
from src.utils.json_helper import extract_json_from_response

@pytest.mark.parametrize(
    "input_text, expected_output",
    [
        # Empty and None inputs
        ("", None),
        (None, None),

        # Clean JSON
        ('{"key": "value", "number": 42}', {"key": "value", "number": 42}),

        # Markdown fences
        ('```json\n{"key": "value"}\n```', {"key": "value"}),
        ('```\n{"key": "value"}\n```', {"key": "value"}),
        ('```json\n{"nested": {"inner": 1}}\n```', {"nested": {"inner": 1}}),

        # Extra text around JSON
        ('Here is the response:\n{"key": "value"}\nHope this helps!', {"key": "value"}),
        ('Leading text {"a": 1} Trailing text', {"a": 1}),

        # Nested JSON structure
        ('Some text\n{"outer": {"inner": "value"}}\nSome text', {"outer": {"inner": "value"}}),

        # Multiple JSON objects (should return the first one successfully parsed)
        ('First one: {"first": 1} Second one: {"second": 2}', {"first": 1}),

        # Invalid JSON (e.g., trailing comma)
        ('{"key": "value", }', None),

        # Invalid JSON inside markdown
        ('```json\n{"key": "value", }\n```', None),

        # Edge cases with extra braces that are valid inside strings
        ('{"key": "value { with } braces"}', {"key": "value { with } braces"}),
    ]
)
def test_extract_json_from_response(input_text, expected_output):
    assert extract_json_from_response(input_text) == expected_output
