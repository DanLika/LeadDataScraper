# Session 2026-05-23 — dogfood prep (PRs #243 #247 #249)

Extracted from `CLAUDE.md` (2026-05-26 shrink; original ~164k chars). Restored to docs/ to keep CLAUDE.md under the harness threshold without losing content.

# Session 2026-05-23 — dogfood prep (PRs #243 / #247 / #249)

Three parallel PRs landed dogfood infrastructure: email-stack
planning docs, a demo-data column + seed pipeline, and a cookie-only
next-intl scaffold. None auto-applies on merge — wiring follow-ups
are gated on operator setup (DNS, native-speaker review,
screenshot-quality decisions).

## Demo data infrastructure (PR #247)

- **Live schema change** (applied via Supabase MCP
  `apply_migration` named `add_leads_is_demo_column` on project
  `kbtkxpvchmunwjykbeht`):
  ```sql
  ALTER TABLE public.leads
    ADD COLUMN IF NOT EXISTS is_demo BOOLEAN NOT NULL DEFAULT FALSE;
  CREATE INDEX IF NOT EXISTS idx_leads_is_demo
    ON public.leads(is_demo)
    WHERE is_demo = TRUE;
  ```
  Partial index — production rows default FALSE and never enter the
  index → writes stay cheap. Mirrored in `supabase_schema.sql`;
  `schema_drift_check.py` picks up the column automatically via
  `parse_expected_columns` (allowlist is name-only, derived from
  CREATE/ALTER TABLE parsing). Reversible down script kept inline
  as a comment.
- `supabase_helper.delete_demo_leads()` uses the real boolean
  predicate `eq("is_demo", True)` — no sentinel needed (unlike
  `delete_all_*` which need a tautology since PostgREST refuses
  naked DELETE). Hits `idx_leads_is_demo`.
- **`DELETE /leads/clear-demo`** mirrors `/leads/clear` template
  exactly — dual-gate (`verify_api_key` + `verify_admin_token`) +
  `@limiter.limit("3/hour")`. Orchestration jobs untouched (demo
  seeds never spawn jobs). Proxy `ADMIN_TOKEN_PATHS` set extended
  to `{'leads/clear', 'leads/clear-demo'}` so X-Admin-Token
  injects on both paths.
- `src/scripts/seed_demo_data.py` — 20 fictional Croatian
  businesses across 4 segments (vacation rental / restaurant /
  dental / fitness) × 4-5 cities (Zagreb / Split / Dubrovnik /
  Rijeka / Plitvice). Stable `_demo_<slug>` unique_keys so re-runs
  ON CONFLICT cleanly. `is_demo=True`, `lead_source='_demo_'`
  double-marked. Run:
  ```
  SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... \
    python -m src.scripts.seed_demo_data
  ```
- **Demo flow integrity**: `GET /leads` via `list_leads_recent`
  uses `.select("*")` so `is_demo` flows to the frontend. Single-
  row mutation paths also use `.select("*")` so refreshed rows
  carry the flag. Any future endpoint that selects explicit
  columns must include `is_demo` or the toggle silently breaks.
- Frontend `FilterBar` "Hide demo data" checkbox. Operator-facing
  state in `localStorage['ldsHideDemoData']` ('0' or '1'); default
  TRUE so seeded rows hide once operator has real leads. Demo
  gate runs FIRST in `filteredLeads` so audited/high-risk view
  counts stay honest under the toggle.
- Settings → Danger Zone: "Remove all demo data" button above the
  existing "Clear All Leads" (same dual-gate, same admin-token
  injection on the proxy side).
- **Cosmetic gap (deliberate, not a bug):** seeded rows have no
  `audit_results` / `seo_score` / `outreach_score`. Dashboard reads
  them as `Pending` — realistic for "freshly imported" but half-
  populated for screenshot/onboarding purposes. If onboarding
  screenshots need richer visuals, add varied scores in a follow-up
  to `seed_demo_data.py::DEMO_LEADS`.

## Cookie-only next-intl scaffold (PR #249)

- **Chose cookie-only over route-group + middleware composition.**
  bookbed-website uses route-group (`app/[locale]/`) + simple
  `createMiddleware(routing)`; LDS doesn't because (1) internal
  tooling, no public/SEO URLs to preserve, (2) `frontend/proxy.ts`
  already owns CSP-nonce + Supabase auth gate and composing a
  second top-level next-intl middleware is half-day work for no
  operator-facing benefit, (3) operator switches locale once a
  session at most. Upgrade path to route-group documented in
  `frontend/i18n/routing.ts` header — flip later if SEO ever
  matters.
- `frontend/i18n/routing.ts` — `locales=['en','hr']`,
  `defaultLocale='en'`, `LOCALE_COOKIE='NEXT_LOCALE'`,
  `isLocale()` guard.
- `frontend/i18n/request.ts` — `getRequestConfig()` reads cookie
  via `next/headers.cookies()`, falls back to `defaultLocale` on
  missing/unknown. Dynamic import per locale so only the matching
  messages JSON ships per request.
- `frontend/next.config.ts` — composes
  `withNextIntl(withSentryConfig(...))`. next-intl plugin wraps
  Sentry's wrap (outermost). Plugin order verified by `next build`
  (Turbopack) clean.
