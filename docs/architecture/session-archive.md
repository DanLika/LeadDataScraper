# Session log archive

Sourced from CLAUDE.md 2026-05-29 slim. Per-session detail under `docs/sessions/`. CLAUDE.md keeps one-line pointer; this index keeps the full hook list.

- [2026-05-22 patterns (#185–#199)](../sessions/2026-05-22-patterns.md) — layered arch / errors / logging / ratchet / test-org.
- [2026-05-23 drain (#235–#251)](../sessions/2026-05-23-drain.md) — backend security headers, WebVitals, TOTAL LEADS, Insights, request.state, REVOKE, poller backoff.
- [2026-05-23 Phase 15 audit](../sessions/2026-05-23-phase15-audit.md) — pkill LAST-flag, stale-build click, Render no-server lessons.
- [2026-05-23 crossover gaps](../sessions/2026-05-23-crossover-gaps.md) — COOP/CORP backport, P0a retraction, docs-PR rebase stack.
- [2026-05-23 branch hygiene](../sessions/2026-05-23-branch-hygiene.md) — HEAD-swap between turns; worktree-per-session mitigation.
- [2026-05-23 phase16-t3 data/obs](../sessions/2026-05-23-phase16-t3.md) — REVOKE account_deletions, seo_score partial index.
- [2026-05-23 dogfood prep](../sessions/2026-05-23-dogfood-prep.md) — demo data + i18n + email plan.
- [2026-05-23 BookBed crossover](../sessions/2026-05-23-bookbed-crossover.md) — Phase B Step 2; rate-limit + firestore findings.
- [2026-05-26 Phase 14+15 stack merge](../sessions/session_2026-05-26_phase14-15-stack.md) — 21 PRs; chained-base + GH outage admin-merge.
- [2026-05-26 Phase 14+15 sweep + ESLint fix](../sessions/session_2026-05-26_phase14-15-sweep.md) — pre-deploy sweep; pytest 1064/0 green; useSyncExternalStore refactor on OfflineBanner cleared eslint=0; ratchet ruff/mypy/pylint deferred; `pre-commit --all-files` splatter recipe.
- [2026-05-26 Phase 14+15 deploy-readiness (parallel)](../sessions/session_2026-05-26_phase14-15-readiness.md) — same-day parallel sweep; identical pytest 1064/0; Render blocked at env-var pre-flight (5/7 keys missing in `~/.bookbed-secrets`); ruff F821 `Undefined name 'db'` at backend/main.py:2999/3009 likely PEP-562 false-positive; ruff-format splatters even when scoped.
- [2026-05-27 schema apply + smoke + 2 fixes](../sessions/session_2026-05-27_schema-apply-smoke-fixes.md) — Phase 14+15 schema applied via Management API (5→11 tables, live rows untouched); 1158/0 across smoke + Chrome battery; PR #353 ESLint baseline merged + PR #354 proxy `/api/proxy/metrics` 401 fix; `.env` `API_SECRET_KEY=` duplicate-line trap diagnosed + collapsed; schema UNIQUE-constraint `duplicate_table` idempotency deferred per parallel-WIP collision.
