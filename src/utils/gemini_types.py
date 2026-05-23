"""TypedDicts + narrowing helpers for the Gemini boundary.

Every Gemini call site in this codebase used to face mypy with three
unrelated Optional-union sources that all surfaced as ``union-attr``:

1. ``extract_json_from_response()`` returns untyped ``dict`` — every
   downstream ``.get("field")`` lookup was typed as ``JSON`` (the
   ``int | float | str | bool | None | Sequence | Mapping`` union from
   PostgREST), so even reading a known-string field flagged
   ``Item "int" of ... has no attribute "strip"``.
2. Gemini SDK's response shapes are deeply optional —
   ``response.text`` is ``str | None``; ``response.candidates[0].content``
   is ``Content | None``; ``content.parts[i].function_call`` is
   ``FunctionCall | None``; ``function_call.args`` is
   ``Mapping[str, JSON] | None``. Each call site re-implemented the
   None-chain (and most got one level wrong, falling back to
   ``# type: ignore``).
3. Per-task plan params (``/execute`` handlers) were typed ``dict`` —
   every ``params.get(...)`` returned ``Any``, but downstream code
   re-narrowed by passing the value to a typed Supabase query, which
   re-introduced the ``JSON`` union.

The fix is one-shot at the boundary:

- Per-response TypedDicts for the 5 sites that JSON-decode a Gemini
  text reply (``AIMapperFieldMap``, ``OutreachHooksResponse``,
  ``EnrichmentDetailsResponse``, ``DeepEnrichmentFieldsResponse``,
  ``StrategicInsightsResponse``).
- Per-task param TypedDicts for ``/execute`` handlers
  (``UniqueKeyParams``, ``DiscoverySearchParams``,
  ``DatabaseQueryParams``, ``FilteredParams``). ``total=False`` because
  the Gemini tool-call may omit optional args.
- ``typed_loads()`` — generic helper that runs
  ``extract_json_from_response``, then ``cast()``s the result to the
  caller-supplied TypedDict. Runtime is unchanged.
- ``response_text()`` — collapses
  ``GenerateContentResponse.text: str | None`` to ``str`` via
  ``(... or "").strip()``.
- ``extract_function_call()`` — narrows the
  ``response.candidates[0].content.parts[i].function_call`` chain in
  one place. Returns a ``FunctionCallResult`` TypedDict or ``None``.

Nothing here changes runtime behaviour — the helpers are
type-narrowing layers. Tests live in ``tests/unit/test_gemini_types.py``.
"""
from __future__ import annotations

from typing import (
    TYPE_CHECKING,
    Any,
    Mapping,
    TypeVar,
    cast,
)

from typing_extensions import NotRequired, TypedDict

from src.utils.json_helper import extract_json_from_response

if TYPE_CHECKING:
    from google.genai.types import GenerateContentResponse


# ---------------------------------------------------------------------------
# Per-response TypedDicts (one per JSON-emitting Gemini call site)
# ---------------------------------------------------------------------------


class OutreachHooksResponse(TypedDict, total=False):
    """``LeadHunter.generate_outreach_hooks_async`` JSON reply."""

    linkedin_hook: str
    email_hook: str


class EnrichmentDetailsResponse(TypedDict, total=False):
    """``LeadHunter.enrich_business_data_async`` JSON reply.

    All four fields are NotRequired because the model may return
    "Unknown" or omit the key entirely; the caller bounds + defaults.
    """

    company_size: str
    leadership_team: str
    business_details: str
    target_clients: str


class DeepEnrichmentFieldsResponse(TypedDict, total=False):
    """``EnrichmentEngine.deep_ai_parse`` JSON reply.

    8 fields; model returns null for missing values which deserialises
    to Python ``None``. Callers must None-check before string ops.
    """

    company_name: NotRequired[str | None]
    company_size: NotRequired[str | None]
    leadership_team: NotRequired[str | None]
    key_offerings: NotRequired[str | None]
    contact_details: NotRequired[str | None]
    business_details: NotRequired[str | None]
    target_clients: NotRequired[str | None]
    pain_points: NotRequired[str | None]


class StrategicInsightsPriority(TypedDict, total=False):
    """One entry in ``StrategicInsightsResponse.top_priorities``."""

    name: str
    reason: str


class StrategicInsightsResponse(TypedDict, total=False):
    """``AgenticRouter._get_strategic_insights`` JSON reply."""

    summary: str
    insights: list[str]
    top_priorities: list[StrategicInsightsPriority]


