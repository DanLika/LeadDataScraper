# Runbooks index

Operator-facing runbooks for production incidents, recurring infra issues,
and deploy procedures. Each runbook has the same shape:

1. **Status** — current resolution state.
2. **Symptom** — what the operator sees.
3. **Root cause** — one or two paragraph explanation.
4. **Fix recipe** — copy-paste commands or PR refs.
5. **Recurrence guard** — CI gate or code pattern preventing repeat.

Companion docs:

- [`incidents.md`](incidents.md) — SEV-1 incident playbook (5 scenarios).
- [`operator-guide.md`](operator-guide.md) — day-to-day operator tasks.
- [`rollback.md`](rollback.md) — backend / frontend / DB rollback flows.
- [`dispatch-cron.md`](dispatch-cron.md) — Phase 14/15 dispatch cron canon.
- [`apply-phase-14-15-migrations.md`](apply-phase-14-15-migrations.md) —
  schema apply procedure.
- [`render-env-push.md`](render-env-push.md) — Render env-var sync recipe.
- [`context-mode.md`](context-mode.md) — context-mode MCP plugin usage.

---

## Top-level (incident-time)

| Runbook | Status | Symptom one-liner |
|---|---|---|
| [`incidents.md`](incidents.md) | LIVE | SEV-1 playbook — backend down, frontend down, AI cost runaway, data corruption, security |
| [`rollback.md`](rollback.md) | LIVE | Roll backend / frontend / DB to a known-good state |

## 2026-05-27 → 2026-05-29 prod-fix batch

Surfaced from session memory so future operators can find without scrolling.

### Discovery / Playwright

| Runbook | Status | Symptom |
|---|---|---|
| [`discovery-oom.md`](discovery-oom.md) | PARTIALLY MITIGATED | `/discovery/start` OOM-kills Render starter (512 MB); PR #397 reduced footprint but single fresh discovery still peaks above 512 MB. Operator decision pending. |

### Schema / migrations

| Runbook | Status | Symptom |
|---|---|---|
| [`check-constraint-apostrophe-drift.md`](check-constraint-apostrophe-drift.md) | RESOLVED | Phase 14/15 CHECK literals double-escaped during Management API apply; regex + IN-list constraints reject all valid input. |
| [`apply-phase-14-15-migrations.md`](apply-phase-14-15-migrations.md) | LIVE | Migration-apply procedure for Phase 14/15 schema (referenced by check-constraint runbook). |

### Cron / background workers

| Runbook | Status | Symptom |
|---|---|---|
| [`pep562-cron-path-trap.md`](pep562-cron-path-trap.md) | RESOLVED | Cron entrypoints bypass FastAPI lifespan; bare-name `db.client.*` → swallowed NameError → silent infinite-replay. |
| [`webhook-burst-stranded-rows.md`](webhook-burst-stranded-rows.md) | RESOLVED | `/webhooks/instantly` 10-parallel burst → 8–23 % return 500 but row commits → background task never fires. |
| [`dispatch-cron-stale-deploy.md`](dispatch-cron-stale-deploy.md) | UNRESOLVED | Render-side dispatch produces `no_email_or_lead_row`; local repro never hits it. Likely stale deploy / env-var drift. |
| [`dispatch-cron.md`](dispatch-cron.md) | LIVE | Phase 14/15 dispatch tick concurrency / windowing / advancement canon. |

### Python version / stdlib

| Runbook | Status | Symptom |
|---|---|---|
| [`py310-isoformat-tolerance.md`](py310-isoformat-tolerance.md) | RESOLVED | Py3.10 `datetime.fromisoformat` rejects 4/5/7-digit microsecond fractional seconds; CI Py3.12 hides bug. |

