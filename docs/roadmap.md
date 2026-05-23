# Roadmap

What's next, in rough priority order. Items marked **[BookBed.io]** are
relevant only if commercializing LeadDataScraper as a hosted product;
those stay parked until the commercialization track is opened.

This file is **not a commitment** — priorities shift. It exists so a
future-self (or contractor) can see what was on the agenda and why
each item is where it is.

---

## Now (next 1–2 weeks)

### Operational hardening
- [ ] **Storage-report tri-state exit codes**. Currently `storage_report.py`
      exits 1 for both 70 % (soft) and 90 % (hard) breaches; the workflow
      grep-distinguishes. Cleaner: exit 0 / 1 / 2 / 3. Reduces grep
      brittleness. (See [`docs/alerting.md`](alerting.md) §storage.)
- [ ] **Schema-drift `EXPECTED_TABLES` update** for the new
      `account_deletions` table. Without this update, the schema-drift
      CI gate is red after the next `supabase_schema.sql` deploy.
- [ ] **RLS deny-all check** in `schema_drift_check.py` for the new
      `account_deletions` table — extend the existing 4-table list.

### Documentation gaps
- [ ] **Frontend Settings → Danger Zone → Delete account button**.
      Backend endpoint + tests shipped; UI button TBD. Mirror the
      "Download my data" pattern at `frontend/app/page.tsx::Settings
      modal`.
- [ ] **CLAUDE.md update**: add the new endpoints + Sentry tagging +
      JSON logging + GDPR work to the security invariant section.

---

## Next (1–2 months)

### Resilience (Gemini SEV-2 reduction)
- [ ] **Circuit breaker around Gemini calls**. Open after N failures
      in M seconds; fail fast for cool-down. See
      [`docs/runbooks/incidents.md`](runbooks/incidents.md) §3.5.
- [ ] **Queue-and-retry for `/draft-outreach` and `/draft-linkedin`**.
      Today they 503 on Gemini outage; a queue + background drain
      keeps the operator's clicks from going to waste.

### Cost monitoring
- [ ] **Cost-report digest**. `cost-report.yml` workflow + Discord
      weekly digest. Sources: Gemini usage (from `/metrics`), Supabase
      DB size + bandwidth, Render deploy minutes + bandwidth, Google
      Maps API (if it has cost), domain + SSL.
- [ ] **Cost-alert thresholds**. Per-source warning bands tuned to
      the budget. Triggered by the digest's parsing step.

### Read-only mode
- [ ] **`READ_ONLY_MODE` env flag**. When set, every write endpoint
      returns 503 + a documented error; reads continue. Replaces the
      "scale Render to 0" mitigation in
      [`docs/runbooks/incidents.md`](runbooks/incidents.md) §4.3a.

### Backend lifespan
- [ ] **Move `recover_interrupted_jobs()` to background task** after
      `yield` in the lifespan. Currently it blocks boot; moving it
      cuts cold-start by ~3–5 s on the slow Supabase boot path.
      Referenced in CLAUDE.md "Lifespan still blocks cold start."

### Testing
- [ ] **PR-time YAML lint** via `actionlint` in `ci.yml`. Catches
      workflow typos before they fire on cron. Today YAML errors only
      show on next scheduled run after merge.
- [ ] **Migration-safety preview-branch gate** — enable
      `migration-safety.yml` (currently disabled by default;
      `workflow_dispatch` only). Requires Supabase Pro for preview
      branches.

---

## Later (3–6 months)

### [BookBed.io] Commercialization track
- [ ] **Marketing site** — landing page with value prop, demo video,
      pricing, signup. Out of scope for the personal deployment.
- [ ] **Pricing page** with Stripe / LemonSqueezy / Paddle integration.
      Aligns with [`docs/legal/terms.md`](legal/terms.md) §7.
- [ ] **Analytics** — Plausible (GDPR-friendly, no cookies) on the
      marketing site only. Dashboard remains analytics-free (single-
      operator + GDPR posture).
- [ ] **Conversion funnel tracking** in Plausible.
- [ ] **Email capture** newsletter — Buttondown / Beehiiv / Listmonk.

