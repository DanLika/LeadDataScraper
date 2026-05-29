"""Regression pin for the Py3.10 ``fromisoformat`` microsecond intolerance.

Python 3.10 (the prod container baseline — Microsoft Playwright image
``v1.60.0-jammy`` ships Python 3.10) rejects fractional-second components
whose digit count isn't 3 or 6, raising
``ValueError: Invalid isoformat string`` on Supabase-emitted timestamps
like ``2026-05-28T14:45:34.51428+00:00`` (5 digits). CI runs Python 3.12
which accepts arbitrary precision, so the bug is invisible to the unit
suite unless we exercise the helper directly.

If any future ``timestamptz`` parse regresses to bare
``datetime.fromisoformat``, the 5-digit case here fails on the prod
runtime and the test grid catches it locally on Python 3.10
(``tox -e py310`` / Docker).
"""

from __future__ import annotations

from datetime import timezone

import pytest

from src.utils.datetime_helper import parse_iso_timestamp


@pytest.mark.parametrize(
    "iso_string,expected_microsecond",
    [
        ("2026-05-28T14:45:34.514+00:00", 514_000),
        ("2026-05-28T14:45:34.5142+00:00", 514_200),
        ("2026-05-28T14:45:34.51428+00:00", 514_280),
        ("2026-05-28T14:45:34.514280+00:00", 514_280),
        ("2026-05-28T14:45:34.5142803+00:00", 514_280),
    ],
)
def test_parse_iso_timestamp_microsecond_widths(
    iso_string: str, expected_microsecond: int
) -> None:
    """Accepts 3/4/5/6/7 digit fractional seconds without raising."""
    parsed = parse_iso_timestamp(iso_string)
    assert parsed.microsecond == expected_microsecond
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timezone.utc.utcoffset(parsed)


def test_parse_iso_timestamp_accepts_z_suffix() -> None:
    """Legacy ``.replace("Z", "+00:00")`` shim is no longer needed."""
    parsed = parse_iso_timestamp("2026-05-28T14:45:34.51428Z")
    assert parsed.microsecond == 514_280
    assert parsed.tzinfo is not None


def test_parse_iso_timestamp_rejects_garbage() -> None:
    """Contract preserved: malformed input still raises ValueError so
    callers' existing ``try/except ValueError`` paths keep firing."""
    with pytest.raises(ValueError):
        parse_iso_timestamp("not-a-timestamp")
