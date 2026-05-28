# Session 2026-05-23 — branch hygiene: HEAD swap BETWEEN turns

Extracted from `CLAUDE.md` (2026-05-26 shrink; original ~164k chars). Restored to docs/ to keep CLAUDE.md under the harness threshold without losing content.

# Session 2026-05-23 — branch hygiene: HEAD swap BETWEEN turns (cross-session)

Observation logged this session: when multiple Claude sessions on
the same repo run in parallel against ONE working tree, HEAD can
flip BETWEEN turns of a single session — not just during a single
session's own branch checkouts. Observed sequence in one ~3-minute
interaction:

1. Session opens; startup `git status` reports branch A
   (`docs/claude-md-dogfood-prep-2026-05-23`) with a known dirty
   working tree.
2. User answers a clarification question. Several seconds pass.
3. Next tool batch runs `git status` — branch B
   (`chore/visual-baselines-2026-05-23`) with an entirely different
   foreign dirty working tree (modified `.py` files + untracked
   `.py.bak` editor backups belonging to a parallel session).
4. The next tool call (`git stash push -u`) reports being on
   branch C (`chore/claude-md-bookbed-crossover-session-2026-05-23`).

Three distinct branches in a single short interaction without this
session running a single `git checkout`. The recovery path that
worked in the earlier "wrong-branch commit" cases documented in
Session 2026-05-22 (cherry-pick + reset) does NOT apply here
because nothing has been committed yet — the foreign work is in
the working tree and the writing session would silently `git add`
files it has no context for.

**Mitigation that worked (in order):**
1. `git stash push -u -m "parallel-session-snapshot-<date>-foreign-do-not-discard"`
   captures foreign work (untracked files included via `-u`)
   without deleting it. The descriptive message tells the parallel
   session it can `git stash apply` to recover. Do NOT `git stash
   drop` — the foreign work is not yours to discard.
2. `git fetch origin main && git worktree add -b <new-branch>
   ../<sibling-dir> origin/main` creates an ISOLATED worktree at
   a sibling path. Each worktree has its own HEAD file under
   `.git/worktrees/<name>/HEAD`, so the parallel session cannot
   swap the new worktree's HEAD even if it manipulates the
   primary worktree's HEAD.
3. `git symbolic-ref HEAD` inside the worktree confirms the
   isolated HEAD stays put across tool calls.

The earlier single-worktree mitigation (`git symbolic-ref HEAD`
right after `git checkout -b` and before the first edit) covers
only the FIRST tool batch on a branch. It does NOT cover the
case above where HEAD swaps mid-session after several stable
turns. Adding an explicit HEAD re-check before EACH write batch
is a partial mitigation; the durable fix is to use a dedicated
worktree per Claude session when more than one session may run
on the same repo.

**Adoption rule for future multi-session work on this repo:**
when a new Claude session starts on a repo that another session
already has open, the new session's first action should be
`git worktree add -b <new-branch> ../<sibling-dir> origin/main`,
then anchor all subsequent work to that sibling path (absolute
paths in tool calls; the Bash shell cwd resets between calls and
will jump back to the primary worktree). The primary working
tree stays reserved for the original session.

Cleanup when the session ends: `git worktree remove
../<sibling-dir>`. If the branch has unpushed commits, push first
or the remove refuses without `--force`.
