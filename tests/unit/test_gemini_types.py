"""Unit tests for `src/utils/gemini_types.py` narrowing helpers.

Covers the three runtime helpers (``response_text``,
``extract_function_call``, ``typed_loads``) and the JSON-loader
contract that ``typed_loads`` relies on. The TypedDicts themselves
have no runtime behaviour to test — they're cast-only at the type
layer.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.utils.gemini_types import (
    OutreachHooksResponse,
    extract_function_call,
    response_text,
    typed_loads,
)


# ---------------------------------------------------------------------------
# response_text
# ---------------------------------------------------------------------------


class TestResponseText:
    def test_none_response(self) -> None:
        assert response_text(None) == ""

    def test_none_text_attr(self) -> None:
        resp = MagicMock(text=None)
        assert response_text(resp) == ""

    def test_empty_string(self) -> None:
        resp = MagicMock(text="")
        assert response_text(resp) == ""

    def test_strips_whitespace(self) -> None:
        resp = MagicMock(text="  hello world  \n")
        assert response_text(resp) == "hello world"

    def test_unicode_preserved(self) -> None:
        # Bosnian / Croatian diacritics — locks in test_i18n parity
        resp = MagicMock(text="Đurić Žito Kovačević")
        assert response_text(resp) == "Đurić Žito Kovačević"


# ---------------------------------------------------------------------------
# extract_function_call
# ---------------------------------------------------------------------------


def _make_fcall(name: str | None, args: dict | None) -> SimpleNamespace:
    return SimpleNamespace(function_call=SimpleNamespace(name=name, args=args))


def _make_response(parts: list | None = None, content_none: bool = False,
                   no_candidates: bool = False) -> SimpleNamespace:
    if no_candidates:
        return SimpleNamespace(candidates=[])
    if content_none:
        return SimpleNamespace(candidates=[SimpleNamespace(content=None)])
    return SimpleNamespace(
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=parts))]
    )


class TestExtractFunctionCall:
    def test_none_response(self) -> None:
        assert extract_function_call(None) is None

    def test_no_candidates(self) -> None:
        assert extract_function_call(_make_response(no_candidates=True)) is None

    def test_content_none(self) -> None:
        assert extract_function_call(_make_response(content_none=True)) is None

    def test_parts_none(self) -> None:
        assert extract_function_call(_make_response(parts=None)) is None

    def test_parts_empty(self) -> None:
        assert extract_function_call(_make_response(parts=[])) is None

    def test_no_function_call_in_parts(self) -> None:
        # part exists but function_call is None
        part = SimpleNamespace(function_call=None)
        assert extract_function_call(_make_response(parts=[part])) is None

    def test_function_call_no_name(self) -> None:
        # function_call present but name empty — skip it
        part = _make_fcall(name=None, args={"foo": "bar"})
        assert extract_function_call(_make_response(parts=[part])) is None

    def test_function_call_empty_name(self) -> None:
        # `not name` covers both None and ""
        part = _make_fcall(name="", args={})
        assert extract_function_call(_make_response(parts=[part])) is None

    def test_happy_path(self) -> None:
        part = _make_fcall(name="seo_audit", args={"unique_key": "abc123"})
        result = extract_function_call(_make_response(parts=[part]))
        assert result is not None
        assert result["name"] == "seo_audit"
        assert dict(result["args"]) == {"unique_key": "abc123"}

    def test_null_args_defaults_to_empty(self) -> None:
        # function_call.args is None in the Gemini SDK when the tool
        # declaration has no params — coerce to {} so callers can dict-index.
        part = _make_fcall(name="status_check", args=None)
        result = extract_function_call(_make_response(parts=[part]))
        assert result is not None
        assert result["name"] == "status_check"
        assert dict(result["args"]) == {}

    def test_first_function_call_wins(self) -> None:
        # If multiple parts carry function_calls, the first one is returned.
        p1 = _make_fcall(name="seo_audit", args={"unique_key": "k1"})
        p2 = _make_fcall(name="outreach_draft", args={"unique_key": "k2"})
        result = extract_function_call(_make_response(parts=[p1, p2]))
        assert result is not None
        assert result["name"] == "seo_audit"

    def test_skips_nameless_then_takes_named(self) -> None:
        # First part is nameless → loop continues → second part wins.
        p1 = _make_fcall(name=None, args=None)
        p2 = _make_fcall(name="status_check", args={})
        result = extract_function_call(_make_response(parts=[p1, p2]))
        assert result is not None
        assert result["name"] == "status_check"


# ---------------------------------------------------------------------------
# typed_loads
# ---------------------------------------------------------------------------


class TestTypedLoads:
    def test_none_text(self) -> None:
        assert typed_loads(None, OutreachHooksResponse) is None

    def test_empty_text(self) -> None:
        assert typed_loads("", OutreachHooksResponse) is None

    def test_whitespace_only(self) -> None:
        # `extract_json_from_response` treats whitespace-only as no JSON.
        assert typed_loads("   \n  ", OutreachHooksResponse) is None

    def test_valid_json(self) -> None:
        result = typed_loads(
            '{"linkedin_hook": "hi", "email_hook": "hello"}',
            OutreachHooksResponse,
        )
        assert result is not None
        assert result["linkedin_hook"] == "hi"
        assert result["email_hook"] == "hello"

    def test_markdown_fenced_json(self) -> None:
        # Gemini commonly wraps in ```json ... ``` — the JSON helper
        # strips fences. Lock in that pass-through.
        text = '```json\n{"linkedin_hook": "x"}\n```'
        result = typed_loads(text, OutreachHooksResponse)
        assert result is not None
        assert result["linkedin_hook"] == "x"

    def test_invalid_json(self) -> None:
        assert typed_loads("not json at all", OutreachHooksResponse) is None

    def test_top_level_array_drops(self) -> None:
        # Caller asked for a dict-shaped TypedDict — non-dict JSON is
        # dropped at the json_helper layer so wrong shapes never reach
        # the cast.
        assert typed_loads("[1, 2, 3]", OutreachHooksResponse) is None

    def test_top_level_scalar_drops(self) -> None:
        assert typed_loads('"just a string"', OutreachHooksResponse) is None

    def test_typeddict_at_runtime_is_plain_dict(self) -> None:
        # TypedDicts are dicts at runtime — caller's `result["key"]`
        # works the same as on a plain dict. Lock in the contract so a
        # future change that wraps the result in a model class is caught.
        result = typed_loads('{"linkedin_hook": "hi"}', OutreachHooksResponse)
        assert result is not None
        assert isinstance(result, dict)
        assert "linkedin_hook" in result


# ---------------------------------------------------------------------------
# Smoke test: helpers compose
# ---------------------------------------------------------------------------


class TestComposition:
    def test_typed_loads_then_get(self) -> None:
        # Mirror the leadhunter.py call pattern:
        #   result = typed_loads(response_text(response), OutreachHooksResponse)
        #   if result: return result
        resp = MagicMock(text='{"linkedin_hook": "  hi  ", "email_hook": "hello"}')
        result = typed_loads(response_text(resp), OutreachHooksResponse)
        assert result == {"linkedin_hook": "  hi  ", "email_hook": "hello"}

    def test_none_response_short_circuits_loader(self) -> None:
        # response_text(None) -> "" -> typed_loads("", ...) -> None.
        # Composing the helpers is safe even when the upstream call failed.
        assert typed_loads(response_text(None), OutreachHooksResponse) is None
