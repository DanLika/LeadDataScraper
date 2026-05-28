"""Daily orphan + zombie sweep.

Five checks, each independent:

1. **Soft-orphan campaign_messages** — rows whose ``lead_unique_key`` no
   longer matches any ``leads.unique_key``. The FK
   (``campaign_messages_lead_unique_key_fkey``) should make this
   impossible, but a dropped / made-DEFERRABLE FK via Supabase Studio
   would let one slip in. Report-only — schema drift is fixed via the
   schema-drift gate, not by deleting orphan rows.

2. **Zombie orchestration_jobs** — ``status='running'`` with
   ``updated_at`` older than ``ZOMBIE_THRESHOLD_HOURS``. The
   orchestrator worker crashed or was killed; no caller will ever set
   the status. **AUTO-HEALED** — flipped to ``'failed'`` so the
   orchestrator stops counting the slot as live. This is the only
   auto-heal in the script; everything else needs an operator decision.

3. **Stuck leads** — ``audit_status IN ('Pending','Processing')`` with
   ``updated_at`` older than ``STUCK_THRESHOLD_HOURS``. The audit
   worker likely crashed mid-flight; no automatic resolution exists
   (could be retried, could be skipped, could have been mis-classified
   as Pending). Report-only.

4. **State-machine violation on campaign_messages** — ``sent_at IS NOT
   NULL`` while ``status='pending'``. One of the two writes is wrong;
   no safe auto-heal because we don't know which side is truth.

5. **Completed-without-results invariant** — ``audit_status='Completed'``
   with ``audit_results IS NULL``. Indicates a producer regression —
   completed audits MUST persist their JSONB payload. Pairs with the
   ``check_jsonb_shapes.py`` shape gate.

Why zombie is the only auto-heal:

- The threshold (4h) is long enough that an alive-but-slow job is rare.
- Flipping ``'running'`` → ``'failed'`` is reversible at zero cost; the
  job ID still exists, the operator can re-run it.
- The other four checks involve user data (audit_results payloads,
  cross-table truth, FK referential state); flipping the wrong side
  could destroy info that exists nowhere else.

Run via security.yml (push + daily cron) or locally:

    DATABASE_URL=postgres://...  python -m src.scripts.check_orphans_and_zombies

Exit codes:
    0 = no findings on any check
    1 = at least one check found rows (report-only checks contribute,
        as does a successful zombie heal — the operator should see what
        was healed even if action was taken)
    2 = misconfigured run (missing DATABASE_URL, can't reach DB, etc.)
"""
from __future__ import annotations

import os
import sys

import psycopg

ZOMBIE_THRESHOLD_HOURS = 4
STUCK_THRESHOLD_HOURS = 24
SAMPLE_LIMIT = 10


def _scalar(cur: psycopg.Cursor) -> int:
    row = cur.fetchone()
    assert row is not None, "expected single-row result"
    return int(row[0])


def _check_soft_orphans(conn: psycopg.Connection) -> list[str]:
    cur = conn.execute(
        "SELECT cm.id, cm.lead_unique_key, cm.created_at "
        "FROM campaign_messages cm "
        "LEFT JOIN leads l ON l.unique_key = cm.lead_unique_key "
        "WHERE cm.lead_unique_key IS NOT NULL AND l.unique_key IS NULL"
    )
    rows = cur.fetchall()
    if not rows:
        return []
    lines = [
        f"SOFT-ORPHAN campaign_messages: {len(rows)} row(s) reference "
        f"a lead_unique_key that no longer exists in leads. The FK "
        f"campaign_messages_lead_unique_key_fkey may have been dropped "
        f"or made DEFERRABLE — re-run schema-drift gate."
    ]
    for row in rows[:SAMPLE_LIMIT]:
        lines.append(f"    sample: {row!r}")
    if len(rows) > SAMPLE_LIMIT:
        lines.append(f"    ... and {len(rows) - SAMPLE_LIMIT} more")
    return lines


def _check_and_heal_zombies(conn: psycopg.Connection) -> list[str]:
    threshold_clause = f"interval '{ZOMBIE_THRESHOLD_HOURS} hours'"
    cur = conn.execute(
        "SELECT id, updated_at, current_phase "
        "FROM orchestration_jobs "
        f"WHERE status = 'running' AND updated_at < (now() - {threshold_clause})"
    )
    zombies = cur.fetchall()
    if not zombies:
        return []

    # AUTO-HEAL: flip dead 'running' jobs to 'failed' so the orchestrator
    # stops treating the slot as live. updated_at refreshed via the
    # column default would NOT fire on UPDATE — set it explicitly.
    cur = conn.execute(
        "UPDATE orchestration_jobs "
        "SET status = 'failed', updated_at = timezone('utc', now()) "
        f"WHERE status = 'running' AND updated_at < (now() - {threshold_clause}) "
        "RETURNING id"
    )
    healed_ids = [row[0] for row in cur.fetchall()]

    lines = [
        f"ZOMBIE orchestration_jobs: {len(zombies)} row(s) had "
        f"status='running' older than {ZOMBIE_THRESHOLD_HOURS}h. "
        f"AUTO-HEALED to status='failed' ({len(healed_ids)} rows updated)."
    ]
    for row in zombies[:SAMPLE_LIMIT]:
        lines.append(f"    sample: {row!r}")
    if len(zombies) > SAMPLE_LIMIT:
        lines.append(f"    ... and {len(zombies) - SAMPLE_LIMIT} more")
    return lines


