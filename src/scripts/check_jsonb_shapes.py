"""Daily JSONB shape validator.

Asserts every row in the two real JSONB columns conforms to the canonical
producer shape. Run nightly via the security workflow; failure means a
producer or a Supabase Studio hand-edit has introduced shape drift.

Note: this gate was scoped down from the original request, which listed
``business_details`` and ``contact_details`` alongside the JSONB columns.
Both are declared TEXT in ``supabase_schema.sql`` and the enrichment
pipeline (``src/scrapers/enrichment_engine.py``) writes plain
natural-language prose into them via Gemini ("Full-service plumbing
company specializing in ..."). No JSON structure exists to validate.
Promoting either column to JSONB would be a separate, deliberate
migration; this gate is **not** the place to retroactively re-shape
producer output.

Columns checked:

- ``leads.audit_results`` (only when ``audit_status = 'Completed'``;
  Pending / Processing / Failed rows legitimately have NULL or partial
  payloads — gating those would be a false positive). Required keys are
  the ones consumers actually read: ``score`` (int), ``is_up`` (bool),
  ``tech_flags`` (object), ``red_flags`` (array). Producer is
  ``src/scrapers/seo_audit.py::perform_seo_audit_async``, persisted via
  ``src/core/parallel_auditor.py``.
- ``orchestration_jobs.filters``. Accepts **either** shape the
  orchestrator actually produces:
    - Pipeline: ``{"type": <str>}`` (``task_orchestrator.py:143``)
    - Discovery: ``{"query": <str>, "location": <str>}``
      (``task_orchestrator.py:101``)
  A NULL ``filters`` is accepted — early jobs may not have set it.

Run via CI (security.yml daily cron) or locally:

    DATABASE_URL=postgres://...  python -m src.scripts.check_jsonb_shapes

Exit codes:
    0 = every row conforms
    1 = at least one row violates an expected shape
    2 = misconfigured run (missing DATABASE_URL, can't reach DB, etc.)
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any

import psycopg


@dataclass(frozen=True)
class ShapeCheck:
    label: str
    # ``sql`` MUST return offending rows only (zero rows = pass). First
    # column should be a human-readable row identifier (unique_key /
    # job id) for diagnostic output.
    sql: str
    # Cap on rows to print on failure — full violation lists can be
    # huge in prod. The check still fails on any non-zero count, this
    # just keeps the log readable.
    sample_limit: int = 10


CHECKS: tuple[ShapeCheck, ...] = (
    ShapeCheck(
        label="leads.audit_results missing required keys (Completed audits only)",
        sql="""
            SELECT unique_key,
                   array(SELECT k FROM unnest(
                       ARRAY['score','is_up','tech_flags','red_flags']
                   ) k WHERE NOT (audit_results ? k)) AS missing_keys
            FROM leads
            WHERE audit_status = 'Completed'
              AND audit_results IS NOT NULL
              AND NOT (
                  audit_results ? 'score'
                  AND audit_results ? 'is_up'
                  AND audit_results ? 'tech_flags'
                  AND audit_results ? 'red_flags'
              )
        """,
    ),
    ShapeCheck(
        label="leads.audit_results value-type drift (Completed audits only)",
        sql="""
            SELECT unique_key,
                   jsonb_typeof(audit_results->'tech_flags') AS tech_flags_t,
                   jsonb_typeof(audit_results->'red_flags')  AS red_flags_t,
                   jsonb_typeof(audit_results->'score')      AS score_t,
                   jsonb_typeof(audit_results->'is_up')      AS is_up_t
            FROM leads
            WHERE audit_status = 'Completed'
              AND audit_results IS NOT NULL
              AND (
                jsonb_typeof(audit_results->'tech_flags') IS DISTINCT FROM 'object'
                OR jsonb_typeof(audit_results->'red_flags') IS DISTINCT FROM 'array'
                OR jsonb_typeof(audit_results->'score') NOT IN ('number','null')
                OR jsonb_typeof(audit_results->'is_up') NOT IN ('boolean','null')
              )
        """,
    ),
    ShapeCheck(
        label="orchestration_jobs.filters shape drift "
        "(must be {type} OR {query+location})",
        sql="""
            SELECT id, filters
            FROM orchestration_jobs
            WHERE filters IS NOT NULL
              AND NOT (
                -- Pipeline shape: {"type": <str>}
                (
                  filters ? 'type'
                  AND jsonb_typeof(filters->'type') = 'string'
                )
                OR
                -- Discovery shape: {"query": <str>, "location": <str>}
                (
                  filters ? 'query'
                  AND filters ? 'location'
                  AND jsonb_typeof(filters->'query')    = 'string'
                  AND jsonb_typeof(filters->'location') = 'string'
                )
              )
        """,
    ),
)


def _run_check(conn: psycopg.Connection, check: ShapeCheck) -> list[str]:
    cur = conn.execute(check.sql)
    rows = cur.fetchall()
    if not rows:
        return []
    errs: list[str] = [
        f"{check.label}: {len(rows)} violating row(s)",
    ]
    for row in rows[: check.sample_limit]:
        errs.append(f"    sample: {row!r}")
    if len(rows) > check.sample_limit:
        errs.append(
            f"    ... and {len(rows) - check.sample_limit} more "
            f"(showing first {check.sample_limit})"
        )
    return errs


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL env var not set", file=sys.stderr)
        return 2

    try:
        conn = psycopg.connect(url, autocommit=True)
    except psycopg.Error as e:
        print(f"ERROR: cannot connect to DATABASE_URL: {e}", file=sys.stderr)
        return 2

    failures: list[str] = []
    try:
        for check in CHECKS:
            failures.extend(_run_check(conn, check))
    except psycopg.Error as e:
        print(f"ERROR: unexpected DB error during JSONB probe: {e}", file=sys.stderr)
        return 2
    finally:
        conn.close()

    if failures:
        print("JSONB shape check FAILED:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        return 1

    print(f"JSONB shape check PASSED ({len(CHECKS)} shape rules verified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
