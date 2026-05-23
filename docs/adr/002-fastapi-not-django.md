# ADR-002: FastAPI, not Django, for the backend

- **Status:** Accepted
- **Date:** 2026-05-22
- **Deciders:** Operator

## Context

The backend has to coordinate:

- Async outbound HTTP for the SEO audit (`aiohttp`, 50+ concurrent in-flight)
- Playwright browser automation (subprocess + async API)
- Supabase PostgREST round-trips (sync supabase-py, wrapped in
  `asyncio.to_thread` on hot paths)
- Google Gemini calls (sync today; async in places via `client.aio.models`)
- Long-running background orchestration jobs (chunks of 50 leads, minutes
  per job)

Two Python web frameworks were candidates:

- **Django** (+ DRF / Channels). Mature ecosystem, batteries-included admin,
  built-in ORM + auth + migrations. Sync-first; `async` is grafted on via
  `sync_to_async`/`asgiref`.
- **FastAPI** + uvicorn. Async-first, Pydantic-native validation, OpenAPI
  out of the box, single-process model with optional workers.

The pipeline has **no** users-table to admin, **no** server-rendered HTML
views (frontend is Next.js), and **no** form-handling needs Django solves.
It has a **lot** of async I/O and structured request/response models.

## Decision

FastAPI on uvicorn. Single-process async event loop per worker. Pydantic v2
models on every input + output with `extra='forbid'` and bounded `constr`
fields. `Depends(verify_api_key)` attaches auth uniformly.

Module-level lazy singletons via `__getattr__` (`db`, `router`, `auditor`,
`orchestrator`) keep cold-start under 250 ms despite heavy dependencies
(pandas, google.genai, playwright). Heavy imports happen on first attribute
access, not at module load.

## Consequences

**Positive:**
- Native `async`/`await` — no `sync_to_async` wrappers, no thread-pool
  exhaustion. The orchestrator can hold 50+ awaiting requests without
  burning threads.
- Pydantic v2 strict validation gives field-level error responses without
  hand-rolled serializers. `RequestValidationError` is gated behind the
  X-API-Key check so anon callers don't see the 422 schema (see CLAUDE.md →
  "API Security" → `_validation_with_authz_check`).
- `Literal` types on `ExecutableTask` enum, `constr` with `max_length` on
  every string. The `tests/test_pydantic_models_meta.py` auto-discovery
  enforces these conventions on every new BaseModel.
- OpenAPI doc generation is automatic — gated behind `ENABLE_DOCS=true` in
  dev only, never in prod.
- Cold start ~219 ms (down from 1.14 s pre-lazy-imports); cheap for Render
  free-tier wakeups.

**Negative / trade-offs:**
- Django's auth + admin + form + ORM ecosystem are unavailable. We rolled
  our own X-API-Key check, our own Supabase migration tooling, and use
  Supabase Studio as the admin UI.
- FastAPI's third-party ecosystem is smaller. Every integration is
  hand-wired.
- Single-process model. Scaling vertically uses `--workers N`; each worker
  owns its own `_StatsCache` instance, so at N workers you pay N cache
  rebuilds per TTL.
- `supabase-py` is sync, so hot-path reads (`list_leads_recent`,
  `get_stats_rows`, `find_running_job`, `insert_orchestration_job`) need
  `asyncio.to_thread` wrappers explicitly. New hot paths need the wrapper
  added by hand.

## Alternatives considered

- **Django + DRF + Channels**: rejected for the async story. The pipeline
  is 70% async I/O; living inside `sync_to_async` for every Supabase call
  is a slower path with worse failure modes.
- **Flask + extensions**: rejected. Reaching Pydantic-quality validation +
  OpenAPI + async on Flask is several plugins; FastAPI ships it.
- **Starlette directly**: too low-level. FastAPI's `Depends` + Pydantic
  integration is the productivity layer we wanted.

## References

- `backend/main.py` (all route definitions)
- `backend/main.py::__getattr__` (lazy singletons)
- CLAUDE.md → "Performance + observability invariants" → "Cold-start lazy
  imports"
- `tests/test_pydantic_models_meta.py`
