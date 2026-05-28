"""Regression tests for the security-critical helpers added during the
2026-05 audit + pentest sessions. Each of these closed a real bug; this
file locks the behaviour so a refactor can't silently regress it.

- `sanitize_csv_cell` / `sanitize_dataframe_for_csv` — CSV / formula
  injection guard (neutralises `= @ + - \\t \\r`-led cells on export).
- `_coalesce_duplicate_columns` — CSV-import data-loss fix (BUGS.md
  Round 4 A): merges duplicate column names instead of letting pandas
  silently drop a populated column.
- `TaskOrchestrator._is_valid_uuid` — turns a non-UUID `job_id` into a
  clean `not_found` instead of a 500 (PENTEST_CRAWLER.md Round 2).
"""

import os
import sys
import pandas as pd
import pytest

backend_path = os.path.join(os.getcwd(), "backend")
if backend_path not in sys.path:
    sys.path.append(backend_path)

from src.utils.csv_helper import sanitize_csv_cell, sanitize_dataframe_for_csv
from src.core.task_orchestrator import TaskOrchestrator


# ───────────────────────── sanitize_csv_cell ─────────────────────────


@pytest.mark.parametrize(
    "payload",
    [
        '=HYPERLINK("http://evil","x")',
        "=cmd|'/c calc'!A1",
        "@SUM(1+9)",
        "+1+1",
        "-2+3",
        "\tTabLed",
        "\rCarriageReturn",
    ],
)
def test_sanitize_csv_cell_prefixes_formula_payloads(payload):
    out = sanitize_csv_cell(payload)
    assert out == "'" + payload, "formula-leading cell must be apostrophe-prefixed"
    assert out[0] == "'"


@pytest.mark.parametrize(
    "safe",
    [
        "Acme Bakery",
        "https://example.com",
        "owner@example.com",  # '@' mid-string is fine; only leading char matters
        "123 Main St",
        "",  # empty string — untouched
    ],
)
def test_sanitize_csv_cell_leaves_safe_values(safe):
    assert sanitize_csv_cell(safe) == safe


@pytest.mark.parametrize("nonstr", [None, 42, 3.14, True, ["x"], {"a": 1}])
def test_sanitize_csv_cell_passes_through_non_strings(nonstr):
    # Non-string cells (numbers, NaN-ish, containers) must pass untouched.
    assert sanitize_csv_cell(nonstr) is nonstr


def test_sanitize_dataframe_for_csv_neutralises_string_columns():
    df = pd.DataFrame(
        {
            "name": ['=HYPERLINK("x","y")', "Safe Co", "@SUM(1)"],
            "score": [10, 20, 30],  # numeric col — untouched
            "note": ["-danger", "fine", "+also"],
        }
    )
    out = sanitize_dataframe_for_csv(df)
    assert out["name"].tolist() == ['\'=HYPERLINK("x","y")', "Safe Co", "'@SUM(1)"]
    assert out["note"].tolist() == ["'-danger", "fine", "'+also"]
    assert out["score"].tolist() == [10, 20, 30]  # numbers unchanged
    # original frame must not be mutated
    assert df["name"].iloc[0] == '=HYPERLINK("x","y")'


def test_sanitize_dataframe_for_csv_empty_frame():
    out = sanitize_dataframe_for_csv(pd.DataFrame())
    assert out.empty


# ──────────────────── _coalesce_duplicate_columns ────────────────────


def _coalesce():
    # Imported lazily — `backend.main` initialises the FastAPI app.
    from main import _coalesce_duplicate_columns

    return _coalesce_duplicate_columns


def test_coalesce_no_duplicates_is_passthrough():
    df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
    out = _coalesce()(df)
    assert list(out.columns) == ["a", "b"]
    assert out.equals(df)


def test_coalesce_merges_duplicate_columns_preferring_non_null():
    # Simulate the post-AI-mapping frame: an empty placeholder `email`
    # column collides with a populated renamed `email` column.
    df = pd.DataFrame(
        [
            [None, "a@x.com"],
            [None, "b@x.com"],
        ],
        columns=["email", "email"],
    )
    out = _coalesce()(df)
    assert list(out.columns) == ["email"]  # de-duplicated
    assert out["email"].tolist() == ["a@x.com", "b@x.com"]  # populated wins


def test_coalesce_first_non_null_left_to_right():
    df = pd.DataFrame(
        [
            ["L", None],
            [None, "R"],
            [None, None],
        ],
        columns=["v", "v"],
    )
    out = _coalesce()(df)
    assert list(out.columns) == ["v"]
    assert out["v"].tolist()[:2] == ["L", "R"]
    assert pd.isna(out["v"].tolist()[2])


def test_coalesce_preserves_unique_columns_alongside_dupes():
    df = pd.DataFrame(
        [
            [1, None, "x", "kept"],
            [2, "y", None, "kept2"],
        ],
        columns=["id", "dup", "dup", "name"],
    )
    out = _coalesce()(df)
    assert sorted(out.columns) == ["dup", "id", "name"]
    assert out["dup"].tolist() == ["x", "y"]
    assert out["name"].tolist() == ["kept", "kept2"]


# ───────────────────── TaskOrchestrator._is_valid_uuid ─────────────────


@pytest.mark.parametrize(
    "good",
    [
        "00000000-0000-0000-0000-000000000000",
        "9a9cf962-358a-493e-b2ab-6367403e9871",
        "9A9CF962-358A-493E-B2AB-6367403E9871",  # upper-case
    ],
)
def test_is_valid_uuid_accepts_real_uuids(good):
    assert TaskOrchestrator._is_valid_uuid(good) is True


@pytest.mark.parametrize(
    "bad",
    [
        "notauuid-attacker",
        "DROP TABLE leads",
        "../../etc/passwd",
        "' OR '1'='1",
        "",
        "12345",
        None,
        12345,
    ],
)
def test_is_valid_uuid_rejects_garbage(bad):
    assert TaskOrchestrator._is_valid_uuid(bad) is False
