# Full-App Audit — Bugs Found & Fixed

Date: 2026-05-12. Pytest: 99→101 passed. Next build: clean (was 2 warnings).

## Fixed

1. **`src/processors/google_maps.py:65` — Pandas dtype TypeError**
   `df.loc[:, 'Rating'] = pd.to_numeric(...)` failed on pandas 3.0: column had `str` dtype from prior `.astype(str)` call, `.loc` can't change dtype. Switched to `df['Rating'] = pd.to_numeric(...)` which replaces dtype. Same fix for Reviews and all other columns. `test_basic.py::test_gmaps_processing` now passes.

2. **`tests/test_scaling.py` — async test ran without `pytest-asyncio`**
   pytest 9 skipped it with "async def functions are not natively supported". Renamed coroutine to `_scaling_logic`, added sync wrapper `test_scaling_logic` that calls `asyncio.run`.

3. **`pytest.ini` — dead asyncio config emitted warnings**
   `asyncio_mode = auto` / `asyncio_default_fixture_loop_scope = function` were unknown options (no pytest-asyncio installed). Removed.

4. **`backend/main.py` — `lifespan` misleadingly logged "schema is up to date" when DB unreachable**
   `db.check_schema()` returns `[]` on connectivity errors (intentional, tested). Lifespan then logged "up to date" even though no check happened. Moved that log after `recover_interrupted_jobs()` — if DB is dead, that throws and the outer except hits the truthful "Startup DB checks skipped" branch.

5. **`backend/main.py` — uncaught exceptions returned `text/plain` 500s, broke frontend JSON parsing**
   Frontend `await response.json()` threw SyntaxError on `/orchestrator/start` etc. Added FastAPI `@app.exception_handler(Exception)` returning `{"error": "Internal server error"}` JSON.

6. **`frontend/middleware.ts` — Next 16 deprecation warning**
   Renamed `middleware.ts` → `proxy.ts`, function `middleware` → `proxy`. Build warning gone.

7. **Recharts SSR `width(-1)`/`height(-1)` warning at static-gen time**
   `<ResponsiveContainer width="100%" height="100%">` inside a fixed-height wrapper caused size measurement of -1 during SSR. Changed three call sites (HealthChart, two on insights page) to explicit numeric `height={240|300}` and dropped the redundant wrapper height.

8. **`frontend/app/page.tsx:1204` — Settings modal hardcoded "Database: Supabase (Connected)"**
   Lied when DB unreachable. Dropped the "(Connected)" tag (the truthful labelling — connection status isn't actively probed).

9. **`frontend/app/insights/page.tsx` — Recharts unused `entry` param + dropped unused `BarChart3` import**
   Eslint warnings.

10. **`frontend/app/components/AIChat.tsx:93` — unused `err` in catch**
    Eslint warning.

11. **`frontend/app/page.tsx:285` — dead `eslint-disable react-hooks/exhaustive-deps` directive**
    Eslint warning (rule had nothing to disable).

12. **`tests/test_cherry_picks.py::test_wildcard_guard_code_exists` — stale assertion**
    Expected old "disable credentials on wildcard" guard, but `main.py` was tightened to *strip* wildcard origins entirely (stricter). Updated assertion to match the new contract (`origin != "*"`).

13. **`frontend/app/campaigns/page.tsx:207` — misleading comment "Create Campaign Modal"**
    Rendered as inline `<div className="card">`, not a dialog. Removed the misleading comment; the form is intentionally inline so no dialog semantics needed.

## Open (low priority)

- **AI chat floats over content on `/insights` when data empty.** The fixed-position chat panel (`position: fixed`) sits at `bottom: 2rem` over the chart cards; with no data the cards are short and the chat visually overlaps. Once data renders the cards are taller and the chat sits below — but with empty state it's visually messy. Either give the page extra bottom-padding equal to chat height, or auto-minimize the chat on empty pages.
- **Chart layout with real data unverified.** The Recharts wrapper change (numeric `height={...}` instead of percentage) was verified to silence the SSR warning and render fine in an empty state; the layout was *not* visually tested with populated data because the local `.env` has placeholder Supabase creds.

## Linter / formatter-driven rewrites during this session

Several files were rewritten in-place by a hook between edits — not just style, but behavior:

- `frontend/app/insights/page.tsx` — switched from direct Supabase client (`supabase.from('leads').select(...)`) to backend-proxy call (`apiFetch('/leads')`). Architecturally consistent with the rest of the app and avoids exposing Supabase keys, but **loses any realtime subscription** the prior code may have had. Verify this matches your intent.
- `backend/main.py` — gained `slowapi` rate limiting, `X-Admin-Token` header, stricter CORS that strips wildcards entirely, `FileResponse` import. The new file matches the architecture described in `CLAUDE.md` (which was also rewritten by a hook during the session). `slowapi==0.1.9` added to `requirements.txt`.
- `frontend/app/components/AIChat.tsx` — added window-resize listener for mobile/tablet positioning. Pure UI enhancement.

Run `git diff HEAD` to see the full delta before committing.

## Not bugs (verified-as-intended)

- `503` / `500` JSON responses from `/leads`, `/insights`, `/campaigns` when Supabase URL is unreachable — backend reports the failure cleanly; frontend renders empty states gracefully.
- AI Chat error toast when Gemini key absent — user-visible error message, no crash.
- Lucide-react icon deprecation diagnostics (`Cell`, `Facebook`, `Instagram`, `Linkedin`, `FormEvent`) — third-party API drift in newer lucide; non-blocking, no functional impact.

## Not exercised (DB-dependent)

These flows compile and route correctly but cannot be end-to-end tested without a live Supabase + Gemini env (the local `.env` has placeholder values, so DNS resolves to "nodename not known"):

- Lead CRUD via `/leads`, `/upload`
- Campaign create / generate / start / pause
- AI Orchestrate pipeline run
- Discovery engine (Playwright)
- SEO audit
- Email/LinkedIn outreach drafting
