# Local Merge Sprint — 2026-05-24

**Operator:** Duško Ličanin (claude session, /effort max)
**Trigger:** GH Actions runner allocation failure → all PR CI red → local-verify+merge bypass
**Source plan:** runbook embedded in `/effort` prompt (referenced `docs/pr-merge-order-2026-05-23.md` + `tests/quality/pr-review-pass-2026-05-23.md`, neither exist; runbook's inline phase list used as source of truth)
**Pre-sprint state:** origin/main = `bd4dab5` (#271 Gemini cost cap); 49 open PRs; local repo corrupted (branch ref mispointed by parallel session HEAD-swap)

## Architecture decisions

1. **Isolated worktree** at `/Users/duskolicanin/git/lds-merge-2026-05-24` on dedicated `chore/merge-sprint-2026-05-24-v2` branch — not main worktree (parallel sessions kept swapping HEAD on it mid-flight).
2. **Cherry-pick over squash-merge** — most PRs branched from `6488afb` (pre-drain) so `git merge --squash` collided on commits already-in-main via Phase 1 stack. Per-PR cherry-pick + ad-hoc reword preserved intent without conflict noise.
3. **Batch push per phase** (chosen via AskUserQuestion) — one `git push origin chore/merge-sprint-2026-05-24-v2:main` per phase → ~6 Render auto-deploys vs ~22 per-PR.
4. **Rescue branches** for floating local-only commits BEFORE any reset: `rescue/audit-2026-05-24-d6aa160` + `rescue/audit-2026-05-24-a2ec2f7` (per advisor recommendation).

## Outcome

| Phase | PRs targeted | Merged | Deferred | Push SHA range |
|---|---|---|---|---|
| 1 — docs stack | #253 #254 #258 | 3 | 0 | `bd4dab5..3846308` |
| 2 — audit fixes | #263-#270 | 8 | 0 | `3846308..426716d` |
| 3 — P1/P2 bug fixes | #275 #276 #277 #278 | 3 | 1 (#277) | `426716d..4e56504` |
| 4 — frontend fixes | #234 #235 #237 #244 #245 #246 #251 | 7 | 0 | `4e56504..b37f45d` |
| 5 — live pipeline docs | #274 | 1 | 0 | `b37f45d..f6691d4` |
| 6 — i18n + email + demo | #243 #247 #249 | 2 | 1 (#247) | `f6691d4..ed62b9d` |
| 7 — Dependabot | 9 PRs | 0 | 9 | (skipped per runbook) |

**Total:** 24 PRs merged across 6 prod-deploys. Final origin/main HEAD: `ed62b9d`.

## Closed without merging (superseded)

- #134 #137 #139 #140 (May-05 stale auto-generated PRs — `Superseded by drain + audit-fix sprint`)
- #233 (already closed)
- #240 (Inter font — exact dupe of #239; kept #239, closed `-opus47-v2` variant)

## Deferred (open, with reproduction comment)

- **#277** (`fix/skip-ai-on-bot-blocked`): test mock `response.text = AsyncMock(...)` incompatible with newly-merged #269 body-cap path (`response.content.read(MAX_HTML_BYTES + 1)`). Code is correct; test needs `response.content.read = AsyncMock(return_value=body.encode())` + `response.charset = 'utf-8'`. 5 unit-test failures (KeyError) on `is_bot_blocked` / `page_text` — all because test never reaches L313-319 bot-block branch (caught by outer `except Exception` first).
- **#247** (`chore/demo-data-seed-13.3`): branched off `6488afb`; cherry-pick would REVERT merged #270 `PipelineFilters` Pydantic model back to raw `_PIPELINE_FILTER_KEYS = frozenset(...)`. Also drops the `gemini_budget` import landed via #271. Needs rebase onto `ed62b9d` + reconciliation, plus `is_demo` column migration applied to live Supabase BEFORE deploy.

## Deferred to user decision

- **#248 vs #255** (crossover verification doc): two PRs editing same files (`docs/bookbed-crossover.md` + `docs/phase-d-header-backport-plan.md`) with different content. Memory flagged as "verification disagreement P0". Neither in any phase; both left open.

## Phase 7 Dependabot — explicit skip

All 9 (#213 #215 #216 #217 #218 #219 #220 #221 #222) deferred per runbook ("may need rebase due to lockfile churn"). Three are MAJOR bumps with potential breaks: lucide-react 0→1, pandas 2→3, eslint 9→10. Rebase + per-PR breaking-change audit needed before merging.

## Verification per phase

- **Backend code phases (2, 3, 4):** `pytest tests/unit -q` via worktree's `PYTHONPATH` against main repo's `.venv`. Each phase: 114-118 passed, **3 pre-existing failures** in `tests/unit/test_guarded_generate_content.py::TestGuardedGenerateContentAsync` (root cause: `pytest-asyncio` plugin not installed/registered in Py3.14 venv — failures present on `origin/main` HEAD before any sprint commits). Pre-existing status confirmed by checking out `origin/main` version of the test file and re-running.
- **Frontend code phases (4, 6):** `tsc --noEmit -p tsconfig.json` via symlinked `node_modules` from main repo. All clean (silent exit).
- **Docs phases (1, 5, 6/#243):** no code verify needed.
- **SQL #263:** no live-DB check possible from worktree; relies on `schema_drift_check.py` daily cron after deploy.
- **Docker #266:** no local docker build; trust Render build to surface errors.

## Render deploys

6 pushes triggered Render auto-deploys (backend + frontend services per `render.yaml`: `lead-scraper-backend` + `lead-scraper-frontend`). Smoke-test from sandbox timed out on backend cold-start (>30s — expected on free tier after idle). Operator: verify Render dashboard shows green deploys for `ed62b9d` on both services.

## Parallel-session interference observed

Multiple `parallel sessions` (per CLAUDE.md note on `head-swap mitigation via git worktree`) were active throughout the sprint:

- HEAD on the main worktree swapped 3+ times mid-investigation (`fix/account-deletions-policy-mode-2026-05-23` → `feature/demo-data-seed-13.3` → `feature/i18n-croatian-phase-13.1`).
- Two PRs in Phase 1 (#254, #258) were auto-closed on GitHub during my run — either via squash-merge by parallel agent OR by the GitHub auto-close-on-merged-commit heuristic.
- First isolated worktree at `/private/tmp/lds-merge-sprint` was wiped by external process (likely `git worktree prune` from parallel agent + macOS tmp-cleanup combo). Recreated at `/Users/duskolicanin/git/lds-merge-2026-05-24` — survived.

## Open PR count

- **Before sprint:** 49 open
- **After sprint:** ~25 open (count fluctuates as parallel sessions open new PRs; gh pr list snapshot at end showed 30)

## Recommended follow-ups (operator)

1. Verify Render shows green for `ed62b9d` on both `lead-scraper-backend` + `lead-scraper-frontend`. If red, `git revert ed62b9d..f6691d4` (Phase 6, smallest blast — Phase 5 docs only).
2. Resolve #248/#255 dupe (crossover verification doc) — both still open.
3. Rebase #277 with the documented test-mock fix → re-cherry-pick.
4. Rebase #247 onto `ed62b9d` + reconcile with #270 + #271 → run schema migration → re-merge.
5. Triage remaining ~22 open PRs (docs/test/cleanup miscellany + 9 Dependabot).
6. `rescue/audit-2026-05-24-{d6aa160,a2ec2f7}` local branches preserved — review + delete after confirming content is on main via the sprint (it is — landed via #270 + #263 squash + the audit-report doc landed via Phase 1 #258's stack).
