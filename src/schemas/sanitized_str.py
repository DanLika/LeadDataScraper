"""``safe_constr`` — Pydantic-v2 constrained-string type with control-char
+ format-char rejection on top of the usual length/pattern/strip options.

Why this exists
---------------

``pydantic.constr(min_length=N, max_length=M)`` enforces length only.
A payload like ``{"query": "hi\\x00\\u202e"}`` passes validation, reaches
the handler, and then crashes downstream:

* PostgreSQL ``TEXT`` columns reject NUL byte INSERTs with SQLSTATE 22021.
* Playwright URL-encodes RTL-override / zero-width codepoints into URLs
  that error mid-request.
* Operator-facing UI silently renders bidi-override text right-to-left.

All three surface as ``HTTP 500`` ("Internal Server Error") at the HTTP
boundary — a noisy, unactionable, error-class-leaking response.

Pinned by ``tests/security/test_control_char_rejection.py``.
Original finding: QA terminal-6 sweep 2026-05-28
(``test-results/06-backend-api.md`` ids ``API-127`` + ``API-201``).

Usage
-----

Drop-in replacement for ``pydantic.constr(...)``::

    from src.schemas.sanitized_str import safe_constr

    class CampaignCreate(BaseModel):
        model_config = ConfigDict(extra="forbid")
        name: safe_constr(min_length=1, max_length=200)

Allowed control characters
--------------------------

Tab ``\\t``, line-feed ``\\n``, carriage-return ``\\r``. Multi-line
operator inputs (e.g. ``AskInstruction.text`` — a 4000-char Gemini
prompt) need these. Every other character whose ``unicodedata.category``
is ``Cc`` (other ASCII control) or ``Cf`` (Unicode format, including
zero-width-space U+200B, zero-width-joiner U+200D, RTL override U+202E,
BOM U+FEFF, etc.) raises ``ValueError`` and Pydantic returns ``422``.
"""

from __future__ import annotations

import unicodedata
from typing import Annotated, Optional

from pydantic import AfterValidator, StringConstraints
from pydantic_core import PydanticCustomError

_ALLOWED_CONTROL = frozenset({"\t", "\n", "\r"})


def _reject_control_or_format(value: str) -> str:
    """Reject the first disallowed char with a clear error message.

    Returns ``value`` unchanged on success. Raises ``PydanticCustomError``
    (a ``ValueError`` subclass) on failure, carrying the byte index, the
    codepoint (``U+xxxx``), and the Unicode category in the ctx — all
    JSON-serializable so the 422 handler can echo them. Raising a plain
    ``ValueError`` would land the exception object itself in ``ctx.error``
    and crash starlette's JSON encoder.
    """
    for index, ch in enumerate(value):
        if ch in _ALLOWED_CONTROL:
            continue
        category = unicodedata.category(ch)
        if category in ("Cc", "Cf"):
            codepoint = f"U+{ord(ch):04X}"
            raise PydanticCustomError(
                "control_or_format_char",
                "control or format character not allowed at index "
                "{index} ({codepoint}, category {category})",
                {"index": index, "codepoint": codepoint, "category": category},
            )
    return value


def safe_constr(
    *,
    min_length: Optional[int] = None,
    max_length: Optional[int] = None,
    strip_whitespace: bool = False,
    to_lower: bool = False,
    to_upper: bool = False,
    pattern: Optional[str] = None,
):
    """Return an ``Annotated[str, …]`` type that:

    1. Enforces the usual ``StringConstraints`` (length, regex, case
       transforms, whitespace strip).
    2. After length+pattern pass, scans the value and raises
       ``ValueError`` on any NUL / Cc / Cf char that is not in
       ``_ALLOWED_CONTROL``.

    Keyword-arg shape matches ``pydantic.constr`` for drop-in
    migration. Returned object is used as a type annotation — call
    once per field declaration::

        name: safe_constr(min_length=1, max_length=200)
    """
    return Annotated[
        str,
        StringConstraints(
            min_length=min_length,
            max_length=max_length,
            strip_whitespace=strip_whitespace,
            to_lower=to_lower,
            to_upper=to_upper,
            pattern=pattern,
        ),
        AfterValidator(_reject_control_or_format),
    ]
