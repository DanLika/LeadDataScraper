"""Offline drift-parity guard for Phase 16 schema additions.

Runs without a live DB. Asserts the static halves of the drift gate
(supabase_schema.sql contents + EXPECTED_CHECK_CONSTRAINTS dict + TABLES
tuple) stay in lockstep, so a future PR that adds one half without the
other gets caught at CI time rather than on the live-DB drift check.

The CHECK-pairing rule is documented in CLAUDE.md: ``Adding a CHECK to
supabase_schema.sql REQUIRES same-PR update to EXPECTED_CHECK_CONSTRAINTS
dict in schema_drift_check.py`` (PRs #353/#356/#366 fell into this trap;
codified by PR #380/#378). This test now also enforces the same for
table-level additions (a finding from Phase 16 PR α advisor pass).
"""

from __future__ import annotations

from src.scripts.schema_drift_check import (
    EXPECTED_CHECK_CONSTRAINTS,
    SCHEMA_FILE,
    TABLES,
    parse_expected_columns,
)


def test_reply_classifications_table_in_drift_tuple() -> None:
    assert "reply_classifications" in TABLES, (
        "reply_classifications must be in TABLES so the column drift check, "
        "RLS check, deny-policy check and grants check all cover it. "
        "Without this entry the new table is silently skipped."
    )


def test_reply_classifications_columns_parsed() -> None:
    cols = parse_expected_columns(SCHEMA_FILE)
    expected = {
        "id",
        "lead_unique_key",
        "campaign_message_id",
        "message_body_hash",
        "classification",
        "confidence",
        "reasoning",
        "model_version",
        "classified_at",
    }
    missing = expected - cols["reply_classifications"]
    assert not missing, (
        f"reply_classifications schema parse missing columns: {sorted(missing)}. "
        "The parser strips line comments + string literals before splitting "
        "on top-level commas; check that no column def lives inside a "
        "string literal or comment block."
    )


def test_reply_classifications_check_constraints_declared() -> None:
    declared = EXPECTED_CHECK_CONSTRAINTS.get("reply_classifications", set())
    expected = {
        "reply_classifications_classification_allowed",
        "reply_classifications_confidence_range",
        "reply_classifications_body_hash_format",
    }
    missing = expected - declared
    assert not missing, (
        f"EXPECTED_CHECK_CONSTRAINTS['reply_classifications'] missing: "
        f"{sorted(missing)}. The live-DB drift gate would silently pass "
        "with these missing here, then fail open if Studio drops one."
    )
    # UNIQUE constraint is contype='u', NOT 'c' — must NOT be in this dict
    # (check_check_constraints queries WHERE c.contype = 'c').
    assert "reply_classifications_unique_classification" not in declared, (
        "reply_classifications_unique_classification is a UNIQUE (contype='u'), "
        "not a CHECK. Belongs in a UNIQUE-constraint check (future work), "
        "not in EXPECTED_CHECK_CONSTRAINTS."
    )


def test_sequences_pause_reason_check_declared() -> None:
    declared = EXPECTED_CHECK_CONSTRAINTS.get("sequences", set())
    assert "sequences_pause_reason_size" in declared, (
        "Phase 16 added CHECK sequences_pause_reason_size to supabase_schema.sql; "
        "EXPECTED_CHECK_CONSTRAINTS['sequences'] must list it in the same PR. "
        "Live-DB drift will pass without this (the CHECK exists), but a "
        "future Studio drop would not be caught."
    )


def test_campaign_messages_paused_by_reply_in_allowlist() -> None:
    sql = SCHEMA_FILE.read_text()
    # The most-recent (final) DROP+ADD of campaign_messages_status_allowed
    # wins at apply time. Locate the last ADD CONSTRAINT block and assert
    # 'paused_by_reply' is in its body.
    marker = "ADD CONSTRAINT campaign_messages_status_allowed"
    last_idx = sql.rfind(marker)
    assert last_idx != -1, (
        "campaign_messages_status_allowed CHECK not found in schema file"
    )
    # Grab the next ~500 chars — enough to span the IN (...) list.
    body = sql[last_idx : last_idx + 500]
    assert "'paused_by_reply'" in body, (
        "Phase 16 must extend campaign_messages.status allowlist with "
        "'paused_by_reply'. dispatch_tick filters on status='pending', so "
        "without this value the auto-pause state machine cannot mark "
        "in-flight rows as paused without violating the CHECK."
    )


def test_no_duplicate_table_keys_in_expected_checks() -> None:
    # Sanity guard — Python silently dict-literal-overwrites duplicate keys.
    # The drift-dict file is hand-edited; a future patch could re-introduce
    # the same key twice and not notice.
    from pathlib import Path
    import re

    text = Path("src/scripts/schema_drift_check.py").read_text()
    # Match keys at the top level of EXPECTED_CHECK_CONSTRAINTS only —
    # heuristic: lines that begin with 4 spaces + a quoted identifier + colon
    # AND sit between EXPECTED_CHECK_CONSTRAINTS opener and its closing brace.
    opener = text.index("EXPECTED_CHECK_CONSTRAINTS")
    body_start = text.index("{", opener)
    # Closing brace of the dict literal — first `}` at column 0 after opener.
    body_end = text.index("\n}\n", body_start)
    body = text[body_start:body_end]
    keys = re.findall(r'^    "(\w+)":\s*\{', body, flags=re.MULTILINE)
    dupes = [k for k in set(keys) if keys.count(k) > 1]
    assert not dupes, (
        f"EXPECTED_CHECK_CONSTRAINTS has duplicate top-level keys: {dupes}. "
        "Python silently keeps only the last; earlier entries are lost. "
        "Merge into a single set per table."
    )
