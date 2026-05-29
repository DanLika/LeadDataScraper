# LeadDataScraper

Single-tenant lead-scraping + enrichment pipeline. FastAPI backend + Next.js
dashboard, Supabase Postgres for state, Google Gemini for AI, Playwright for
browser automation. Designed for one operator end-to-end.

## Where to start

- **New developer?** Read [`docs/onboarding.md`](docs/onboarding.md) — get from
  clone to first PR in under a day.
- **Day-to-day operator?** [`docs/runbooks/operator-guide.md`](docs/runbooks/operator-guide.md).
- **Project depth (defenses, contracts, test invariants)?** [`CLAUDE.md`](CLAUDE.md)
  in the repo root — the canonical project brief.

## Documentation map

| Doc | Purpose |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | Canonical project brief; every defense, contract, and pinned finding |
| [`docs/onboarding.md`](docs/onboarding.md) | New-dev setup, run, test, deploy |
| [`docs/runbooks/operator-guide.md`](docs/runbooks/operator-guide.md) | Day-to-day operations + failure recovery |
| [`docs/runbooks/incidents.md`](docs/runbooks/incidents.md) | Incident response — 5 scenarios with detection → triage → mitigation → post-mortem |
| [`docs/runbooks/rollback.md`](docs/runbooks/rollback.md) | Rollback runbook + quarterly drill protocol |
| [`docs/legal/privacy-policy.md`](docs/legal/privacy-policy.md) | Privacy policy (GDPR/CCPA template — needs counsel review) |
| [`docs/legal/terms.md`](docs/legal/terms.md) | Terms of Service template — needs counsel review |
| [`docs/launch-checklist.md`](docs/launch-checklist.md) | Pre-launch checklist — block ship until 100% green |
| [`docs/support-process.md`](docs/support-process.md) | Support email + SLA + ticket flow |
| [`docs/faq.md`](docs/faq.md) | Top-questions FAQ |
| [`docs/status-page-setup.md`](docs/status-page-setup.md) | upptime-based status page setup (separate repo) |
| [`docs/roadmap.md`](docs/roadmap.md) | Roadmap — now / next / later / probably not |
| [`docs/adr/`](docs/adr/README.md) | Architecture Decision Records — *why* this and not that |
| [`docs/observability.md`](docs/observability.md) | Sentry wiring, source maps, alerts, PII scrubbing |
| [`docs/alerting.md`](docs/alerting.md) | Discord alert routing for 5 non-Sentry signals (synthetic / storage / mutation / cold-start / cert-expiry) |
| [`docs/ci-architecture.md`](docs/ci-architecture.md) | 15 GitHub Actions workflows and what they gate |
| [`docs/secret-inventory.md`](docs/secret-inventory.md) | Every secret, rotation cadence, blast radius |
| [`docs/e2e-and-frontend-contracts.md`](docs/e2e-and-frontend-contracts.md) | Playwright E2E + frontend invariants |
| [`docs/post-deploy-smoke.md`](docs/post-deploy-smoke.md) | Post-deploy synthetic checks |
| [`docs/synthetic-monitor.md`](docs/synthetic-monitor.md) | Hourly synthetic monitoring |
| [`SECURITY.md`](SECURITY.md) | Vulnerability reporting |

## Stack

- **Backend:** Python · FastAPI · Supabase (PostgREST) · Playwright · Google
  Gemini · aiohttp · slowapi
- **Frontend:** Next.js 16 (App Router) · React 19 · TypeScript · Recharts ·
  Tanstack Virtual · Lucide icons
- **Hosting:** Render (both services) · GHCR (container registry) · SLSA3
  provenance · cosign verify before rollout
