# Session 2026-05-23 — drain crossover gaps (#227 #231 #237)

Extracted from `CLAUDE.md` (2026-05-26 shrink; original ~164k chars). Restored to docs/ to keep CLAUDE.md under the harness threshold without losing content.

# Session 2026-05-23 — drain crossover gaps (#227, #231, #237)

Items shipped during the 2026-05-23 drain that fell outside the
scope of PR #253 (drain PRs #235–#251) and PR #254 (Phase 15 finding
matrix). Pinned here so future audits don't re-discover them.

## Cross-origin header backport from bookbed-website (PR #237)

`frontend/next.config.ts::baseHeaders` now stamps three additional
headers on every response, completing the bookbed-website parity
gap called out in `docs/bookbed-crossover.md`:

- `Cross-Origin-Opener-Policy: same-origin` — isolates the
  browsing-context group so cross-window timing attacks (Spectre
  class) lose access. Drops `window.opener` references from
  other-origin windows that linked in.
- `Cross-Origin-Resource-Policy: same-origin` — other origins
  can't pull our responses as `<img>` / `<script>` / `<iframe>`
  subresources. Supabase + Sentry are reached via the
  Next.js proxy / official browser SDK, both same-origin from
  the dashboard's perspective — no breakage.
- `X-Permitted-Cross-Domain-Policies: none` — legacy Flash /
  Adobe Reader gating; defensive against any rehydrated PDF
  payload on a stale tab.

No middleware change required — the existing `headers()` block in
`next.config.ts` already covers every route the frontend ships.
CSP / HSTS / XFO / XCTO / Referrer-Policy stay where they are
(`frontend/proxy.ts` per-request for CSP, `next.config.ts` static
for the rest).

## `.gitignore` gap for frontend exports (PR #231)

Root `.gitignore` had `exports/` which (per gitignore glob
semantics — slash → `FNM_PATHNAME`) matches only the root-level
`exports/` directory. CSV artifacts written by export scripts run
from the `frontend/` working dir landed at `frontend/exports/` and
showed up untracked. PR adds `frontend/exports/` as an additional
pattern.

Pattern to remember: a gitignore rule with a trailing slash AND a
slash inside (or implied path from being non-leading) is
**anchored to the repo root**. `exports/` is anchored;
`**/exports/` or a bare `exports/` at every depth would match
nested directories. Surfaced during Phase 16-T1's `git status -s`
sweep.

## P0a Sign Out retraction (PR #227)

Phase 15 finding #1 ("Sign Out click no-ops on prod") was a
**false positive from a stale build**, not a real handler bug.
Root cause:

```bash
pkill -f "next start" -f "uvicorn backend"
```

On macOS (BSD `pkill`), only the LAST `-f <pattern>` is honored —
the previous `next-server` (PID 59710 from 2026-05-22 18:53) was
never killed and kept serving cached pre-build output instead of
the rebuild. chrome-devtools-mcp tested the cached build whose
React tree had no Sign Out handler.

**Operational rule pinned forward**: when restarting multiple
services, use SEPARATE `pkill` invocations AND verify with
`pgrep -f "<pattern>"` returning exit 1 before claiming a fresh
build is under test:

```bash
pkill -f "next start"; pkill -f "uvicorn backend"
pgrep -f "next start" || echo "next clean"
pgrep -f "uvicorn backend" || echo "backend clean"
```

Re-test path: add `console.log` at the handler entry FIRST,
rebuild, re-test. Confirms whether the click event reaches the
handler vs being lost upstream by stale React tree.

Same root cause likely poisoned other Phase 15 findings that
relied on the same restart command; cross-check before re-running
any test that was negative on Phase 15 + positive on Phase 16.

## Docs-PR stack via sequential rebase

When two or more docs PRs append to the same insertion point in
CLAUDE.md (every session uses "after the last existing section"),
naive parallel branches all conflict on the same diff hunk. The
2026-05-23 session resolved three colliding PRs (#253 + #254 + #258)
into a deterministic merge stack:

```
main ← #253 ← #254 ← #258
```

Each PR is rebased onto the previous PR's tip, so its append now
lands AFTER the prior block. When the bottom of the stack merges to
main, GitHub auto-rebases the next PR; the diff collapses to that
PR's own additions only.

Recipe per PR (worked example for #254 onto #253):

```bash
# 1. Dedicated worktree off the PR's remote branch
git worktree add /tmp/lds-254-fix \
  origin/docs/claude-md-phase15-session-2026-05-23
cd /tmp/lds-254-fix
git checkout -b docs-254-fix

# 2. Rebase onto the previous PR in the stack
git fetch origin --quiet
git rebase origin/docs/claude-md-drain-2026-05-23-opus47-v2

# 3. Resolve conflict — keep BOTH blocks
#    (CLAUDE.md "Session …" headings stack vertically)
# 4. git add + git rebase --continue

# 5. Push with safety net — fail if remote moved since last fetch
git push --force-with-lease=docs/claude-md-phase15-session-2026-05-23:<remote-tip-sha> \
  origin docs-254-fix:docs/claude-md-phase15-session-2026-05-23
```

Key invariants:

- **`--force-with-lease=<branch>:<expected-tip>` not bare `--force`.**
  If a parallel session pushed to the same remote branch since the
  local fetch, lease comparison fails and aborts — your unseen
  collaborator's commits are NOT clobbered. Bare `--force` would
  overwrite silently.
- **Resolve in the rebased branch, not the base.** Don't touch the
  base PR you're stacking on — it stays exactly as its author left
  it. The rebase only affects YOUR PR's commits.
- **Comment the stack order on every PR.** When #253 merges,
  GitHub auto-rebases #254 onto main; if a reviewer merges #254
  first by mistake, the auto-rebase target is wrong and #253's
  content goes to main via #254's PR. Make the order visible in the
  description AND a top-comment.
- **Worktree, not main checkout.** The parallel-session contention
  problem (CLAUDE.md "Multi-session worktree race" — same session
  block) bites if you rebase in the shared `~/git/LeadDataScraper`
  worktree while another session has a HEAD there.

What to do if the stack contains a PR you don't own (parallel
session): leave its base alone, don't change `gh pr edit --base`,
don't force-push the parallel session's branch unless you control
that session. Only ever rebase the PRs YOU created. For someone
else's PR, the comment + merge-order documentation IS the fix.

What this does NOT fix:

- If the base PR is **rejected** instead of merged, the stacked PRs
  still carry its content. Inspect each PR's diff against main
  before merging — if base content is unwanted, revert the rebase
  with `git reset --hard origin/<your-branch>@{1}` (use reflog).
- Stack-of-3 was manageable; stack-of-N for large N gets fragile.
  Beyond 3-4, switch to a single combined PR or a docs-only train
  branch.
