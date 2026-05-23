# Launch Checklist

**Block the public launch until every box is checked.** Re-run quarterly
even after launch to catch drift.

The checklist is keyed to existing artifacts — each item names the doc /
workflow / endpoint that supplies the evidence. "Done" means the
referenced artifact is current; "in progress" means the artifact exists
but a follow-up is open; "not started" means the artifact doesn't
exist yet.

---

## Pre-launch (block until 100%)

### Testing
- [ ] All 8 phases of tests passing in CI on the latest `main` —
      [`docs/ci-architecture.md`](ci-architecture.md), specifically the
      ~20 required checks in `ci.yml`. Verify with `gh run list
      --workflow=ci.yml --branch=main --limit=1 --json conclusion`.
- [ ] Mutation-coverage tracker issue (label `mutation-coverage`) is
      CLOSED — `gh issue list --label mutation-coverage --state open`
      returns nothing.
- [ ] Flakiness-detector tracker issue (label `flaky`) is CLOSED.
- [ ] At least one full end-to-end Playwright run green within last
      72h —
      [`docs/e2e-and-frontend-contracts.md`](e2e-and-frontend-contracts.md).

### Branch protection + supply chain
- [ ] Branch protection enabled on `main` — Settings → Branches:
      "Require pull request before merging", "Require status checks
      to pass", "Require code-owner review" (CODEOWNERS at
      `.github/CODEOWNERS`).
- [ ] All workflow `uses:` lines are SHA-pinned with the
      `# vX.Y.Z` comment Dependabot reads
      ([`docs/ci-architecture.md`](ci-architecture.md) §workflow-drift).
- [ ] Pre-commit hooks installed locally (`make install-hooks`) AND
      the `ci.yml::pre-commit (local-CI parity)` gate is green.

### Secrets
- [ ] Secrets rotated within last 30 days
      ([`docs/secret-inventory.md`](secret-inventory.md)): all
      "monthly" tier secrets — `SUPABASE_SERVICE_ROLE_KEY`,
      `RENDER_API_KEY`, `SUPABASE_DATABASE_URL`.
- [ ] All "quarterly" tier secrets rotated within last 90 days:
      `API_SECRET_KEY`, `ADMIN_TOKEN`, `GEMINI_API_KEY`.
- [ ] gitleaks scan clean — `gh run list --workflow=security.yml
      --branch=main --limit=1 --json conclusion`.

### Backups + data integrity
- [ ] Backup verified (Faza 8.9 = `backup-verify-deep.yml` monthly
      cadence) — last run within 35 days, green.
- [ ] Schema-drift CI gate green on latest main.
- [ ] Referential-integrity CI gate green.
- [ ] Storage usage < 70 % of plan limit
      ([`docs/alerting.md`](alerting.md) §storage).

### Observability
- [ ] Synthetic monitor active and reporting green in Discord — last
      cron run within 10 min, `gh run list
      --workflow=synthetic-monitor.yml --limit=1`.
- [ ] Sentry receiving events — manual trigger via
      `SENTRY_TEST_ENABLED=1 + POST /_sentry/test`
      ([`docs/observability.md`](observability.md) §5).
- [ ] Discord webhook reachable — manually fire the throwaway
      `test-alert.yml` and confirm receipt in `#alerts`.
- [ ] Cost monitoring active — `cost-report.yml` ran within 7 days,
      digest landed in Discord.

### Legal
- [ ] **Privacy policy published** at a stable customer-facing URL
      ([`docs/legal/privacy-policy.md`](legal/privacy-policy.md)) and
      reviewed by counsel.
- [ ] **Terms of Service published**
      ([`docs/legal/terms.md`](legal/terms.md)) and reviewed by counsel.
- [ ] If commercializing: DPA template available for B2B customers who
      ask. Sub-processor list in privacy policy §6 is current.
- [ ] Cookie banner (if EU traffic expected and any analytics added
      later) — out of scope until analytics ship; revisit when adding.

