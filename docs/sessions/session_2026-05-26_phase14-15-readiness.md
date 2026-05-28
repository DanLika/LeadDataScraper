# Session 2026-05-26 — Phase 14+15 deploy readiness

**Verdict:** Phase 14+15 code locally green. Render deploy blocked at env-var pre-flight (5 of 7 keys missing in `~/.bookbed-secrets`). No code changes this session.

## Goal

Verify Phase 14+15 dispatch stack (PRs #325–#348 + #350) is coherent on `main` before pushing env vars to Render and triggering deploy. Two-part:
1. Pre-flight `scripts/render_env_push.sh` against current `~/.bookbed-secrets`
2. Run full local pytest + targeted Phase 14+15 contract tests + lint/type/schema-drift

## Part 1 — Render env-var pre-flight

### Secrets file inventory

| Key | Status | Note |
|-----|--------|------|
| `INSTANTLY_API_KEY` | ❌ missing | |
| `INSTANTLY_DEFAULT_CAMPAIGN_ID` | ❌ missing | |
| `INSTANTLY_WEBHOOK_SIGNING_SECRET` | ✅ 64 chars | pre-populated 2026-05-26 |
| `UNSUBSCRIBE_TOKEN_SECRET` | ✅ 64 chars | pre-populated 2026-05-26 |
| `UNSUBSCRIBE_BASE_URL` | ❌ missing | must be backend host, not frontend |
| `OPERATOR_SIGNATURE` | ❌ missing | newline-handling caveat (see runbook) |
| `SEND_WINDOW_DEFAULT_TZ` | ❌ missing | `Europe/Sarajevo` per Phase 15 spec |

### Environment checks
- File permissions: `chmod 600` confirmed
- `RENDER_API_KEY` present in shell, 32 chars
- Render API reachable: `GET /v1/services/srv-d89bisbbc2fs73f1pjpg` → **HTTP 200**
- Script `bash -n` syntax: clean
- Script has no `--dry-run`; interactive `y/N` prompt is the only gate

### Blocker
Cannot run script until 5 keys filled. See [`docs/runbooks/render-env-push.md`](../runbooks/render-env-push.md) for operator recipe.

## Part 2 — Local test sweep

### Step 1: Working tree
**DIRTY** — 7 frontend files (`LeadTable.tsx`, `OfflineBanner.tsx`, 4 e2e specs, `eslint.config.mjs`) + 1 untracked (`tests/loadtest/smoke-test-2026-05-26.md`). `pgrep -af "claude --"` showed 5 concurrent PIDs. Per `multi-session-working-tree-contention` rule, did not stash/sync — ran sweep read-only on current HEAD.

### Step 4: Full pytest
```
1064 passed, 80 skipped, 86 deselected, 1438 warnings, 191 subtests passed in 19.29s
```
Beats baseline ≥1029 by +35. Tool versions: py3.14.3, pytest 9.0.3, fastapi 0.121.0, supabase 2.30.0.

### Step 5: Phase 14+15 contract tests

| Test | Pass | Notes |
|------|-----:|-------|
| `tests/integration/test_unsubscribe_url_roundtrip.py` | 4/4 | URL mint → handler-route parity; trailing-slash + empty-token guards |
| `tests/test_provider_literal_parity.py` | 3/3 | Webhook + ledger + suppression provider Literals vs DB CHECK |
| `tests/ -k webhook_event_repo` | 9/9 | Repository extraction (PR #344) + `is_unique_violation` checks |

### Step 6: Lint + type

| Tool | Current | `.quality-baselines.json` | Δ |
|------|--------:|--------------------------:|--:|
| ruff (`src backend tests`) | 273 | 90 | +183 |
| mypy `--strict src/` | 663 | 401 | +262 |

Tools: ruff 0.15.14, mypy 2.1.0. Baseline file `_meta.captured_at: 2026-05-22`. CI runs same py3.14 — if `quality-ratchet.yml` is currently green on `main`, baseline file is stale, not code. **Recommend:** verify ratchet run on `main` before treating as code regression; if stale, run `scripts/capture-quality-baselines.sh` and commit fresh baseline.

**One worth investigating:** `F821 Undefined name 'db'` at `backend/main.py:2999, 3009`. Likely PEP-562 false-positive (ruff doesn't see module-level `__getattr__`), but no test pins those exact lines under lifespan-cold-start. PEP 562 trap is documented in CLAUDE.md "Cold-start lazy imports".

### Step 7: Schema drift
Script is at `src/scripts/schema_drift_check.py` (user's STEP 7 had wrong path `scripts/`). Needs `DATABASE_URL` env var (Postgres conn-string), unavailable locally without violating "don't touch live infra" rule. **Skipped.** Recommend running on staging or as part of `ci.yml` schema-drift job which has the secret.

### Step 8: Pre-commit (scoped)
Ran `pre-commit run --files <last-4-commits-backend-files>` (3 files: `backend/main.py`, `src/repositories/webhook_event_repo.py`, `tests/unit/test_webhook_event_repo.py`). **Result:** `ruff-format` hook mutated all 3 files — 759-line diff on `backend/main.py` alone. Reverted via `git checkout --`.

**Memory rule `feedback_precommit_all_files_splatter.md` extended:** even scoped to a single file, `ruff-format` rewrites the file holistically. To lint without splatter, use `ruff check <file>` (no `format`, no pre-commit).

## Coherence verdict

**GREEN** for code; **RED** for deploy readiness (env-var blocker).

Phase 14+15 wiring is internally consistent:
- Unsubscribe URL handler route matches dispatch-time URL minting
- Provider Literal types match DB CHECK constraints
- Webhook event repository extraction (PR #344) intact
- Zero pytest regressions vs baseline

## Operational notes

- **5 parallel claude PIDs** during this session — kept all writes read-only; reverted accidental ruff-format mutation
- **Working tree dirty before session start** — 7 frontend files attributed to parallel sessions; untouched here
- **No live infra touched** — schema drift skipped, env-var push deferred, no Render API mutation

## Cross-references

- Memory: `session_2026-05-26_phase14-15-readiness.md` (this session) — short-form non-obvious findings
- Runbook: [`docs/runbooks/render-env-push.md`](../runbooks/render-env-push.md) — operator recipe to unblock
- Prior session: [`session_2026-05-26_stack_merge.md`](session_2026-05-26_stack_merge.md) — 21-PR merge that produced the code under test
- Smoke attempt: [`tests/loadtest/smoke-test-2026-05-26.md`](../../tests/loadtest/smoke-test-2026-05-26.md) — earlier same-date attempt blocked at /health
- Memory: `smoke_test_blocked_2026-05-26.md` — same-day smoke-test blocker context