def _check_stuck_leads(conn: psycopg.Connection) -> list[str]:
    threshold_clause = f"interval '{STUCK_THRESHOLD_HOURS} hours'"
    # `AND is_demo = false` excludes Phase 13.3 demo-seed rows that are
    # intentionally inserted with audit_status='Pending' (see
    # seed_demo_data.py L271 + docstring "Pending rows would also fail-
    # fast on resolution"). Without this filter the daily security gate
    # flags 3 _demo_* rows as stuck — false alarm, not a real worker
    # crash. CLAUDE.md "Phase 13.3 demo-data" pins the invariant.
    cur = conn.execute(
        "SELECT unique_key, audit_status, updated_at, last_processed_at "
        "FROM leads "
        "WHERE audit_status IN ('Pending', 'Processing') "
        "  AND is_demo = false "
        f"  AND updated_at < (now() - {threshold_clause})"
    )
    rows = cur.fetchall()
    if not rows:
        return []
    lines = [
        f"STUCK leads: {len(rows)} row(s) in non-terminal audit_status "
        f"(Pending/Processing) untouched for >{STUCK_THRESHOLD_HOURS}h. "
        f"Likely an audit worker crash. Manual review — could be retried, "
        f"could be reclassified."
    ]
    for row in rows[:SAMPLE_LIMIT]:
        lines.append(f"    sample: {row!r}")
    if len(rows) > SAMPLE_LIMIT:
        lines.append(f"    ... and {len(rows) - SAMPLE_LIMIT} more")
    return lines


def _check_state_machine_violation(conn: psycopg.Connection) -> list[str]:
    cur = conn.execute(
        "SELECT id, campaign_id, lead_unique_key, sent_at, status "
        "FROM campaign_messages "
        "WHERE sent_at IS NOT NULL AND status = 'pending'"
    )
    rows = cur.fetchall()
    if not rows:
        return []
    lines = [
        f"STATE-MACHINE VIOLATION on campaign_messages: {len(rows)} row(s) "
        f"have sent_at set but status='pending'. One of the two writes is "
        f"wrong — no safe auto-heal (truth could be either side)."
    ]
    for row in rows[:SAMPLE_LIMIT]:
        lines.append(f"    sample: {row!r}")
    if len(rows) > SAMPLE_LIMIT:
        lines.append(f"    ... and {len(rows) - SAMPLE_LIMIT} more")
    return lines


def _check_completed_without_results(conn: psycopg.Connection) -> list[str]:
    cur = conn.execute(
        "SELECT unique_key, last_processed_at "
        "FROM leads "
        "WHERE audit_status = 'Completed' AND audit_results IS NULL"
    )
    rows = cur.fetchall()
    if not rows:
        return []
    lines = [
        f"INVARIANT BROKEN: {len(rows)} lead(s) with audit_status='Completed' "
        f"but audit_results IS NULL. A Completed audit MUST persist the "
        f"JSONB payload — investigate parallel_auditor.py upsert path."
    ]
    for row in rows[:SAMPLE_LIMIT]:
        lines.append(f"    sample: {row!r}")
    if len(rows) > SAMPLE_LIMIT:
        lines.append(f"    ... and {len(rows) - SAMPLE_LIMIT} more")
    return lines


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL env var not set", file=sys.stderr)
        return 2

    try:
        # autocommit=True so the zombie UPDATE commits immediately even
        # if a later check raises. Per-check SELECTs are independent.
        conn = psycopg.connect(url, autocommit=True)
    except psycopg.Error as e:
        print(f"ERROR: cannot connect to DATABASE_URL: {e}", file=sys.stderr)
        return 2

    findings: list[str] = []
    try:
        findings.extend(_check_soft_orphans(conn))
        findings.extend(_check_and_heal_zombies(conn))
        findings.extend(_check_stuck_leads(conn))
        findings.extend(_check_state_machine_violation(conn))
        findings.extend(_check_completed_without_results(conn))
    except psycopg.Error as e:
        print(f"ERROR: unexpected DB error during sweep: {e}", file=sys.stderr)
        return 2
    finally:
        conn.close()

    if findings:
        print("Orphan/zombie sweep — FINDINGS:", file=sys.stderr)
        for line in findings:
            print(f"  - {line}", file=sys.stderr)
        return 1

    print("Orphan/zombie sweep PASSED (no findings)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
