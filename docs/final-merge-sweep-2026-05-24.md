# Final merge sweep — 2026-05-24

Branch / PR / worktree / stash inventory + triage plan for the end-of-week
sweep. Captured mid-session while the Render restore is in flight.

## Snapshot (2026-05-24 ~10:35 UTC)

| Surface | Count |
|---|---|
| Local branches | 52 |
| Remote branches | 164 |
| Worktrees | 7 (4 sibling-session, 3 mine) |
| Stashes | 5 (all sibling-session annotations) |
| Open PRs | 45 |

### Main HEAD trajectory this session

  d402911 fix(lifespan): prime lazy globals before single-tenant assertion (#288)
  3affadc fix(lockfile): pin exceptiongroup + tomli (py3.10 stdlib backports) (#287)
  5f9c451 fix(lockfile): pin async-timeout==5.0.1 (py3.10 transitive of aiohttp) (#283)
  2cd9e57 docs(merge-sprint): correct root cause (runner alloc) + add merge-base canary

Three Render-restore fixes merged in this session. Render backend is
expected to come up once the operator clicks Manual Deploy
(restart-loop terminated before the last auth fix landed; see Render
restore doc below).

## Render restore status

See [`render-restore-2026-05-23.md`](render-restore-2026-05-23.md) for
the full doc.

| Surface | State | Blocking |
|---|---|---|
| Backend `lead-scraper-backend` | Built OK after #283 + #287; lifespan crashes pre-#288; auth row missing token defaults; auto-restart loop exited at 10:27Z | Operator: Manual Deploy `d402911` |
| Frontend `lead-scraper-frontend` | Deploy live but every page route 500s (middleware throws before CSP set) | Operator: verify `NEXT_PUBLIC_SUPABASE_URL` + `NEXT_PUBLIC_SUPABASE_ANON_KEY` in Render env match canonical Supabase values |
| Supabase Auth users | 1 user (`duskolicanin1234@gmail.com`), tokens fixed to empty-string defaults via `UPDATE auth.users` | None |

## PR triage (45 open)

### Category A — Stale draft jules-bot-style (auto-generated suggestions, drafts)

Numbers <200, branch names follow auto-suggestion conventions, all flagged
as Drafts. Likely superseded by the post-2026-05-23 hardening drain.

| PR | Title | Verdict |
|---|---|---|
| #130 | code health — remove unused `export_facebook_links` | close-superseded (function may have been re-introduced or already pruned) |
| #131 | edge case tests for `extract_names` | review-and-cherry-pick if covers a real gap |
| #132 | add tests for `extract_names` edge cases | dup of #131 — close |
| #133 | tests for `error_response` function | review-and-cherry-pick |
| #135 | tests for `_is_table_missing_error` | review-and-cherry-pick |
| #136 | tests for API key verification failure | likely already covered by `tests/security/test_endpoint_hardening.py`; close-superseded |
| #138 | asyncio.gather in agentic_router | risky perf change — defer or close (touches AI surface) |

**Action**: skim each diff with `gh pr diff`. Cherry-pick the test
additions that close real coverage gaps (verify via existing test inventory
in CLAUDE.md "AI quality & safety test suite" section). Close the rest
with reference to the merged work that superseded them.

### Category B — Dependabot (9 open)

| PR | Bump | Verdict |
|---|---|---|
| #213 | Docker base `playwright/python` v1.40.0-jammy → v1.60.0-jammy | **HIGH VALUE** — v1.60.0 uses Python 3.11+, would obsolete async-timeout/exceptiongroup/tomli backport pins. Verify py version of v1.60.0 tag, then merge + remove backport pins in a follow-up. |
| #215 | npm-prod group, 4 updates in `frontend/` | review — minor/patch likely safe |
| #216 | `@types/node` 20.19.37 → 25.9.1 (major × 5) | major bump — defer, requires manual review |
| #217 | pip-patches group, 3 updates | safe — merge after local pytest |
| #218 | `lucide-react` 0.577.0 → 1.16.0 (major) | major — defer, icons may move |
| #219 | `pandas` 2.2.3 → 2.3.3 | minor — merge after pytest |
| #220 | `numpy` 2.2.3 → 2.4.6 | minor — merge after pytest |
| #221 | `playwright` 1.50.0 → 1.60.0 (Python lib) | pairs with #213 Docker base — merge together |
| #222 | `eslint` 9.39.4 → 10.4.0 (major) | major — defer, lint config may need updates |

**Action**: merge patches/minors after a quick local `pytest -q` or
`npm run build` per change. Defer all majors with a tracking note.

### Category C — Real human/claude work (~29)

Recent PRs (#227+). Includes both:
- This session's lockfile/lifespan trio (#283, #287, #288) — all merged
- Pre-existing drain from 2026-05-23: phase16 hardening, security headers, web-vitals, sign-out fix, etc.
- Phase 13 dogfood prep: i18n, demo data, email schema

Sub-categories from this session and prior:

  Already merged this session:
    #283 — async-timeout backport pin
    #287 — exceptiongroup + tomli backport pins
    #288 — lifespan priming order fix

  Still open, high priority:
    #247 — demo data (potentially superseded by #285 ship of Phase 13.3)
    #277 — skip-ai-on-bot-blocked (mock incompatible with #269 body-cap — needs rebase)
    #281 — Resend sender (53/53 tests at last check; may need rebase)
    #286 — email dispatch schema (HARD STOP — needs Supabase migration applied LIVE first)
    #284 — dogfood prep clean
    #285 — demo data seed
    #280 — cleanup (whatever this is — review)

  Documentation / chore series:
    #236, #238, #239, #242, #244, #245, etc. — claude-md and phase16 hardening trail. Most likely already merged or thin; need per-PR scan.

**Action**: per-PR check in Phase 3 of the sweep. Rebase + local
verification + local fast-forward merge (since CI is red — separate
unblock).

### Category D — Held / explicit operator decision

| PR | Why held |
|---|---|
| #248 / #255 | Verification disagreement (per memory `session_2026-05-23_pr_review_pass.md`) — operator must decide |
| #286 | Live Supabase migration required before merge (see Category C) |
| #213 / #221 (Playwright upgrade pair) | Cross-cutting; ideally bundled with backport removal |

## Worktree inventory

| Path | Branch | Owner | Action |
|---|---|---|---|
| `~/git/LeadDataScraper` | `feature/demo-data-seed-13.3` | Sibling session | **DO NOT TOUCH** |
| `/private/tmp/lds-email-schema-1779615311` | `feature/email-schema-pr2` | Sibling | DO NOT TOUCH |
| `/private/tmp/lds-lifespan-fix` | `fix/lifespan-prime-order-2026-05-24` | Mine (merged) | Cleanup after Phase 5 green |
| `/private/tmp/lds-lockfile-fix` | `chore/final-merge-sweep-2026-05-24` | **Mine (active)** | Sweep work happens here |
| `/private/tmp/lds-lockfile-fix2` | `fix/lockfile-py310-backports-2026-05-24` | Mine (merged) | Cleanup after Phase 5 green |
| `/private/tmp/lds-resend-pr1-1779611218` | `feature/email-resend-sender` | Sibling | DO NOT TOUCH |
| `~/git/lds-merge-2026-05-24` | `chore/merge-sprint-2026-05-24-v2` | Sibling | DO NOT TOUCH |

## Stash inventory

All five stashes carry sibling-session annotations. None are mine.
**Action: leave them — sibling sessions are the only safe parties to
drop them.**

  stash@{0}: On feature/i18n-croatian-phase-13.1: wip-schema-before-i18n
  stash@{1}: On chore/render-restore-2026-05-23: wip-claude-md-email-stack
  stash@{2}: On chore/render-restore-2026-05-23: claude-md-mutmut-paragraph-preserve-2026-05-24
  stash@{3}: On chore/email-stack-plan: sibling mutmut baseline docs (do-not-merge here)
  stash@{4}: On chore/claude-md-bookbed-crossover-session-2026-05-23: parallel-session-snapshot-2026-05-23-foreign-do-not-discard

## Phased execution plan

| Phase | Status | Budget | Notes |
|---|---|---|---|
| 1 Inventory | **In progress (this doc)** | 15min | About done. |
| 2 Jules-bot triage (7 PRs) | Pending | 30-60min | Skim diffs, cherry-pick test gaps, close superseded |
| 3 Real PRs + dependabot patches | Pending | 30-60min | Per-PR rebase + local merge. Skip majors. |
| 4 Verify ONE main | Pending | 10min | `git pull --ff-only`; only held categories remain open |
| 5 Comprehensive smoke | Pending | 1-2h | Backend pytest+mypy+ruff+drift; frontend tsc+build; local prod boot; chrome-devtools-mcp flow; DB integrity. **HARD STOP on any red.** |
| 6 Branch cleanup | Pending | 15-30min | Only after Phase 5 green. |

## Hard stops (reproduced from task spec)

- Any smoke phase red → stop at 5, no Phase 6, no deletion
- Cherry-pick conflict on >3 files → skip + document
- Local merge breaks main build → `git reset --hard origin/main`, investigate
- Jules-bot PR touching auth/RLS/secrets → manual review only, never blind cherry-pick
- Supabase migration needed but not applied → halt that PR
- Real operator account creds lost during smoke → STOP and surface

## Deliverables (to be filled as phases complete)

- [x] `docs/final-merge-sweep-2026-05-24.md` (this doc)
- [ ] `docs/final-smoke-2026-05-24.md`
- [ ] `~/.claude/projects/.../memory/session_2026-05-24_final-sweep.md`
- [ ] Open PR list reduced to: deps majors + email PRs 3-5 + operator decision items
- [ ] Smoke verdict (GO / NO-GO for dogfood Day 1) at end of smoke doc
