# Backend module dossier

Per-file responsibility map for `backend/` + `src/`. Sourced from CLAUDE.md 2026-05-29 slim.

- `backend/main.py` — FastAPI app. Lazy module-level singletons (`db`, `router`, `auditor`, `orchestrator`) via module `__getattr__` so heavy chains don't fire at import. **PEP 562 caveat**: `__getattr__` doesn't fire for bare-name `LOAD_GLOBAL` inside same-module functions. Lifespan attribute-accesses each name via `sys.modules[__name__]` to populate `globals()`. See "Cold-start lazy imports" in `docs/architecture/performance.md`.
- `src/utils/supabase_helper.py` — Supabase wrapper (`SUPABASE_SERVICE_ROLE_KEY`). Hot-path reads `asyncio.to_thread`-wrapped.
- `src/utils/stats_cache.py` — 60s TTL + `asyncio.Lock` stampede guard. Per-worker singleton.
- `src/utils/query_profiler.py` — Dev-only, env-gated (`QUERY_PROFILER=1`). `assert_o1(per_unit=N)` for N+1 guards.
- `src/scrapers/seo_audit.py` — Async SEO auditor (aiohttp, no Playwright).
- `src/scrapers/discovery_engine.py` — Google Maps via Playwright. Full invariants: [`./discovery-engine.md`](./discovery-engine.md).
- `src/scrapers/enrichment_engine.py` — Shared-Chromium pool, per-lead `new_context()`. `aclose()` MUST run on teardown.
- `src/core/task_orchestrator.py` — Background jobs. `_process_in_chunks` `finally` calls `enricher.aclose()` + `stats_cache.invalidate()`.
- `src/core/agentic_router.py` — AI instruction routing. Full invariants: [`./ai-router.md`](./ai-router.md).
