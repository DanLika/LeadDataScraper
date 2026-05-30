"""Regression tests for the `_upsert_leads_to_db` dedupe + counter contract.

Pins the bug observed in prod 2026-05-30 where uploading a CSV with three
rows sharing the same Name + Website + email (so `load_csv_with_unique_key`
derived the same `unique_key` for all three) returned HTTP 200 "processing"
yet inserted ZERO rows. Postgres raises error code `21000`
(`ON CONFLICT DO UPDATE command cannot affect row a second time`) when the
upsert batch contains duplicate constrained values; supabase-py swallows
the APIError, returns None, and the operator sees nothing.

See `memory/csv_import_edge_cases_2026-05-30.md` Finding 1 for the prod
trace + replay recipe.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# `backend.main` is normally imported via `backend.main` but the project
# also runs it as a script; either form works once `backend/` is on the
# path (conftest.py at repo root handles this for unit tests).
BACKEND_PATH = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(BACKEND_PATH))

import main as backend_main  # noqa: E402  (after sys.path mutation)


def _fake_upsert_result(rows: list[dict]) -> MagicMock:
    """Mimic `supabase-py` execute()-style return."""
    result = MagicMock()
    result.data = rows
    return result


class TestUpsertDedupe:
    def test_three_identical_unique_keys_collapse_to_one(self):
        """3 rows, same unique_key → upsert called with 1 row, deduped=2."""
        df = pd.DataFrame(
            [
                {"unique_key": "K1", "name": "A1", "email": "x@y.local", "phone": "1"},
                {"unique_key": "K1", "name": "A2", "email": "x@y.local", "phone": "2"},
                {"unique_key": "K1", "name": "A3", "email": "x@y.local", "phone": "3"},
            ]
        )
        fake_db = MagicMock()
        fake_db.upsert_leads.return_value = _fake_upsert_result(
            [{"unique_key": "K1", "name": "A3"}]
        )
        with patch.object(backend_main, "db", fake_db):
            submitted, deduped, inserted = backend_main._upsert_leads_to_db(df)
        assert submitted == 3
        assert deduped == 2
        assert inserted == 1
        # Exactly one row submitted to Supabase, and `keep='last'` semantics
        # mean the LAST row's values won (A3, phone=3).
        called_with = fake_db.upsert_leads.call_args[0][0]
        assert len(called_with) == 1
        assert called_with[0]["name"] == "A3"
        assert called_with[0]["phone"] == "3"

    def test_distinct_unique_keys_pass_through_unchanged(self):
        """No dedupe when keys differ; counters reflect submitted == inserted."""
        df = pd.DataFrame(
            [
                {"unique_key": "K1", "name": "A"},
                {"unique_key": "K2", "name": "B"},
                {"unique_key": "K3", "name": "C"},
            ]
        )
        fake_db = MagicMock()
        fake_db.upsert_leads.return_value = _fake_upsert_result(
            [{"unique_key": k} for k in ("K1", "K2", "K3")]
        )
        with patch.object(backend_main, "db", fake_db):
            submitted, deduped, inserted = backend_main._upsert_leads_to_db(df)
        assert submitted == 3
        assert deduped == 0
        assert inserted == 3
        called_with = fake_db.upsert_leads.call_args[0][0]
        assert len(called_with) == 3

    def test_supabase_returns_none_yields_zero_inserted(self):
        """`db.upsert_leads` returning None (swallowed APIError) → inserted=0."""
        df = pd.DataFrame([{"unique_key": "K1", "name": "A"}])
        fake_db = MagicMock()
        fake_db.upsert_leads.return_value = None
        with patch.object(backend_main, "db", fake_db):
            submitted, deduped, inserted = backend_main._upsert_leads_to_db(df)
        assert submitted == 1
        assert deduped == 0
        assert inserted == 0

    def test_empty_dataframe_short_circuits(self):
        df = pd.DataFrame(columns=["unique_key", "name"])
        fake_db = MagicMock()
        with patch.object(backend_main, "db", fake_db):
            submitted, deduped, inserted = backend_main._upsert_leads_to_db(df)
        assert (submitted, deduped, inserted) == (0, 0, 0)
        fake_db.upsert_leads.assert_not_called()

    def test_dataframe_without_unique_key_column_no_dedupe(self):
        """If the upstream pipeline never produced a `unique_key` column, the
        helper should NOT crash on the dedupe step — let the upsert raise
        downstream so the schema-mismatch path takes over."""
        df = pd.DataFrame([{"name": "A"}, {"name": "B"}])
        fake_db = MagicMock()
        fake_db.upsert_leads.return_value = _fake_upsert_result(
            [{"name": "A"}, {"name": "B"}]
        )
        with patch.object(backend_main, "db", fake_db):
            submitted, deduped, inserted = backend_main._upsert_leads_to_db(df)
        assert submitted == 2
        assert deduped == 0
        assert inserted == 2


class TestUpsertDedupeProdRepro:
    """Pins the exact 08_duplicate_keys.csv shape that prod observed
    silently zero-inserting. Bytes-for-bytes equivalent to the CSV in
    `memory/csv_import_edge_cases_2026-05-30.md`."""

    REPRO_CSV_BYTES = (
        b"name,email,phone,website\n"
        b"EDGETEST-Dup-1,dup1@edgetest.local,+38566800001,https://dup.edgetest.local\n"
        b"EDGETEST-Dup-1,dup1@edgetest.local,+38566800002,https://dup.edgetest.local\n"
        b"EDGETEST-Dup-1,dup1@edgetest.local,+38566800003,https://dup.edgetest.local\n"
    )

    def test_three_identical_rows_dedupe_to_one(self):
        """End-to-end shape that prod hit. unique_key here is whatever the
        post-pipeline frame contains; the dedupe is keyed on the column the
        upsert constraint uses."""
        df = pd.read_csv(pd.io.common.BytesIO(self.REPRO_CSV_BYTES), dtype=str)
        # Simulate the post-pipeline state: unique_key set, columns lower-case.
        df = df.assign(unique_key=df["website"].str.cat(df["email"], sep="_"))
        assert df["unique_key"].nunique() == 1  # bug precondition

        fake_db = MagicMock()
        fake_db.upsert_leads.return_value = _fake_upsert_result(
            [{"unique_key": df["unique_key"].iloc[0]}]
        )
        with patch.object(backend_main, "db", fake_db):
            submitted, deduped, inserted = backend_main._upsert_leads_to_db(df)

        assert submitted == 3
        assert deduped == 2
        assert inserted == 1
        # Phone of the surviving row should be the LAST one (last-wins).
        called_with = fake_db.upsert_leads.call_args[0][0]
        assert len(called_with) == 1
        assert called_with[0]["phone"] == "+38566800003"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