### [BookBed.io] Multi-tenant migration
- [ ] **`owner_user_id` column + RLS migration**. Supersedes
      [ADR-001](adr/001-single-tenant-by-design.md). Requires:
      - Per-table `owner_user_id NOT NULL` backfill (~32 SELECT
        sites need the filter; see [ADR-005](adr/005-no-soft-delete.md)
        §migration estimate).
      - Per-row RLS replacing deny-all.
      - Handler-level scoping for every per-resource endpoint.
      - Tests for cross-tenant isolation.
      - Estimate: 2–3 PR weeks.

### [BookBed.io] Support tooling upgrade
- [ ] Graduate from GitHub Issues + email to Plain.com when ticket
      volume crosses ~20/week. See
      [`docs/support-process.md`](support-process.md) §10.

### Provider diversification
- [ ] **Provider-fallback for AI** (Gemini → Claude → OpenAI). Big
      lift — every prompt + every structured-output schema needs
      re-tuning. Tracked in
      [`docs/runbooks/incidents.md`](runbooks/incidents.md) §3.5.
      Superseded ADR-006 if implemented.

### Migration tooling
- [ ] **Supabase migration runbook** with a tested DB dump → restore
      cycle to a different provider (Neon / RDS / Cloud SQL). Today
      [`docs/runbooks/incidents.md`](runbooks/incidents.md) §2.5 notes
      this as a stub; making it a runnable script unlocks the > 6h
      outage threshold.

---

## Probably not (would change the project significantly)

- **Soft delete adoption**. Documented in
  [ADR-005](adr/005-no-soft-delete.md) as deliberately rejected.
  Revisits only if multi-operator + audit/regulatory retention
  becomes a requirement.
- **In-app analytics for the dashboard**. Single-operator means
  there's no analytics audience but the operator themselves; the
  `/metrics` web-vitals ingest + Sentry already cover what the
  operator needs.
- **Self-serve operator signup**. Conflicts with
  [ADR-001](adr/001-single-tenant-by-design.md). Each deployment has
  one operator; signup is manual provisioning via Supabase Auth.
- **Real-time WebSocket dashboard**. The orchestrator banner +
  polling at 60-min cadence covers the actual needs. WebSocket adds
  infra complexity (sticky sessions, fan-out, reconnect logic) for
  ~zero UX gain at single-operator scale.

---

## Process

This file is updated alongside the work, not as a separate exercise:

- A new item lands here as part of the PR that creates it (or that
  *discovers* it's needed).
- An item moves between `Now` / `Next` / `Later` as priorities
  shift — a git diff shows the movement.
- An item moves to a new ADR (`docs/adr/`) when it crystallizes into
  an architectural decision.
- An item is **deleted** (not archived) when it's done. The git
  history is the audit trail.

---

## Recently shipped (last 4 weeks)

- ✅ GDPR data export endpoint + tests + frontend button.
- ✅ GDPR account deletion endpoint + tests + audit trail + 30-day
      purge script.
- ✅ Sentry error tracking + APM (`docs/observability.md`).
- ✅ Structured JSON logging + request-ID middleware
      (`src/utils/logging_config.py`).
- ✅ Discord alerting for 5 operational signals
      (`docs/alerting.md`).
- ✅ Incident-response runbook (`docs/runbooks/incidents.md`).
- ✅ Rollback runbook + quarterly drill protocol
      (`docs/runbooks/rollback.md`).
- ✅ Launch checklist (`docs/launch-checklist.md`).
- ✅ Privacy policy + ToS templates (`docs/legal/`).
- ✅ Support process scaffold (`docs/support-process.md` +
      `docs/faq.md`).
- ✅ Status page setup procedure (`docs/status-page-setup.md`).
- ✅ Operator guide + onboarding guide
      (`docs/runbooks/operator-guide.md`, `docs/onboarding.md`).
- ✅ Architecture Decision Records (`docs/adr/`).

The most recent ADRs:

- [ADR-001 — Single-tenant by design](adr/001-single-tenant-by-design.md)
- [ADR-002 — FastAPI, not Django](adr/002-fastapi-not-django.md)
- [ADR-003 — Supabase PostgREST, not direct PG](adr/003-supabase-postgrest-not-direct-pg.md)
- [ADR-004 — Playwright for Discovery, aiohttp for Audit](adr/004-playwright-for-discovery-aiohttp-for-audit.md)
- [ADR-005 — No soft delete](adr/005-no-soft-delete.md)
- [ADR-006 — Gemini, not OpenAI / Anthropic](adr/006-gemini-not-openai.md)
- [ADR-007 — Render, not Vercel, for the backend](adr/007-render-not-vercel-for-backend.md)
