import pytest
from src.utils.json_helper import extract_json_from_response

def test_extract_json_direct():
    text = '{"key": "value"}'
    assert extract_json_from_response(text) == {"key": "value"}

def test_extract_json_markdown():
    text = '```json\n{"key": "value"}\n```'
    assert extract_json_from_response(text) == {"key": "value"}

def test_extract_json_markdown_no_lang():
    text = '```\n{"key": "value"}\n```'
    assert extract_json_from_response(text) == {"key": "value"}

def test_extract_json_embedded():
    text = 'Here is the response you requested:\n{"key": "value"}\nHope this helps!'
    assert extract_json_from_response(text) == {"key": "value"}

def test_extract_json_empty_input():
    assert extract_json_from_response(None) is None
    assert extract_json_from_response("") is None

def test_extract_json_invalid_json():
    text = '{"key": "value", }' # Invalid JSON in Python's standard json parser
    assert extract_json_from_response(text) is None

def test_extract_json_unbalanced_braces():
    text = '{"key": "value"'
    assert extract_json_from_response(text) is None

def test_extract_json_deeply_nested():
    text = '{"level1": {"level2": {"level3": "value"}}}'
    assert extract_json_from_response(text) == {"level1": {"level2": {"level3": "value"}}}

def test_extract_json_surrounding_garbage():
    text = 'Garbage before {"valid": "json"} and garbage after.'
    assert extract_json_from_response(text) == {"valid": "json"}

def test_extract_json_multiple_json_blocks():
    # It should extract the first valid JSON it finds
    text = 'First: {"key1": "val1"}. Second: {"key2": "val2"}.'
    assert extract_json_from_response(text) == {"key1": "val1"}
