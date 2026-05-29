"""Pin the 17-vector cursor-escape harness from the 2026-05-29 deep RLS audit.

`_decode_lead_cursor` (`backend/main.py`) interpolates the cursor `k` field
RAW into a PostgREST `.or_()` predicate downstream
(`src/utils/supabase_helper.py:178`). A permissive cursor with `,` `)` or
`(` would escape the intended tie-break clause and let pagination scope
escape. Defense layers, in order:

    1. outer length gate (>512 chars rejected)
    2. base64url decode
    3. inner length gate on decoded bytes (>512 rejected)
    4. JSON parse + dict-shape check
    5. per-field length gates (`c` <=64, `k` <=128)
    6. charset regex on `k`: `\\A[A-Za-z0-9_-]{1,128}\\Z`
    7. `datetime.fromisoformat(c.replace("Z", "+00:00"))` on `c`

Decoder returns `None` on any malformed input (not raises). This is
load-bearing -- callers must treat None as "start from first page".

The 17 REJECT vectors below are reconstructed from the audit's category
list (comma / paren / null / CRLF / dot / space / unicode / oversize /
bad-ISO / nested-or / RLS-bypass / empty / b64-garbage / non-dict).
The audit ran the harness in a context-mode Python sandbox; this file
pins the canonical set as pytest regressions. Future audits compare
against THIS file, not against the audit memo.

The FastAPI `/leads` Query layer ALSO caps `cursor` at `max_length=512`.
These tests pin the decoder layer specifically; both layers must hold.
See `tests/security/test_validation_authz_gate.py` for the Query layer.
"""

from __future__ import annotations

import base64
import json

import pytest

from backend.main import _decode_lead_cursor, _encode_lead_cursor

_VALID_C = "2026-01-01T00:00:00+00:00"
_VALID_K = "abc123"


def _b64(payload: object) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _mal(c: str = _VALID_C, k: str = _VALID_K) -> str:
    return _b64({"c": c, "k": k})


REJECT_VECTORS: list[tuple[str, str]] = [
    # ---- charset-regex layer: PostgREST .or_() escape attempts on `k` ----
    ("k-comma", _mal(k="abc,def")),
    ("k-close-paren", _mal(k="abc)def")),
    ("k-open-paren", _mal(k="abc(def")),
    ("k-nul", _mal(k="abc\x00def")),
    ("k-cr", _mal(k="abc\rdef")),
    ("k-lf", _mal(k="abc\ndef")),
    ("k-dot", _mal(k="abc.def")),
    ("k-space", _mal(k="abc def")),
    ("k-unicode", _mal(k="abcñdef")),
    # ---- length-gate layer ----
    ("k-oversize-129", _mal(k="a" * 129)),
    ("cursor-empty", ""),
    ("cursor-oversize-513", "A" * 513),
    # ---- structural / parse layer ----
    ("non-base64", "!!!not-base64!!!"),
    ("non-dict-payload", _b64(["c", "v", "k", "x"])),
    ("bad-iso-c", _mal(c="not-a-date")),
    # ---- intent vectors: same defenses, different attacker framing ----
    # `nested-or-injection` shares the regex layer with `k-comma` but pins
    # the documented attacker intent (escape into a second `.or_()` term).
    # Do not dedupe -- they pin different stories for future auditors.
    ("nested-or-injection", _mal(k="x,or.eq.1")),
    ("rls-bypass-via-c", _mal(c="' OR 1=1 --")),
]

assert len(REJECT_VECTORS) == 17, (
    f"expected 17 cursor-escape vectors, got {len(REJECT_VECTORS)}; "
    "update rls_deep_audit_2026-05-29 if intentional"
)


@pytest.mark.parametrize(
    "vid,payload",
    REJECT_VECTORS,
    ids=[vid for vid, _ in REJECT_VECTORS],
)
def test_cursor_escape_rejected(vid: str, payload: str) -> None:
    """Decoder MUST return None for every hostile cursor in the harness."""
    decoded = _decode_lead_cursor(payload)
    assert decoded is None, f"{vid}: decoder accepted hostile cursor {payload!r}"


def test_cursor_accept_baseline() -> None:
    """Positive control: a broken decoder returning None for ALL input
    would silently pass 17/17 reject without this."""
    encoded = _encode_lead_cursor(_VALID_C, _VALID_K)
    decoded = _decode_lead_cursor(encoded)
    assert decoded == {"c": _VALID_C, "k": _VALID_K}