### Operator + user-facing endpoints
- [ ] `GET /operator/data-export` returns valid ZIP — manual smoke
      test from dashboard Settings → "Download my data".
- [ ] `DELETE /operator/account` correctly fails when confirmation
      phrase is wrong (422) — manual smoke test.
- [ ] `DELETE /operator/account` correctly hard-deletes when all 3
      gates pass — smoke test on a throwaway data set; confirm `audit_log`
      JSON has the row + `account_deletions` row exists.
- [ ] 30-day purge script (`purge_expired_audit_log.py`) runs daily
      in `security.yml`.

### Operational alerting
- [ ] Cost-monitoring alert wired (`#alerts` Discord channel receives
      weekly digest) — [`docs/alerting.md`](alerting.md).
- [ ] Synthetic-monitor 3-fail alert tested end-to-end (Discord ping
      received in a deliberate-fail drill).
- [ ] Cert-expiry monitor active — `cert-expiry-monitor.yml` ran
      within last week, all hosts > 30 days out.
- [ ] Cold-start monitor active — `cold-start-monitor.yml` ran within
      last day.

### Status page + domain
- [ ] Status page live at `status.<your-domain>` — see
      [`docs/status-page-setup.md`](status-page-setup.md) for the
      upptime setup.
- [ ] Domain SSL certificate valid > 90 days — `cert-expiry-monitor.yml`
      last run, all hosts > 90.
- [ ] Domain DNS records reviewed — A + AAAA + CNAME + MX (if email).
      Backup MX configured if email-sending is critical.

### Documentation
- [ ] Operator guide complete +
      [`docs/runbooks/operator-guide.md`](runbooks/operator-guide.md)
      screenshots captured.
- [ ] Onboarding guide complete and verified by a fresh dev
      ([`docs/onboarding.md`](onboarding.md)).
- [ ] FAQ complete enough to cover the top 10 expected support
      questions ([`docs/faq.md`](faq.md)).
- [ ] Incident-response runbook reviewed within last 90 days
      ([`docs/runbooks/incidents.md`](runbooks/incidents.md)).

### Support
- [ ] Support email `support@<your-domain>` configured with an
      auto-responder ([`docs/support-process.md`](support-process.md)).
- [ ] GitHub Issues label `support` exists.
- [ ] Internal ticket SLA defined and posted.

### Rollback + DR
- [ ] Rollback runbook reviewed within last 90 days
      ([`docs/runbooks/rollback.md`](runbooks/rollback.md)).
- [ ] **Disaster recovery drill executed within last 90 days** —
      a quarterly rollback drill per `rollback.md` §5. Drill log in
      `docs/runbooks/drills/`.
- [ ] PITR restore test executed within last 90 days — manual
      `backup-verify-deep.yml workflow_dispatch` run with a target
      timestamp; restore branch produced clean schema-drift +
      referential-integrity diffs.

### Marketing (if commercialized)
- [ ] Marketing site + landing page live — out of scope for the
      personal/internal deployment; required for the BookBed.io
      commercialization track.
- [ ] Pricing page live with payment-processor integration (Stripe /
      LemonSqueezy / Paddle) — required when commercializing.

---

## Post-launch (re-run quarterly)

Every quarter, re-walk the entire list above. Drift catches:

- Branch protection accidentally relaxed.
- A secret missed its rotation window (most common).
- Documentation referencing endpoints that no longer exist.
- Drill logs > 100 days old.

Cadence: **first Monday of each quarter.** Block the next major
feature shipment until the checklist passes 100%.

---

## Quick "am I ready to launch?" formula

```
If ALL boxes checked → ship.
If ANY box unchecked → don't ship. Open an issue named
"launch-blocker:<item>" and fix it before ship.

Do NOT ship with a "we'll fix it after launch" item.
The post-launch fire is always more expensive than the pre-launch
diligence. The checklist exists because previous-self regretted not
having it.
```
