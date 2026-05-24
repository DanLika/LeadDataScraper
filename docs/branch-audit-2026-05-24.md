# Branch audit — 2026-05-24

Audit-first sweep of every local branch in the repo after the session's
21-PR merge train. Captured post `0da9d58` (main HEAD).

## Before / after

| Metric | Before audit | After audit |
|---|---|---|
| Local branches | 31 | **28** |
| Remote branches | 133 | 133 (no remote pruning this audit) |
| Worktrees | 5 (4 sibling + 1 my audit worktree) | 4 (after audit worktree removal) |
| Stashes | 5 | 5 (all sibling-annotated, preserved) |
| Open PRs | 27 | 27 |

## Per-branch verdicts (29 branches inspected, excluding `main` and the audit branch itself)

| Branch | Verdict | Unique commits | PR | Action |
|---|---|---|---|---|
| `chore/merge-sprint-2026-05-24` | ALREADY_IN_MAIN | 0 | — | **deleted** (HEAD was `bd4dab5`, also session-start main HEAD) |
| `fix/lifespan-prime-order-2026-05-24` | ALREADY_IN_MAIN (squash) | 1 | — | **deleted** — content shipped via `d402911` (#288 squash); local SHA `86b693b` is the unsquashed source |
| `fix/lockfile-py310-backports-2026-05-24` | ALREADY_IN_MAIN (squash) | 1 | — | **deleted** — content shipped via `3affadc` (#287 squash); local SHA `b4448ae` is the unsquashed source |
| `chore/merge-sprint-2026-05-24-v2` | SIBLING_WT | 0 | — | leave — `~/git/lds-merge-2026-05-24` worktree |
| `feature/demo-data-seed-13.3` | SIBLING_WT | 1 | #285 | leave — `~/git/LeadDataScraper` worktree + held PR |
| `feature/email-resend-sender` | SIBLING_WT | 2 | #281 | leave — `/tmp/lds-resend-pr1-1779611218` worktree + held PR |
| `feature/email-schema-pr2` | SIBLING_WT | 1 | #286 | leave — `/tmp/lds-email-schema-1779615311` worktree + held PR |
| `chore/revoke-trigger-fn-grants-A10-opus47-v2` | OPERATOR_HOLD | 1 | #250 | leave — GRANT revoke, manual SQL review |
| `chore/t3-grants-harden-and-seo-index` | OPERATOR_HOLD | 1 | #230 | leave — account_deletions GRANT, manual SQL review |
| `chore/backend-security-headers-A7-opus47-v2` | OPEN_PR | 2 | #238 | leave — design overlap with #255 Phase D plan |
| `chore/claude-md-bookbed-crossover-session-2026-05-23` | OPEN_PR | 1 | #256 | leave — CLAUDE.md churn cluster |
| `chore/fix-p0-signout-prod-2026-05-23` | OPEN_PR | 3 | #227 | leave — stacked on phase15 branch |
| `chore/mutation-test-baseline-2026-05-23` | OPEN_PR | 2 | #259 | leave — memory says shipped; re-verify before re-merge |
| `chore/typecov-phase2-gemini-types` | OPEN_PR | 1 | #261 | leave — CONFLICTING, large refactor |
| `chore/visual-baselines-2026-05-23` | OPEN_PR | 9 | #260 | leave — CONFLICTING, snapshot binaries |
| `docs/claude-md-dogfood-prep-2026-05-23` | OPEN_PR | 1 | #252 | leave — CLAUDE.md churn cluster |
| `docs/claude-md-gemini-types-2026-05-24` | OPEN_PR | 2 | #273 | leave — stacked on #261 |
| `docs/claude-md-head-swap-mitigation-2026-05-23` | OPEN_PR | 1 | #257 | leave — CLAUDE.md churn cluster |
| `docs/claude-md-phase910-followups-2026-05-23` | OPEN_PR | 1 | #279 | leave — Phase 9.10 docs |
| `docs/claude-md-session-2026-05-23` | OPEN_PR | 1 | #236 | leave — CLAUDE.md churn cluster |
| `fix/skip-ai-on-bot-blocked-2026-05-23` | OPEN_PR | 1 | #277 | leave — CONFLICTING test mock |
| `chore/dogfood-prep-2026-05` | UNIQUE_NO_PR | 8 | — | **leave** — predecessor of merged `chore/dogfood-prep-2026-05-clean` (#284); commits don't trivially overlap, defer per HARD STOP "Unsure → KEEP" |
| `chore/render-restore-2026-05-23` | UNIQUE_NO_PR | 1 | — | **leave** — my session's runbook skeleton (`68128ea`); content superseded by `docs/final-cleanup-2026-05-24.md` (PR #294) but the doc itself didn't ship verbatim. Operator can `git branch -D` after reviewing diff. |
| `docs-253-append` | UNIQUE_NO_PR | 2 | — | leave — part of "Session 2026-05-23 drain + docs stack" (memory); the PR was squash-merged so unique SHAs survive |
| `docs-254-fix` | UNIQUE_NO_PR | 5 | — | leave — same stack |
| `docs-258-extend` | UNIQUE_NO_PR | 8 | — | leave — same stack |
| `fix/inter-font-drop-2026-05-23-clean` | UNIQUE_NO_PR | 1 | — | leave — likely duplicate of PR #239 source branch (different SHA, same intent); operator can verify + delete |
| `rescue/audit-2026-05-24-a2ec2f7` | UNIQUE_NO_PR | 4 | — | **leave** — by-design rescue branch (per memory, recovery snapshot). Never delete without operator confirmation. |
| `rescue/audit-2026-05-24-d6aa160` | UNIQUE_NO_PR | 3 | — | **leave** — same |

## Deletions executed (3)

- `chore/merge-sprint-2026-05-24` (was `bd4dab5`)
- `fix/lifespan-prime-order-2026-05-24` (was `86b693b`)
- `fix/lockfile-py310-backports-2026-05-24` (was `b4448ae`)

All three: content verifiably in main, no commits lost.

## Cherry-picks: none

No `PARTIAL_IN_MAIN` cases. Open PRs that need attention are listed
above; cherry-picking them piecemeal in this audit would either
(a) duplicate operator-decision work, (b) collide with sibling-session
PRs, or (c) risk pulling unreviewed code into main. Per the plan
defaults, all defer to operator review.

## Stashes (5, all preserved per safety rule)

Same as prior session — all carry sibling-session annotations. Will
remain until those sessions either pop them or explicitly drop them:

  stash@{0}: On chore/clear-smoke-debt-2026-05-24: wip-schema-before-i18n (re-stashed after accidental pop in lds-lockfile-fix worktree 2026-05-24)
  stash@{1}: On chore/render-restore-2026-05-23: wip-claude-md-email-stack
  stash@{2}: On chore/render-restore-2026-05-23: claude-md-mutmut-paragraph-preserve-2026-05-24
  stash@{3}: On chore/email-stack-plan: sibling mutmut baseline docs (do-not-merge here)
  stash@{4}: On chore/claude-md-bookbed-crossover-session-2026-05-23: parallel-session-snapshot-2026-05-23-foreign-do-not-discard

## Worktrees (4 + 1 audit)

  ~/git/LeadDataScraper           → feature/demo-data-seed-13.3      (SIBLING)
  /private/tmp/lds-email-schema-* → feature/email-schema-pr2          (SIBLING)
  /private/tmp/lds-resend-pr1-*   → feature/email-resend-sender       (SIBLING)
  ~/git/lds-merge-2026-05-24      → chore/merge-sprint-2026-05-24-v2  (SIBLING)
  /tmp/lds-audit                  → chore/branch-audit-2026-05-24     (this audit's worktree — removed at audit end)

None mine to clean except the audit worktree itself.

## What this audit deliberately did NOT do

- **No cherry-picks**. Open PRs are owned by their respective workflows
  (operator decisions, sibling sessions, dependabot). Plucking commits
  out of them would short-circuit review.
- **No remote-branch deletions**. `gh pr merge --delete-branch` already
  handled the closed-PR remote cleanup; pruning further (e.g. orphan
  remotes from abandoned forks) is a separate maintenance task.
- **No stash drops**. Every stash carries a sibling-session marker;
  dropping them is the sibling's call.
- **No sibling worktree edits**. Three sibling-owned worktrees + one
  on the main repo path — all untouched.

## Outstanding (carryover from prior session-end report)

Unchanged from `docs/final-cleanup-2026-05-24.md`:

1. Render `lead-scraper-backend` Failed deploy → Logs paste
2. Render `lead-scraper-frontend` HTTP 500 → env vars verify
3. DB integrity re-run under authed Supabase session
4. 5 operator-decision PRs: #230, #250, #281, #285, #286
5. CLAUDE.md churn cluster: #236, #252, #256, #257, #260
6. 9 dependabot PRs (#213–#222)
7. #138 (asyncio.gather AI router — needs Semaphore + budget pre-gate)
8. #277 (skip-ai-on-bot-blocked — CONFLICTING test mock, needs rebase)

## Audit branch lifecycle

This doc lands on `chore/branch-audit-2026-05-24` → squash-merge to
main → branch + worktree removed.
