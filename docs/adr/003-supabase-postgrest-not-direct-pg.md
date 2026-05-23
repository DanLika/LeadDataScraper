# ADR-003: Supabase PostgREST, not a direct Postgres connection

- **Status:** Accepted
- **Date:** 2026-05-22
- **Deciders:** Operator

## Context

Supabase hosts the Postgres database. Two ways to talk to it:

- **PostgREST over HTTPS** via the `supabase-py` client. One transport,
  one auth mechanism (service-role JWT), Supabase pooler handles connection
  reuse.
- **Direct Postgres wire protocol** via `psycopg`/`asyncpg`/`psycopg2`. More
  control: prepared statements, multi-statement transactions, `EXPLAIN`,
  COPY, advisory locks, listen/notify. Requires app-level pool, network
  firewalling, and either trusting `service_role` for everything or
  building per-request role switching.

Both work. The choice changes the operational footprint substantially.

## Decision

The backend (`backend/` + `src/` excluding `src/scripts/` and `tests/`)
talks to Supabase **only** over PostgREST via `supabase-py`. Direct pg
imports are forbidden in the runtime path — `tests/test_connection_pool.py`
runs a static grep that asserts no module under those paths imports
`psycopg`/`asyncpg`/`psycopg2`/`pg8000`, and fails CI if one slips in.

The exception is `src/scripts/` (CI-only health checks: schema-drift,
referential-integrity, query-plans, orphan/zombie sweep, NULL ratio,
function safety, grants matrix, statement timeout). Those use
`psycopg[binary]>=3.1` — installed inline by every Supabase-DB CI job, not
shipped to the runtime image — and connect via `DATABASE_URL`, which is
required to be a pooler URL (`*.pooler.supabase.com`), not the direct
`db.<ref>.supabase.co` host.

## Consequences

**Positive:**
- One transport: HTTPS. No firewall rule for the DB host. No pool
  exhaustion at the app layer — the Supabase pooler (PgBouncer) absorbs it.
- One auth mechanism: `service_role` JWT, kept in env. RLS is the boundary
  for non-bypass callers; the backend bypasses by design (ADR-001).
- Schema changes go through Supabase migrations + Studio, with the
  schema-drift gate in CI ensuring `supabase_schema.sql` matches reality.
- The runtime container ships zero database drivers — smaller attack
  surface, faster cold start.
- The connection-pool contract is testable as a static grep, not as a
  runtime invariant that drifts.

**Negative / trade-offs:**
- PostgREST is row-shaped. Multi-statement transactions are not native to
  the supabase-py client. The pipeline is built around **idempotent upserts
  on `unique_key`** and **`pg_advisory_xact_lock` for race-critical
  read-modify-write** (the pattern is exercised by
  `tests/test_concurrent_writes.py`).
- `supabase-py` is sync. Every hot-path read needs `asyncio.to_thread`
  (see ADR-002). New hot paths need this wrapper added explicitly.
- Operations that *do* need direct pg (EXPLAIN plans, `pg_stat_*` reads,
  `pg_advisory_lock`, `LISTEN/NOTIFY`) live in `src/scripts/` and run in
  CI only — they aren't usable from request handlers.
- Some standard Postgres features are awkward: `COPY` for bulk insert
  becomes an upsert loop; `RETURNING` becomes a follow-up select.

## Performance budget

The PostgREST round-trip is ~30–80 ms in-region. The hot dashboard read
(`/stats`, cached 60 s) and the cursor-paginated `/leads?limit=50` finish
inside the `/stats` 200 ms target. The `_StatsCache` (60 s TTL with double-
checked locking) absorbs traffic spikes without thrashing PostgREST.

The `query_profiler` (`src/utils/query_profiler.py`, env-gated
`QUERY_PROFILER=1`) records verb + caller-frame + timing for every
supabase-py call to catch N+1 patterns in dev. A 2026-05-22 static audit
of `src/` found zero O(N) N+1 patterns; the profiler exists as a
regression guard.

## References

- `src/utils/supabase_helper.py`
- `tests/test_connection_pool.py`
- `src/scripts/check_query_plans.py` (the only direct-pg consumer at scale)
- CLAUDE.md → "Connection pool / pooler-URL contract"
- CLAUDE.md → "Async DB wrappers"