<a id="py3-10-vs-py3-12-drift"></a>
**Py3.10 vs Py3.12 drift cluster** — Both the isoformat runbook and the
lockfile runbook (cluster #1) are instances of the same shape: CI Python
3.12 hides bugs that only surface on prod Python 3.10. Eventually wire a
parallel Py3.10 pytest job — pending operator decision on CI cost budget.

### CI / lockfile / quality gates

| Runbook | Status | Symptom |
|---|---|---|
| [`lockfile-drift-recovery.md`](lockfile-drift-recovery.md) | RESOLVED (cluster #1) | `npm ci` fails across 7 jobs with `@swc/helpers` lockfile drift on Node 20 CI. |
| [`supply-chain-pin-discipline.md`](supply-chain-pin-discipline.md) | LIVE | Carets on security-critical npm deps + bare `FROM` tag + unpinned apt — three pin classes per `/security-audit:run` 2026-05-29. |

<a id="ci-cluster-discipline"></a>
**CI cluster discipline** (per `ci_six_clusters_2026-05-28.md` memory) —
admin-merging through "cluster #N noise" silently absorbed real Phase 14+15
regressions in #357 / #358 / #366. NEVER admin-merge again without
`gh run view --log-failed | head -80` per failing job to confirm the failure
fingerprint matches a known cluster.

<a id="check-constraint-pairing"></a>
**CHECK constraint dict pairing** — adding a CHECK to `supabase_schema.sql`
REQUIRES same-PR update to `EXPECTED_CHECK_CONSTRAINTS` in
`src/scripts/schema_drift_check.py`. 3 PRs fell into the trap
(#353 / #356 / #366); codified by PR #380 / #378. See
[`check-constraint-apostrophe-drift.md`](check-constraint-apostrophe-drift.md).

<a id="page-tsx-split"></a>
**page.tsx split** — 1980-LOC dashboard refactor (32 buttons, 5 dialogs)
queued on branch `chore/page-tsx-split`; deferred while `pgrep claude ≥ 2`
(per `claude_md_refactor_defer_2026-05-25.md` timing rule). See memory
`project_page_tsx_split_deferred.md`.

### Quality baselines / mypy / ratchet

See `tests/quality/` + memory `quality_baseline_drift_exception_template.md`
for the documented escape hatch when admin-merge has bypassed the ratchet.

### AI / Gemini

| Runbook | Status | Symptom |
|---|---|---|
| [`gemini-quota-exhausted.md`](gemini-quota-exhausted.md) | RESOLVED (graceful surfacing) | All AI endpoints return 503 `{"error":"ai_quota_exceeded","retry_after":"tomorrow"}`. Upstream Gemini 429. Distinguish from local-cap 503 by body. PR #420. |

### Secrets / env

| Runbook | Status | Symptom |
|---|---|---|
| [`env-var-local-vs-prod-drift.md`](env-var-local-vs-prod-drift.md) | UNRESOLVED | Local `.env` `API_SECRET_KEY` (128 char) differs from Render prod (64 char); historic rotation skipped local. |
| [`render-env-push.md`](render-env-push.md) | LIVE | Render env-var sync recipe (used by env-drift runbook). |

<a id="secret-rotation"></a>
**Secret rotation** — `docs/secret-inventory.md` is the canonical list +
cadence. Memory `reminder_supabase_key_rotation.md` notes the Supabase
service_role JWT for `kbtkxpvchmunwjykbeht` was leaked 2026-05-27 via a
shell pattern; rotate BEFORE first real send / Phase 16 / transcript share.

### Documentation / operator tooling

| Runbook | Status | Symptom |
|---|---|---|
| [`claude-md-size-budget.md`](claude-md-size-budget.md) | RECURRING | CLAUDE.md exceeds 35 k soft / 40 k hard; parallel-session growth re-breaches budget within days. Slim recipe + timing rules pinned. |

<a id="render-cron-deploys"></a>
**Render cron deploys** — pin `{"clearCache":"do_not_clear"}` payload
(string-enum, not boolean). Env-PUT auto-redeploys. Logs API needs
`ownerId` + resource (not `/services/<id>/logs`). Memory
`render_cron_deploy_recipe.md` has the webhook_sweeper cron ID +
team ownerId.

---

## Adding a new runbook

1. Create `docs/runbooks/<slug>.md` matching the 5-section shape above.
2. Add a row to the appropriate table in this README.
3. If the runbook is referenced from a CLAUDE.md invariant section, add a
   one-line pointer there too (respecting the
   [size budget](claude-md-size-budget.md)).
4. Cross-link related runbooks in the **Related** section at the bottom of
   each runbook file.
