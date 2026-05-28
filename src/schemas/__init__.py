"""Shared Pydantic schema primitives.

Currently exposes `safe_constr` — a drop-in replacement for
``pydantic.constr(...)`` that *also* rejects NUL, other ASCII control
chars (Unicode category Cc) and format chars (Cf, e.g. zero-width-space,
RTL-override, BOM) — except common whitespace ``\\t \\n \\r``.

Rationale: bounded ``constr`` is length-only, so a payload like
``{"query": "hi\\u0000\\u202e"}`` slips past validation and crashes
the downstream layer (PostgreSQL TEXT ``INSERT`` rejects NUL with
SQLSTATE 22021; Playwright URL-encodes bidi codepoints into broken
URLs). Centralising the validator here means new request models
inherit the protection by switching from ``constr`` to ``safe_constr``.
"""

from .sanitized_str import safe_constr

__all__ = ["safe_constr"]
