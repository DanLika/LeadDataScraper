# Phase 16 — Task T3 — Findings disposition

Companion to [`phase16-t3-data-obs.md`](./phase16-t3-data-obs.md).
Tracks what shipped, what's deferred, and what's been accepted as a
non-action.

## Shipped in this PR

| Finding | Severity | Action |
|---------|----------|--------|
| **T3.8-A** | HIGH | `REVOKE ALL ON public.account_deletions FROM anon, authenticated, PUBLIC;` applied to live DB + mirrored in `supabase_schema.sql`. Post-REVOKE `information_schema.table_privileges` shows only `postgres` + `service_role` rows for the table. `service_role` SELECT verified still works (T3-style sanity). |
| **T3.1-A** | MEDIUM | `CREATE INDEX IF NOT EXISTS idx_leads_seo_score ON public.leads (seo_score) WHERE seo_score IS NOT NULL;` applied to live DB + mirrored in `supabase_schema.sql`. Partial: `seo_score` is NULL on every pre-audit row, so the partial form keeps writes cheap. `check_query_plans.py::HOT_PATH_QUERIES` extended with `BETWEEN 50 AND 100` probe — re-verified `Index Scan idx_leads_seo_score` for both `BETWEEN` and `<` operators (plain btree handles both directions; no DESC variant needed). |

## Accepted as known acceptable — no action

### T3.8-B — `account_deletions_deny_all` is RESTRICTIVE while peer deny-all policies are PERMISSIVE

- **Disposition:** accepted as-is. RESTRICTIVE + `USING(false)` and PERMISSIVE + `USING(false)` are semantically equivalent for a pure deny-all when nothing else stacks on top — both deny.
- **Why not standardize now:** RESTRICTIVE is strictly stronger (a future permissive policy can't override it), so the mismatch errs on the safer side for the GDPR audit log specifically. Standardizing all 5 tables on RESTRICTIVE would be a defensible cleanup, but no security gain on the 4 core tables today and not worth churning the schema-drift baseline for a docs-only change.
- **Reopen if:** a future change adds a permissive policy on top of any of the 4 PERMISSIVE deny-alls, or someone tries to mix permissive/restrictive on the same table.

### T3.9-A — `update_updated_at_column` has EXECUTE granted to PUBLIC / anon / authenticated

- **Disposition:** accepted as-is. This is a trigger function (returns `NEW`), not a PostgREST RPC. It is **not** `SECURITY DEFINER` (so it runs as the calling role, never escalating), takes no arguments, and is functionally useless when called outside trigger context.
- **Why not REVOKE:** standard Supabase pattern for `BEFORE UPDATE` trigger glue. The `EXEC_GRANT_ALLOWLIST` in `src/scripts/check_function_safety.py` is the right place to record the exception — out of scope for this PR (gate not yet wired into CI per the script's own header).
- **Reopen if:** someone marks it `SECURITY DEFINER` or wires it through PostgREST as a callable.

## Deferred (no infrastructure to exercise live)

- **T3.2** — JSONB shape on `audit_results`: no `Completed` rows in the live DB. Static gate `check_jsonb_shapes.py` continues to enforce the contract once data exists.
- **T3.12 / T3.13 / T3.14** — cooperative cancel, /stats stampede, browser pool: require a running backend + workload. Static contracts verified in T3 sweep; live repros documented in the main report.
- **T3.15 / T3.16 / T3.17 live tail** — Sentry capture + Web Vitals aggregation + JsonFormatter stdout sample: require Render-side log access (Supabase MCP only sees Postgres logs).

## Verification artifacts (re-runnable)

```sql
-- Post-REVOKE grants check (must show ONLY postgres + service_role):
SELECT grantee, privilege_type FROM information_schema.table_privileges
  WHERE table_schema='public' AND table_name='account_deletions'
  ORDER BY grantee, privilege_type;

-- Post-CREATE INDEX plan check (must be Index Scan idx_leads_seo_score):
SET LOCAL enable_seqscan = off;
EXPLAIN (FORMAT JSON)
  SELECT * FROM public.leads WHERE seo_score BETWEEN 50 AND 100;

-- service_role sanity (REVOKE didn't hit service_role):
SET LOCAL ROLE service_role;
SELECT count(*) FROM public.account_deletions;  -- expects 0, not perm-denied
```
