# ADR-005: No soft delete (Faza 5.18)

- **Status:** Accepted
- **Date:** 2026-05-22 (decided during Faza 5.18 / Phase 5.18 review)
- **Deciders:** Operator

## Context

During the Faza 5.18 review, the question of adopting soft-delete semantics
came up for `leads`, `campaigns`, and `campaign_messages`. The standard
pattern is:

- Add `deleted_at TIMESTAMPTZ NULL` to each table.
- Every read site adds `WHERE deleted_at IS NULL`.
- Every FK reference becomes "join AND child.deleted_at IS NULL".
- DELETEs become `UPDATE … SET deleted_at = now()`.

Soft delete buys two things: undeletability ("oh wait, restore that lead")
and a per-row audit trail. The costs are not free.

The pipeline already has well-defined hard-delete entry points, and the
data-recovery story has a different shape than per-row undelete.

## Decision

**Hard delete only.** The pipeline keeps its two explicit DELETE points:

- `DELETE /leads/clear` — wipes the leads table. Behind the X-Admin-Token
  gate. Rate-limited to **3 per hour**. Confirm-dialog on the frontend
  with the lead count.
- `DELETE /campaigns/{id}` — cascades via the FK on
  `campaign_messages.campaign_id`.

Recovery is via the **Supabase PITR (point-in-time recovery) snapshot**
chain, verified by `backup-verify-deep.yml` (monthly, restores to
`now() - 1h`, runs schema-drift + referential-integrity + row-count diff
on the restore). Recovery is at the *database* level, not the *row* level.

## Consequences

**Positive:**
- Every SELECT site stays simple — no `WHERE deleted_at IS NULL`
  boilerplate, no audit-grep rule enforcing the filter.
- FK CASCADE semantics work as users expect (the FK is the truth).
- Indexes don't need partial `WHERE deleted_at IS NULL` clauses; the
  existing `idx_leads_created_at_desc`, `idx_leads_audit_status`, and
  `idx_orchestration_jobs_status` stay as they are.
- The schema-drift gate has nothing extra to verify.
- The RLS deny-all + service-role-bypass model in ADR-001 doesn't grow a
  soft-delete-aware policy on top.
- Storage doesn't accumulate tombstone rows — every row in the table is a
  live row.

**Negative / trade-offs:**
- **No in-system undelete.** The only recovery path is the PITR snapshot
  restore, which is per-database, not per-row. A misclicked
  `DELETE /leads/clear` requires a Supabase support ticket to restore from
  PITR — or eating the loss.
- Mitigated by: (a) the X-Admin-Token header, (b) the 3/hour rate limit on
  the destructive endpoint, (c) the in-UI confirm dialog naming the
  count + "this cannot be undone".
- No row-level audit trail. We don't know *who* deleted *which row when*.
  The pipeline is single-operator (ADR-001), so this matters less than it
  would in a team.

## When to revisit

This ADR is superseded if any of the following land:

- The pipeline ships to a **second operator** (ADR-001 supersession path
  triggers this in parallel). Multi-operator deletes need an audit trail.
- **Regulatory retention** (GDPR data-subject-access logs, SOC2 audit
  trail, etc.). Tombstones become a compliance artifact.
- An operator-facing **trash / undelete UI** is wanted on a per-row basis.

Migration estimate: ~1 PR week. 3 columns added with default NULL,
**32 SELECT sites** (current count via `git grep -E
'\.table\("(leads|campaigns|campaign_messages|orchestration_jobs)"\).select'
src backend`) get the `WHERE deleted_at IS NULL` predicate, 4 FK
constraints get the multi-condition predicate, 6 index changes go
partial, the schema-drift gate gets a new constraint check, the
soft-delete-aware RLS policies replace the deny-all on the relevant
tables.

## References

- `src/utils/supabase_helper.py` (DELETE call sites)
- `backend/main.py::clear_leads` + `delete_campaign`
- `supabase_schema.sql` (no `deleted_at` columns anywhere)
- `.github/workflows/backup-verify-deep.yml` (PITR verification chain)
- CLAUDE.md → "Soft-delete decision (deliberately not adopted)"
- [ADR-001](001-single-tenant-by-design.md) — the single-operator
  assumption that makes hard delete defensible
