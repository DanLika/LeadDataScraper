# Full-App Audit — Bugs Found & Fixed

Date: 2026-05-12. Pytest: 99→101 passed. Next build: clean (was 2 warnings).

## Round 4 — CSV import E2E (2026-05-21)

Browser-driven CSV upload through `/upload` → `process_csv_background` →
`SupabaseHelper.upsert_leads`. Two bugs surfaced.

A. **`backend/main.py:_apply_ai_mapping` + `src/utils/csv_helper.py`
   produce duplicate target column names → silent data loss**
   - Input CSV headers: `Business Name`, `Web Address`, `Mail`, `Phone
     Number`, `Notes` (intentionally non-canonical to exercise the AI
     mapper).
   - csv_helper renames `business_name` → `company_name` and ensures
     essential cols `Name`, `Website`, `email`, `unique_key` exist
     (creates empty columns if missing).
   - backend lowercases columns, then calls `GeminiMapper.get_column_mapping`.
     The AI returns:
     ```
     {'business_name': 'company_name', 'web_address': 'website',
      'mail': 'email', 'phone_number': 'phone',
      'name': 'name', 'website': 'website', 'email': 'email'}
     ```
     The last three entries are AI hallucinated no-op identity maps for
     columns the CSV doesn't have, but they collide with the empty
     placeholder columns csv_helper just created.
   - `df.rename(columns=mapping)` produces a DataFrame with multiple
     columns named `website`, `email`. Pandas warns
     `UserWarning: DataFrame columns are not unique, some columns will
     be omitted.` and the populated source values are dropped in favour
     of the empty placeholders.
   - **Backend log**: `Upserting 3 leads with columns: ['company_name',
     'website', 'website', 'email', 'email', 'phone', 'name', 'website',
     'website', 'email', 'email', 'unique_key']` — six duplicates total.
   - **Live result**: 3 leads land in Supabase, but their `name`,
     `website`, `email`, `lead_source` columns are all `NULL`. Only
     `company_name` and `phone` survive. The UI shows the company name
     in the lead row (the inventory falls back from `name` to
     `company_name`), so the user **doesn't see the data loss**.
   - **Fix sketch**: filter AI mapping to keys that actually exist in
     the dataframe AND drop self-maps (`v == k` where the original
     column wasn't already canonicalised); deduplicate target names by
     preferring the populated source over the empty placeholder. Add a
     `df = df.loc[:, ~df.columns.duplicated()]` guard before upsert with
     a `logger.warning` listing the dropped columns so the failure is
     loud, not silent.

B. **Malformed CSV row crashes parser → 0 rows imported instead of
   partial recovery**
   - CSV row containing unquoted comma inside parentheses
     (e.g. `=HYPERLINK("http://evil/csv","sneaky")` rendered as the
     first cell without enclosing the cell in `"..."`) makes
     pandas `read_csv` raise `ParserError: Expected 5 fields in line 4,
     saw 6`.
   - `src/utils/csv_helper.load_csv_with_unique_key` catches that with
     `except (pd.errors.EmptyDataError, pd.errors.ParserError)` and
     falls back to an EMPTY DataFrame with only the essential headers.
     **All N valid rows that came before the malformed one are lost.**
   - Downstream: `Upserting 0 leads...` then a misleading PGRST100
     error from supabase-py because the columns parameter ends up
     empty.
   - **Frontend signal**: the UI just shows the import button return to
     idle and TOTAL LEADS = 0. No toast, no error banner — the user
     thinks the file was rejected.
   - **Fix sketch**: switch to `pd.read_csv(..., on_bad_lines='skip')`
     (or `'warn'` to log each skipped row) so good rows survive.
     Pre-flight check the column count of every line and surface a
     row-level error in the toast ("3 of 4 rows imported; row 4
     malformed — skipped"). Short-circuit the upsert if the dataframe
     ends up empty and log that as a distinct error path, not a
     supabase PGRST100.

Both bugs are pre-existing in `main`; uncovered by the E2E CSV import
test. Not security-critical (no privilege escalation, no XSS), but
both cause silent data loss / user confusion. Worth a follow-up commit.

## Round 3 — E2E verification during /security-audit:run (2026-05-21)

End-to-end browser test of AI execution + Playwright crawl via chrome-devtools
MCP. Created throw-away Supabase Auth user `claude-audit-test@example.com`,
logged in via the Server Action path, exercised the full pipeline, deleted
the test user + scraped rows afterwards.

**PASS** (no regression vs prior round):
- Login Server Action → httpOnly cookie set, dashboard reachable
- AI chat status query `"How many leads are in the database?"`
  → `STATUS_CHECK` autoexec → `"0 leads total."` ✓
- AI chat action prompt `"Find me 3 dentists in Mostar"`
  → `DISCOVERY_SEARCH` plan card with Confirm & Execute ✓
- Confirm & Execute → `/execute` 200 → orchestrator job_id ✓
- Playwright Chromium → Google Maps query → 16 result containers → 8
  deduplicated leads in 35s ✓
- `SupabaseHelper.upsert_leads`: `"Upserted 8/8 leads to Supabase"` ✓
- Frontend live-refresh: Pipeline Intelligence stats `8 / 8 / 0 / 0`
  (Total / Pending / High Risk / Healthy) populated within poll window ✓
- Backend log: zero exceptions

**Found**:

A. **`src/scrapers/discovery_engine.py` — `_extract_lead_data` returned no
   `lead_source` or `address` (FULL FIX 2026-05-21)**
   The dict shipped to `SupabaseHelper.upsert_leads` originally only set
   `name, unique_key, website, phone, rating, audit_status`. Live
   verification confirmed both `leads.lead_source` and `leads.address`
   columns were `NULL` on every Google-Maps-discovered row. Two concrete
   consequences:
   - Provenance was lost: there was no way to query "which leads came from
     Google Maps vs CSV import vs hand entry" — `lead_source` is the
     contract for that and it was never written.
   - Test-data cleanup workarounds: the cleanup query
     `DELETE FROM leads WHERE lead_source = 'google_maps' AND address
     ILIKE '%Mostar%'` matched zero rows during this audit. Had to fall
     back to `created_at` timestamp matching.
   **Both fixed**:
   1. `lead_source: "google_maps"` set unconditionally in the returned
      dict.
   2. New `_extract_address(page, container)` staticmethod pulls the
      address from the Maps side panel. Tries `button[data-item-id=
      'address']` first, then `button[aria-label^='Address:']`, then
      `[data-tooltip='Copy address']`. If none are present (panel
      closed), clicks the result card to open the panel and re-queries.
      Prefers the `aria-label` (formatted `"Address: 123 Main St, City"`)
      and falls back to `inner_text()`. The Maps icon glyph that
      precedes inner_text is collapsed via `re.sub(r"\s+", " ", ...)` +
      a `re.search(r"[\w].*")` trim. Live-verified on a bookstore /
      cafe search in Sarajevo + Tuzla — every returned lead carried a
      clean Bosnian street address.

## Round 2 — Fixed (2026-05-12 second pass)

A. **`backend/main.py:116` — `/docs`, `/openapi.json`, `/redoc` publicly readable**
   Per CLAUDE.md only `/` is meant to be public, but FastAPI's default Swagger UI was exposing the full API surface. Now gated by `ENABLE_DOCS=true`; off by default. Set env in dev to restore.

B. **`backend/main.py` `verify_api_key`/`verify_admin_token` — plain `!=` compare**
   Vulnerable to timing attack on secret length/prefix. Switched to `secrets.compare_digest`. Both API key and admin token paths now constant-time.

C. **`backend/main.py:208` — `/upload` read entire body before size check**
   `await file.read()` buffers the full payload (up to whatever client sends) before validation rejects >50MB. DoS vector. Replaced with streamed `read_capped()` that aborts at 50MB and returns 413.

D. **`backend/main.py:131` — `Limiter(headers_enabled=True)` crashed every rate-limited success path**
   slowapi tried to inject `X-RateLimit-*` headers but required the endpoint to declare `response: Response` — none did. Any successful `/upload`, `/leads`, `/stats`, etc. returned 500. Flipped to `headers_enabled=False`. No frontend consumes those headers; verified via grep.

E. **`frontend/app/api/proxy/[...path]/route.ts` — forwarded client-controlled XFF**
   Client could send `X-Forwarded-For: spoof` and bypass per-IP rate limits at the backend. Now strips XFF / X-Real-IP / Forwarded from the incoming request and re-emits XFF only from Vercel's `x-vercel-forwarded-for` (edge-set, unforgeable). On non-Vercel deploys (e.g. Render-only), backend collapses to a single proxy-IP bucket — acceptable trade.

F. **`requirements.txt` — `slowapi` missing locally**
   Already pinned in requirements but not installed in dev environment; reinstalled. Tests now run from a fresh checkout.

G. **`frontend/app/api/proxy/[...path]/route.ts:58` — unused `err` in catch**
   ESLint warning. Removed binding.

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