- `frontend/app/layout.tsx` — `getLocale()` + `getMessages()` on
  the server, `<html lang={locale}>`, `<NextIntlClientProvider>`
  wraps children. The existing `dynamic = 'force-dynamic'` (in
  place for the CSP-nonce pipeline) ensures cookie reads aren't
  statically cached.
- `frontend/messages/{en,hr}.json` — 50+ strings across 8
  namespaces (`nav` / `common` / `dashboard` / `leadTable` /
  `actions` / `settings` / `login` / `localeSwitcher`).
  **`hr.json` carries machine-quality translations** — operator
  must skim before any external user touches the app.
- **PARITY GOTCHA:** `hr.json` has a `_meta` namespace
  (`{note: "review needed"}`) that `en.json` doesn't. next-intl
  ignores unknown keys today, so harmless — but a future parity-
  checking linter would trip. Move to `messages/_meta.json` (or
  delete the namespace) if/when such a linter lands.
- `frontend/app/components/LocaleSwitcher.tsx` — client `<select>`,
  sets NEXT_LOCALE cookie (Max-Age=1y, SameSite=Lax, Secure in
  prod) + `router.refresh()` so RSC re-renders without losing
  modal/scroll state.
- `frontend/app/components/Sidebar.tsx` — `useTranslations('nav')`
  wired for Dashboard / Insights / Settings / Sign Out. Mounted
  LocaleSwitcher below nav (only when sidebar expanded). Remaining
  ~150 visible strings (`FilterBar` options, `LeadTable` headers,
  action buttons, modals, page titles, `login/page.tsx`,
  `insights/page.tsx`, `campaigns/page.tsx`) are still inline —
  incremental extraction follow-up.

## Email stack plan (PR #243, no wiring yet)

`docs/email-deliverability.md` is the operator runbook for
authenticated email send. `docs/email-dispatch-architecture.md` is
the 5-PR wiring plan. **Both ship with `Do NOT wire` gating clearly
stated** — the wiring PRs only become safe when the deliverability
checklist (DNS green, Resend account live, mail-tester 10/10,
Gmail/Outlook seed inbox tests pass) is 100% complete.

Key decisions locked in by the docs:
- Sending domain: **`mail.leaddatascraper.com`** (NEW — operator
  registers `leaddatascraper.com` first, ~$10–15/yr at Cloudflare).
  Subdomain isolates outreach reputation from any future
  marketing/transactional sends on the root.
- Provider: **Resend EU region**. BookBed already uses Resend (one
  account); $20/mo Pro = 50k/mo.
- **DMARC ramp**: `p=none` (2 weeks RUA-only) →
  `p=quarantine pct=25→50→100` → `p=reject`. Aggressive day-1
  policies silently drop legit forwarders (Google Groups, internal
  mailing lists). RUA reports to `dmarc@leaddatascraper.com`
  (operator-controlled mailbox required; Cloudflare Email Routing
  free forward works).
- **DKIM**: 3 CNAMEs Resend generates per account. Doc points at
  Resend dashboard for the real values — embedded placeholders
  would be wrong and tempt copy-paste.
- **No MX** on `mail.` subdomain — outbound-only; replies go to
  operator's real inbox via `Reply-To` header.

Wiring-PR sequence (`docs/email-dispatch-architecture.md` §4):
1. `ResendEmailSender(EmailSenderBase)` HTTP API client (NOT SMTP
   — SMTP loses webhooks → `campaign_messages` state machine stays
   stuck at `pending` forever).
2. Schema additions (`campaign_messages.provider_message_id`,
   `bounce_reason`; new tables `email_send_ledger`,
   `email_suppression`) + `schema_drift_check.py` +
   `check_grants_matrix.py` allowlist updates.
3. `POST /webhooks/resend` with Svix-Signature HMAC verify
   (`secrets.compare_digest` against `RESEND_WEBHOOK_SECRET`) +
   replay-window check (`Svix-Timestamp` ±5 min) + Pydantic-Literal
   event model. Updates `campaign_messages.status` by
   `provider_message_id`; suppression-list inserts on bounce /
   complaint.
4. **Render Cron** dispatcher (NOT uvicorn-lifecycle loop, NOT
   pg_cron — Cron is observable, decoupled from worker lifecycle,
   avoids multi-worker leader-election). Per-domain throttle 3/hr +
   per-day global cap 50 + 09:00–18:00 Europe/Sarajevo window
   (cold outreach landing at 03:00 local recipient time signals
   automation; 10:00 local signals human).
5. `/campaigns/{id}/send` operator endpoint + UI "Send Now" button
   (X-Admin-Token gate) + suppression-list view in Settings.

## Branch hygiene reinforcement

The parallel-session branch-confusion gotcha (already documented in
Session 2026-05-22 notes) hit **twice** in this session — once
after C.3 committed onto `docs/crossover-spot-checks` instead of
`chore/demo-data-seed-13.3`, once after C.2 committed onto
`docs/crossover-verification-2026-05-23` instead of
`chore/i18n-scaffold-13.1`. Recovery via cherry-pick + reset (and
once `git rebase --onto origin/main <parallel-session-tip>`) worked
both times. **Cheap-insurance mitigation for future sessions:**
right after `git checkout -b <new> origin/main` and BEFORE editing
any file, run `git symbolic-ref HEAD` and verify the output ends in
`refs/heads/<new>`. If the parallel session has swapped HEAD, the
output names a different branch and you need to re-checkout. ~3
seconds, catches the gotcha at point-of-no-loss.

