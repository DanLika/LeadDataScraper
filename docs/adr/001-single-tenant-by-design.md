# ADR-001: Single-tenant by design (the `OPERATOR_EMAIL` invariant)

- **Status:** Accepted
- **Date:** 2026-05-22
- **Deciders:** Operator

## Context

LeadDataScraper is built and operated by one person for their own outreach
pipeline. There is no team, no contractor pool, no end-customer account that
needs lead isolation. Every authed request belongs to the same human.

Multi-tenant architecture has a real cost. Every read site needs a
`WHERE owner_user_id = current_user`. Every FK needs to respect the tenant.
Every test fixture needs to seed a tenant. Every Supabase RLS policy becomes
per-row instead of deny-all. The boilerplate compounds across hundreds of
files, and every line is one more place a tenant boundary can leak.

The alternative is to **lean into the single-user reality**, design every
endpoint as if there is exactly one operator, and make that invariant
verifiable at boot.

## Decision

Backend handlers treat the operator as implicit. Per-resource endpoints —
`/process-lead`, `/draft-outreach`, `/orchestrator/status/{job_id}`,
`/campaigns/{id}/...` — do **not** filter by `owner_user_id`. The column does
not exist on any table.

When `OPERATOR_EMAIL` is set in the backend `.env`, the lifespan runs
`_assert_single_tenant_if_enforced()` (in `backend/main.py`) which queries
Supabase Auth and asserts exactly that one user exists. **The check is
fail-closed:** the only swallowed exception is the explicit `RuntimeError`
raised on a real invariant violation; any other failure (Supabase Auth API
hiccup, permission error, network blip) re-raises and aborts boot. "Could
not run" must not pass for "passed".

Unset → check skipped (dev convenience; the assertion is not gating local
work).

## Consequences

**Positive:**
- Every handler is ~30 lines smaller — no JOIN or WHERE on `owner_user_id`.
- Test fixtures don't seed a tenant row before each scenario.
- Supabase RLS stays deny-all on `leads`, `campaigns`, `campaign_messages`,
  `orchestration_jobs`. `service_role` bypasses RLS server-side and is the
  only DB role the backend ever uses.
- The schema is simpler; the boundary check lives in one function instead of
  ~50 SQL clauses.
- The data model and the human's mental model match. No "but what if a
  second user…" branching at design time.

**Negative / trade-offs:**
- If a second Supabase Auth user is provisioned (intentionally or not) and
  `OPERATOR_EMAIL` is unset, every per-resource endpoint becomes a data
  leak. The assertion is the *only* defense, and it is opt-in.
- Migrating to multi-tenant later means a non-trivial migration:
  `owner_user_id NOT NULL DEFAULT <operator-uuid>` backfill on every table,
  plus updating every handler + RLS policy. Estimate: 2 PR weeks.
- The single-tenancy assumption leaks into operational tooling (the CI
  `concurrency-tests` job, the orphan/zombie sweep) — none of these scale to
  multi-tenant cleanly.

## Mitigations

- `OPERATOR_EMAIL` is documented as the day-1 production env var in
  [`docs/onboarding.md`](../onboarding.md) and
  [`docs/secret-inventory.md`](../secret-inventory.md).
- The check is verified by the production env var matrix.
- The `daily-cron` health sweeps will surface a second auth user as a fail
  on the lifespan smoke check.

If LeadDataScraper ever ships to a second human, **this ADR is superseded**.
The successor ADR documents the migration plan, the RLS policy redesign, and
the contractor onboarding path. Add a row to `.github/CODEOWNERS`
(`/frontend/ @DanLika @new-contractor`) only after that successor is in.

## References

- `backend/main.py::_assert_single_tenant_if_enforced`
- CLAUDE.md → "API Security" → "Optional single-tenancy assertion"
- CLAUDE.md → "Soft-delete decision (deliberately not adopted)"
  (related single-operator reasoning, also locked in by [ADR-005](005-no-soft-delete.md))
