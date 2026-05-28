# Session 2026-05-26 — Phase 14+15 local-sweep + ESLint deploy-gate fix

8-step pre-deploy verification sweep on `main @ c051879` after the Phase 14+15 stack merge (PRs #320–#348). Goal: confirm coherence locally before Render deploy.

## Outcomes

### Code state
- Pytest: **1064 pass / 80 skip / 86 deselect / 0 fail** — above the 1029 baseline; no new regressions.
- Targeted Phase 14/15 contracts: `test_unsubscribe_url_roundtrip` 4/4, `test_provider_literal_parity` 3/3, `test_webhook_event_repo` 9/9 — all 100%.
- TSC: clean. Phase 14/15 schema/Pydantic invariants intact.

### CI state on main @ c051879 (cross-checked via `gh run view`)
- `main-matrix` ✅, `e2e` ✅
- `Quality Ratchet` ❌ — 4 metric regressions vs `.quality-baselines.json`:
  - ruff 90 → **273** (+183)
  - mypy_strict 401 → **629** (+228)
  - pylint_score 10.0 → **8.74** (lower = worse)
  - eslint 0 → **2** (+2)
- `Security`, `synthetic-monitor`, `flakiness-detector` ❌ — operator-side, smoke blocked per `smoke_test_blocked_2026-05-26.md`.

### Deploy-gate analysis
`deploy-backend.yml` triggers on `push: branches:[main] paths:[backend/**, src/**]` — **independent of Quality Ratchet**. The only frontend-side hard CI gate is the `--max-warnings 0` ESLint check.

## ESLint fix (deploy-gate cleared)

CI eslint count 2 came from two findings (both from `eslint-plugin-react-hooks@7.0.1`):

1. `frontend/app/components/OfflineBanner.tsx:25` — `react-hooks/set-state-in-effect` (ERROR). `setIsOnline(navigator.onLine)` inside a `useEffect` body triggers cascading renders.
   - **Fix**: refactor to `useSyncExternalStore` with `subscribeOnline` + `getOnlineSnapshot` + `getOnlineServerSnapshot = () => true`. SSR-safe; matches the existing `queuedCount` pattern in the same file. Removes the `useState` + setState-in-effect entirely.
2. `frontend/app/components/LeadTable.tsx:130` — `react-hooks/incompatible-library` (WARN, counted under `--max-warnings 0`). `useVirtualizer` from `@tanstack/react-virtual` lacks React Compiler annotations.
   - **Fix**: `// eslint-disable-next-line react-hooks/incompatible-library` above the call. Library-side issue; skip is harmless.

Additional local-only fix: added `.cx_*` to `globalIgnores` in `frontend/eslint.config.mjs` so context-mode artifact dotfiles (gitignored, not in CI) stop tripping the local pre-commit hook.

Autofix in `frontend/e2e/*.spec.ts` removed 5 unused `eslint-disable` directives (no-console / no-constant-condition).

## Quality-ratchet (ruff/mypy/pylint) regressions — NOT fixed

- Baseline file last refreshed 2026-05-23 (`e8c325f` mutmut, `63a3a65` initial). Predates the entire Phase 14/15 stack of 2026-05-24/25/26.
- Likely mixed cause: real new findings from Phase 14/15 merges + py3.12 → py3.14 stdlib stub diff (CI runner resolves `python-version: '3.12'` to **Python 3.14.5** per the action's hostedtoolcache log).
- Single-session line-by-line fix unrealistic (700+ findings). Defer to a deliberate baseline-refresh PR with audit.
- **Does not gate deploy.**

## Mishap — `pre-commit run --all-files` autoformat splatter

Running `pre-commit run --all-files` during step 8 of the sweep silently autoformatted **200 unrelated files** (SKILL.md trailing newlines, `backend/main.py` import reformat +759 LOC re-wrap, isort across many test files). All genuine quality improvements but unrelated to the task — entangled my 3 deliberate edits in a 203-file diff.

### Recovery recipe (load-bearing)

`git stash push -- <paths>` silently NO-OPs but leaves the index staged when **any** path in the pathspec is tracked-but-gitignored (`.agents/*` here). The empty stash misleads — files remain staged.

Correct revert (preserves my deliberate edits, restores everything else to HEAD):

```bash
git diff --name-only | grep -vE "<keep-pattern>" > /tmp/revert-list.txt
xargs git restore --source=HEAD --staged --worktree -- < /tmp/revert-list.txt
```

`--source=HEAD --staged --worktree` is the key — it restores both index and worktree to the committed state, bypassing the "restore from index" default behavior that does nothing when the autofix is already staged.

### Lesson

Never run `pre-commit run --all-files` mid-session unless you're committing the full autofix bundle. Use scoped form instead:
- `pre-commit run <hook-id> --files <list>` (one hook, narrow file set)
- `pre-commit run <hook-id> --all-files` (one hook, all files — predictable single-tool surface)

Persisted to memory as `feedback_precommit_all_files_splatter.md`.

## Working-tree state at session end

7 files modified, uncommitted:
- `frontend/app/components/OfflineBanner.tsx` — useSyncExternalStore refactor
- `frontend/app/components/LeadTable.tsx` — incompatible-library disable
- `frontend/eslint.config.mjs` — `.cx_*` global ignore
- `frontend/e2e/{csv-upload,full-flow,locale,memory-soak}.spec.ts` — 5 unused-disable removals

1 untracked from another session (`tests/loadtest/smoke-test-2026-05-26.md`) — left alone.

## Recommended next actions

1. Commit the 7-file fix bundle as `fix(frontend): ESLint --max-warnings 0 — quality-ratchet eslint=0`.
2. Open a separate planning PR for ruff/mypy/pylint baseline refresh (with audit of what the new findings actually are vs python-stub-diff artifact).
3. Install `prettier@3.8.3` dev-locally (`npm install --save-dev` in `frontend/`) so the pre-commit `prettier-frontend` hook works for everyone without a globally cached prettier.
4. Render deploy unblocked code-side; remaining blockers are operator-side per `smoke_test_blocked_2026-05-26.md`.