# AIMapperFieldMap is a free-shape ``{str: str}`` — the column-name set
# is operator-defined (``standard_columns``), so a TypedDict can't
# enumerate the keys. Type alias is precise enough.
AIMapperFieldMap = Mapping[str, str]


# ---------------------------------------------------------------------------
# Per-task param TypedDicts (one per ``/execute`` task)
# ---------------------------------------------------------------------------


class UniqueKeyParams(TypedDict, total=False):
    """Params for tasks scoped to one lead by ``unique_key``.

    Covers SEO_AUDIT, OUTREACH_DRAFT, LINKEDIN_DRAFT, DEEP_HUNT,
    DEEP_ENRICHMENT. ``lead_data`` lets callers pre-pass the row so the
    handler skips the DB fetch (used by CAMPAIGN_STRATEGY's per-lead
    OUTREACH_DRAFT to avoid N+1 queries).
    """

    unique_key: str
    lead_data: Mapping[str, Any]


class DiscoverySearchParams(TypedDict, total=False):
    """Params for DISCOVERY_SEARCH."""

    query: str
    location: str


class DatabaseQueryParams(TypedDict, total=False):
    """Params for DATABASE_QUERY."""

    query_text: str


class FilteredParams(TypedDict, total=False):
    """Params for tasks that take a string ``filters`` selector.

    Covers RUN_MASSIVE_PIPELINE, CAMPAIGN_STRATEGY. ``type`` is a
    legacy alias accepted by ``_execute_massive_pipeline`` for backwards
    compatibility with older orchestration plans.
    """

    filters: str
    type: str


# ---------------------------------------------------------------------------
# Gemini-response narrowing helpers
# ---------------------------------------------------------------------------


def response_text(response: GenerateContentResponse | None) -> str:
    """Collapse ``response.text: str | None`` to a stripped ``str``.

    Replaces every ``(response.text or "").strip()`` /
    ``response.text.strip()`` pattern. Returns ``""`` on missing
    response or missing text.
    """
    if response is None:
        return ""
    text = response.text
    if text is None:
        return ""
    return text.strip()


class FunctionCallResult(TypedDict):
    """Narrowed result of ``extract_function_call``.

    ``args`` is ``Mapping[str, JSON]`` (the Gemini SDK's declared type)
    — callers downcast to a per-task TypedDict (``UniqueKeyParams`` etc.)
    via ``cast()`` when they know which tool was selected.
    """

    name: str
    args: Mapping[str, Any]


def extract_function_call(
    response: GenerateContentResponse | None,
) -> FunctionCallResult | None:
    """Walk the Gemini tool-calling response; return first function_call.

    Returns ``None`` when: response is None / no candidates / first
    candidate has no content / no parts / no part carries a function_call
    / function_call has no name.

    Centralises the 5-deep None-chain that previously sat inline at
    every call site (~5 ``union-attr`` errors).
    """
    if response is None:
        return None
    candidates = response.candidates
    if not candidates:
        return None
    content = candidates[0].content
    if content is None:
        return None
    parts = content.parts
    if not parts:
        return None
    for part in parts:
        function_call = part.function_call
        if function_call is None:
            continue
        name = function_call.name
        if not name:
            continue
        args = function_call.args or {}
        return {"name": name, "args": args}
    return None


# ---------------------------------------------------------------------------
# Generic typed-JSON loader
# ---------------------------------------------------------------------------

_T = TypeVar("_T")


def typed_loads(text: str | None, schema: type[_T]) -> _T | None:
    """Parse a Gemini JSON reply and ``cast`` to the caller's TypedDict.

    ``schema`` is mypy-only — at runtime it's discarded. The parsed
    dict is returned as-is (no shape coercion), matching the existing
    ``extract_json_from_response`` contract.

    Usage::

        result = typed_loads(response.text, OutreachHooksResponse)
        if result is None:
            return {"linkedin_hook": "", "email_hook": ""}
        linkedin = result.get("linkedin_hook", "")  # mypy: str

    Caller is responsible for None-checking optional fields (TypedDicts
    don't validate at runtime).
    """
    if not text:
        return None
    parsed = extract_json_from_response(text)
    if parsed is None:
        return None
    return cast(_T, parsed)


__all__ = [
    "AIMapperFieldMap",
    "DatabaseQueryParams",
    "DeepEnrichmentFieldsResponse",
    "DiscoverySearchParams",
    "EnrichmentDetailsResponse",
    "FilteredParams",
    "FunctionCallResult",
    "OutreachHooksResponse",
    "StrategicInsightsPriority",
    "StrategicInsightsResponse",
    "UniqueKeyParams",
    "extract_function_call",
    "response_text",
    "typed_loads",
]
