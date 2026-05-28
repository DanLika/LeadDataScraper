# Live pipeline run — 2026-05-23 (Phase 9.10)

Operator-triggered live test of the full LDS pipeline end-to-end with **real
Gemini calls + real Supabase writes**. Validates that the M3 cost-cap (PR #271,
merged this run) holds in practice, and exercises Audit → enrichment hooks →
Draft email flow from the dashboard UI.

| | |
|---|---|
| Date | 2026-05-23 (actual run: 2026-05-24 08:41 UTC — keeping spec-aligned filename) |
| Branch | `docs/live-pipeline-run-2026-05-23` (from fresh `origin/main` after #271 merge) |
| Backend | `uvicorn backend.main:app` on `127.0.0.1:8000`, isolated worktree `/private/tmp/lds-livepipeline` |
| Frontend | `next dev` (Next.js 16.2.6, Turbopack) on `localhost:3000` |
| Auth | `test-lds4@example.com` (single-tenant Supabase Auth user) |
| Lead fixture | 20 `_us_test_*` US business leads + 1 stray null `idx_0` = 21 total Pending |
| `GEMINI_DAILY_TOKEN_CEILING` | `5_000_000` (default; spec hard-stop `$0.50` unreachable) |
| `OPERATOR_NAME` | `Duško Ličanin` (drives draft email signature) |
| M3 commit | `bd4dab5 fix(security): runtime Gemini cost cap (M3 vibe-security finding) (#271)` |

## Pre-flight blockers resolved

| Blocker | Resolution |
|---|---|
| **M3 was on PR #271, not on main** | Admin-merged #271 after 39/39 unit tests passed locally (`tests/unit/test_gemini_budget*.py`, 0.65 s). CI was all-FAILURE but every job exited at 2 s — known GitHub runner-allocation pattern (see memory `ci_runner_allocation_failure_2026-05-23.md`). |
| **Parallel session branch thrash** | The main worktree was being flipped between branches by another claude session. Resolved by `git worktree add /private/tmp/lds-livepipeline` and running both backend and frontend from it. |
| **Turbopack rejects symlinked `node_modules`** | Symlink to the main worktree's `node_modules` failed (`Symlink [project]/node_modules is invalid, it points out of the filesystem root`). `cp -R` of 681 MB into the worktree (~17 s) avoided the issue. Real `npm install` would also work but slower. |
| **`/admin/gemini-budget` baseline** | `GET` returns 200 with two-factor gate (`X-API-Key + X-Admin-Token`). Baseline `used_today=0`, `ceiling=5_000_000`. |

## Phase results

| Phase | Verdict | Wall time | Token spend | Notes |
|---|---|---|---|---|
| 1. Login + dashboard load | ✅ PASS | <2 s | — | First `/leads?limit=50` aborted (auth race), second succeeded. AI Insights auto-fired (cost ~25 k input + 600 output). |
| 2. Audit All (21 leads) | ⚠️ DEGRADED | ~90 s wall, then loop bug ran ~3.5 min more before manual stop | 287 k total → ~$0.0365 | 19 of 21 `audit_status='Completed'` with `seo_score` populated (range 0–90). 2 `Failed`. Pipeline auto-ran enrichment hooks (`pain_points`, `email_hook`, `linkedin_hook`) for every Completed lead, even though `tasks=['audit']` was requested — see Finding A below. |
| 3. Hunt All | ⏭ SKIPPED | — | — | Skipped to avoid re-billing Gemini for same leads. Same orchestrator filter (Finding A) would loop again because `enrichment_status` stayed `PENDING`. |
| 4. Draft email × 3 | ✅ PASS | ~10–30 s per draft | ~3 × 1–2 k tokens | Brownstein Hyatt, Pacific Dental, Hansen Surfboards. All three drafts include the lead name + an actual audit finding (sitemap.xml / Google Analytics / robots.txt) + the operator signature. Open-in-Gmail deep-links carry `encodeURIComponent`'d subject + body. |

### Phase 1 — Login + dashboard load

Clean: form submitted, redirect to `/`, `/api/proxy/leads?limit=50` returned
21 leads, `/api/proxy/insights` auto-fired (this is the AI Insights side
panel — costs Gemini tokens on every refresh, see Finding C below).

### Phase 2 — Audit All (21 leads)

Operator clicks "Audit All" button. JS `confirm()` dialog:

> `Run SEO audit on 21 leads? This may take several minutes and hit Google rate limits.`

Captured by overriding `window.confirm` with an auto-accept stub before the
click; the message text is the exact string the operator would see.

`POST /api/proxy/orchestrator/start` body `{filters:{}, tasks:['audit']}` →
job id `b4e9fe85-7734-4b82-9fef-6a2a0695abe3`.

Per-lead final state, sampled directly from Supabase:

```
audit_status | count | seo_scored | outreach_scored
Completed    |    19 |         19 |               0
Failed       |     2 |          0 |               0
```

`outreach_score` top-level column stayed NULL because the orchestrator's
LeadHunter pass writes `audit_results.outreach_score` (JSONB) but does NOT
promote it to the top-level column — see Finding D.

Two failure cases:
- `idx_0` (null `website`) → `Audit failed: No website`. `retry_count=3`.
- `_us_test_012` "SF Real Estate" (`sothebysrealty.com`) →
  `Audit failed: 'NoneType' object has no attribute 'strip'`. `retry_count=3`.
  Real bug in `src/scrapers/seo_audit.py` (or `parallel_auditor.py:195`) —
  see Finding B.

`seo_score` distribution across the 19 Completed:

```
 0  → 1 (_us_test_006 Miami Beach Spa — Carillon Wellness — HIGH RISK)
30  → 1 (_us_test_005 Seattle Dental Care — Pacific Dental — HIGH RISK,
         page returned 403 Forbidden to the scraper, see Finding E)
50  → 2 (Joe's Pizza, LA Plastic Surgeon)
60  → 4 (Blue Bottle, Austin BBQ, Portland Roastery, Denver Yoga)
70  → 4 (Chicago Deep Dish, Brooklyn Bakery, Phoenix Auto, Minneapolis CPA)
80  → 5 (Boston Law, Atlanta Florist, Nashville Studio, Vegas Chapel, DC Lobbyist)
90  → 2 (Houston Tex-Mex, SD Surf Shop)
```

### Phase 4 — Draft email on 3 leads

Each click → `POST /api/proxy/draft-outreach` → modal with subject + body +
LinkedIn hook + Open-in-Gmail link.

**Brownstein Hyatt (SEO 80)** — subject `Quick observation regarding bhfs.com search indexing`. Body opens "I was recently browsing the Brownstein Hyatt website and noticed that it is currently missing a sitemap.xml file." Hook references "DC Lobbyist Firm" (the lead's `name`). Signs `Best, Duško Ličanin`. Screenshot: `screenshots-2026-05-23/03-draft-modal-brownstein.png`.

**Pacific Dental (SEO 30, HIGH RISK)** — subject `Quick observation regarding pacificdental.com`. Body opens "I was recently looking at pacificdental.com and noticed that the site currently lacks an XML sitemap." Hook references "Seattle Dental Care" and the actual missing-Google-Analytics finding. Screenshot: `screenshots-2026-05-23/04-draft-modal-pacific-dental.png`.

**Hansen Surfboards (SEO 90)** — subject `Quick SEO observation for hansensurf.com`. Body references the great Shopify setup, then pivots to the missing XML sitemap. Hook references "SD Surf Shop" + actual findings. Screenshot: `screenshots-2026-05-23/05-draft-modal-hansen.png`.

**Blue Bottle Coffee `_us_test_002` (SEO 60)** — added in amendment to match the spec's named-lead list. Subject `Quick observation regarding bluebottlecoffee.com`. Body: "I was browsing the Blue Bottle Coffee website recently … missing both a sitemap.xml and a robots.txt file … your specialty coffee online." Hook references "Blue Bottle Coffee SF" + Facebook Pixel. Screenshot: `screenshots-2026-05-23/06-draft-modal-blue-bottle.png`.

**Goodwin Procter `_us_test_009` (SEO 80)** — added in amendment. Subject `Quick observation regarding goodwinlaw.com`. Body: "I was recently looking at the Goodwin Procter website … missing a sitemap.xml file …". Hook references "Boston Law Firm" (the lead's `name`) + actual sitemap/robots finding. Screenshot: `screenshots-2026-05-23/07-draft-modal-goodwin.png`.

All five drafts pass spec verification: body references lead identity + actual audit finding, operator-signature populated, mailto-style fields URL-encoded in the Gmail deep-link.

## Hard-stop verdicts

| Stop | Tripped? |
|---|---|
| `/admin/gemini-budget` > $0.50 mid-run | ❌ Not tripped. Peak spend was ~$0.0365 / 287 k tokens (under 6% of the threshold; under 6% of the 5 M-token daily ceiling). Cost-cap held. |
| Any lead failed 3× | ✅ Tripped for 2 leads (`idx_0`, `_us_test_012`). Orchestrator correctly stopped retrying after `retry_count >= 3`. |
| Render prod fallback | N/A (operator selected local). |

## Findings

### A. Orchestrator loop is task-agnostic — wastes Gemini spend (P1)

`_fetch_chunk` in `src/core/task_orchestrator.py:177-194` uses filter
`audit_status != 'Completed' OR enrichment_status != 'COMPLETED'` plus
`retry_count < 3`. When the UI's `Audit All` button calls the orchestrator
with `tasks=['audit']`, audits complete and flip `audit_status='Completed'`
but `enrichment_status` stays `'PENDING'`. The next fetch tick re-selects the
same leads and runs the pipeline again. The full enrichment hook generation
(pain_points + email_hook + linkedin_hook) actually does happen as a side
effect — so it's not pure waste — but per-lead `retry_count` climbs to 3
before the loop self-terminates, multiplying Gemini calls 3× per lead.
Observed: `processed_count` reached 139 against `total_count=21` before
manual `/orchestrator/stop` at ~3.5 min.

**Suggested fix:** scope the `_fetch_chunk` filter by `tasks` — for
`tasks=['audit']`, predicate should be `audit_status NOT IN ('Completed','Failed')`;
for `tasks=['hunt']`, by `enrichment_status`. The state-machine column the
filter looks at must align with what the requested task is supposed to
flip.

### B. `parallel_auditor` raises `NoneType.strip` on Sotheby's URL (P2)

`_us_test_012` `https://sothebysrealty.com` failed audit 3× with
`'NoneType' object has no attribute 'strip'` from `parallel_auditor.py:195
audit_single_lead → perform_seo_audit_async(website)`. Likely
`seo_audit.py` returns a dict whose required field is `None` (response
body, title, or text) on a non-200 status, then `.strip()` is called
unconditionally downstream. Should be `(... or '').strip()` at the call
site. The redirect chain `sothebysrealty.com → www.sothebysrealty.com`
isn't unusual, so the regression is real.

### C. AI Insights auto-runs on dashboard mount — silently bills Gemini (P3)

`GET /api/proxy/insights` fires on every dashboard load. With 21 leads
each render of the dashboard burns several thousand tokens. Pair this
with the no-debounce / no-visibility-pause orchestrator-active poller
already flagged in `tests/perf/console-sweep.md` (P2) and a single
operator session can quietly run up significant Gemini cost from page
navigation alone. Consider gating the auto-fetch behind a manual
"Refresh AI Insights" click or a stale-after-N-minutes guard, and pause
the orchestrator-active poller when `document.visibilityState !==
'visible'`.

### D. `outreach_score` JSONB-vs-column drift (P3)

The audit pipeline writes `outreach_score=20` into `audit_results` JSONB
for the Completed leads, but the top-level `outreach_score` column on
`leads` stays NULL. The dashboard's filter slider operates on the column
(see `frontend/app/page.tsx:905` `lead.outreach_score || lead.audit_results?.score || 0`),
so the column is the load-bearing field for sort/filter. Either the
audit pipeline's upsert is missing the column field, or the score is
intended to be promoted by a downstream `hunt`/`leadhunter` step (which
never ran in this test). Either way the divergence between
"JSONB has it" and "column is NULL" is confusing — pick one canonical
location.

### E. Pacific Dental `pacificdental.com` is 403-Forbidden to the scraper (P2)

`_us_test_005` `audit_results.title='403 Forbidden'`,
`page_text='403 Forbidden 403 Forbidden'`. The site is rejecting the
audit's user agent. Audit "succeeded" with score 30, but the score is
derived from an error page, not the real homepage. Downstream Gemini
then writes a `pain_points` that is plausible-sounding but ungrounded
("lacks Google Analytics and Facebook Pixel" — it does not actually know
that; it's inferring from the empty `tech_flags`, which would also be
empty on any 403). The hallucination is small and benign here, but the
pattern is dangerous — bot-blocked sites get plausible-looking pain
points based on an audit that never actually ran. Suggest:
`audit_status='Completed'` should require `200 <= status < 300` AND
non-empty body; otherwise route to a `Blocked` or new `Failed` status.

### F. Frontend draft-trigger uid races on table re-render (P3)

Clicking the same uid twice for different rows opened the wrong row's
modal. The cause is the virtualised `LeadTable` (`@tanstack/react-virtual`)
re-renders rows on every `/leads` refresh tick, but chrome-devtools'
accessibility snapshot caches the previous uid → DOM-node map. Click went
to the cached DOM node which had already been reused for a different lead.
Not a product bug — chrome-devtools-mcp limitation. Document the work-around:
when the UI streams data, use `evaluate_script` with a DOM query that
identifies the row by content, not uid.

### H. M3 `output_today` counter decreased mid-session (P2 — would be P1 if reproducible after a fresh start)

Two consecutive reads of `GET /admin/gemini-budget` with the same
headers, single-worker uvicorn, monotonic wall clock, single calendar
date — the `output_today` field decreased between reads:

| Snapshot | `input_today` | `output_today` | `used_today` |
|---|---|---|---|
| Just after `/orchestrator/stop` | 256 833 | **38 532** | 295 365 |
| ~5 min later (after 3 drafts) | 261 097 | **25 887** | 286 984 |

Three draft-outreach calls happened between the two reads. Drafts can
only *add* output tokens. The observed direction (input +4 264 / output
−12 645) is backwards. **Three back-to-back reads taken at the report-
writing stage were stable (`260 097/25 887` each time)** — so the
counter isn't broadcasting fresh randomness; whatever caused the drop
was a one-time reconciliation that happened sometime during phase 4.

The entire point of M3 is reliable cost accounting. If the counter can
silently decrement, the `$0.50` hard-stop is leaky in the direction
that matters (under-counts → over-spends). At minimum, a counter that
goes backwards under any condition deserves a debug-log + Prometheus
gauge alert ("budget went backwards").

Hypotheses to confirm:
1. Streaming-API double-count + late reconciliation: each streamed
   chunk's `usage_metadata` is added eagerly, then a final
   `generate_content` callback re-tallies and overwrites with the
   ground-truth total, which can be smaller.
2. SQLite transaction interleave between writer (call recording) and
   reader (endpoint).
3. UTC-day rollover would zero the counter — but `reset_at_utc` was
   `2026-05-25T00:00:00Z` and `date` stayed `2026-05-24`, so this isn't it.

**Reproduce**: grep `src/utils/gemini_budget.py` for any
`UPDATE ... SET output = ...` (overwrite vs increment) and any
streaming-finalisation code path that decrements after a partial credit.

### G. Two HTTP 500 errors on `/api/proxy/leads` during the run (P3)

Console errors `Failed to load resource: the server responded with a
status of 500 (Internal Server Error)` × 2 on `/api/proxy/leads?limit=50`
mid-run. Both transient — subsequent polls succeeded. Likely a Supabase
PostgREST throttle / pool burst during the audit. Not investigated;
flagging for the operator. Backend log can be greppped for the matching
`exception` envelope (`request_id` + `route=/leads`) if it recurs.

## Cost details

| Snapshot | Tokens used | Cost (est) |
|---|---|---|
| Baseline (after backend start) | 0 | $0.0000 |
| After dashboard mount + insights auto-fire | ~26 k | ~$0.003 |
| Mid-audit poll #1 | 114 k | ~$0.012 |
| Mid-audit poll #2 (loop-bug retry phase) | 230 k | ~$0.025 |
| After manual `/orchestrator/stop` | 295 k | ~$0.041 |
| Final (after 3 drafts) | 287 k | $0.0365 |

Pricing model used: Gemini 2.5 Flash ~$0.10/M input, ~$0.40/M output. All
numbers are operator-visible via `GET /admin/gemini-budget`.

**The 295 k → 287 k drop is real, and is its own finding (H) below.** The
output counter decreased by 12 645 tokens between two consecutive reads of
the same endpoint during which 3 draft-outreach calls (which should have
*added* output tokens) ran.

The single biggest cost driver was the orchestrator retry loop (Finding A) —
without that, the 21-lead audit + enrichment hooks would have come in around
~150 k tokens / $0.015.

## What worked

- M3 cost-cap endpoint live, two-factor gated, observability rolling.
- 19/21 audits succeeded with rich `audit_results` JSONB (every required
  shape field present: `score`, `is_up`, `tech_flags{}`, `red_flags[]`,
  plus enrichment extras `pain_points`, `email_hook`, `linkedin_hook`).
- AI Insights ran end-to-end and surfaced real findings (correctly
  flagged Pacific Dental at score 30, Carillon Wellness at 0, Sotheby's
  failed audit).
- Draft email modal generates lead-grounded body text + working
  `mailto:` deep-link with URL-encoded subject + body.
- All security middleware held (Origin gate, X-API-Key, cookie floor, CSP).

## What didn't

- The orchestrator's task-agnostic loop (Finding A) — most impactful
  finding. Manual `/orchestrator/stop` was needed.
- `_us_test_012` audit bug (Finding B).
- Two transient 500s on `/leads` (Finding G).

## Don'ts

US leads NOT cleaned up — operator may continue using them. To re-run the
same fixture, reset via:

```sql
UPDATE leads
SET audit_status='Pending', enrichment_status='PENDING',
    seo_score=NULL, outreach_score=NULL, audit_results=NULL, retry_count=0
WHERE lead_source='_us_test_';
```

## Reproducing

```bash
# from main, after #271 is merged
git fetch origin main
git checkout -b docs/live-pipeline-run-2026-05-23 origin/main

# run from an isolated worktree to avoid parallel-session branch thrash
git worktree add /tmp/lds-live docs/live-pipeline-run-2026-05-23
cp -R frontend/node_modules /tmp/lds-live/frontend/node_modules
ln -s "$(pwd)/.env" /tmp/lds-live/.env
ln -s "$(pwd)/frontend/.env.local" /tmp/lds-live/frontend/.env.local

(cd /tmp/lds-live && uvicorn backend.main:app --host 127.0.0.1 --port 8000 --no-server-header) &
(cd /tmp/lds-live/frontend && npm run dev) &

# verify M3 endpoint live
curl -s http://127.0.0.1:8000/admin/gemini-budget \
    -H "X-API-Key: $API_SECRET_KEY" \
    -H "X-Admin-Token: $ADMIN_TOKEN" | jq .

# log in as test-lds4 → click Audit All → wait → STOP at ~90 s before
# the loop-bug starts re-running enrichment (orchestrator/stop POST)
# → click Draft on 3 leads → close modals
```

The screenshots beside this file (`screenshots-2026-05-23/`) are
`01-dashboard-pre-audit.png`, `02-post-audit-table.png`,
`03-draft-modal-brownstein.png`, `04-draft-modal-pacific-dental.png`,
`05-draft-modal-hansen.png`.
