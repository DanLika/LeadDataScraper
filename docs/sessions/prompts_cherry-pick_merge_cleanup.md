# Prompts — cherry-pick / merge stack / delete branches / cleanup

Copy-paste ready. Run when `pgrep -c "claude --dangerously"` returns `1`.

---

## PROMPT 1 — Pre-flight safety check

```
PRE-FLIGHT — before any write ops.

1. Confirm sole session:
   pgrep -c "claude --dangerously" must == 1. Stop if > 1.
2. Confirm canonical worktree clean:
   cd /Users/duskolicanin/git/LeadDataScraper
   git status (must be clean) + git branch --show-current (must be main).
3. Refresh main:
   git fetch origin --prune && git reset --hard origin/main
4. List open PRs to act on:
   gh pr list --state open --limit 50 --json number,title,headRefName,mergeStateStatus
5. Confirm CI green policy:
   For each PR to merge, check ci.yml run conclusion + diff failure set vs
   most-recently-merged baseline PR. Block admin-merge if any NEW failure
   not already in pre-existing catalogue.

Report findings. Wait for go-ahead.
```

---

## PROMPT 2 — Cherry-pick recipe (single commit → main)

Use when you want ONE commit from a stack PR onto main without merging the whole PR.

```
CHERRY-PICK — single commit onto main.

INPUT: SHA=<commit-sha>, REASON=<one-line why this commit goes alone>

Hard rules:
- main only — no force-push to main
- if cherry-pick fails with conflict, ABORT (git cherry-pick --abort), do NOT
  resolve in cherry-pick; open a fixup PR instead.

1. Worktree isolation (avoids parallel-session HEAD swap):
   git worktree add /private/tmp/lds-cherry-$SHA origin/main
   cd /private/tmp/lds-cherry-$SHA
   git checkout -b cherry/$SHA

2. Pick:
   git cherry-pick -x $SHA   # -x records origin SHA in message

3. Verify single-commit diff is what you expected:
   git show HEAD --stat
   git log -1 --format='%B'

4. Push as PR (not direct push to main):
   git push -u origin cherry/$SHA
   gh pr create --base main --head cherry/$SHA \
     --title "cherry: <SHORT> ($SHA)" \
     --body "Cherry-pick of $SHA. Reason: $REASON"

5. After PR green + merged:
   cd /Users/duskolicanin/git/LeadDataScraper
   git worktree remove /private/tmp/lds-cherry-$SHA --force
```

---

## PROMPT 3 — Stack merge into main (sequential, 13 PRs)

```
STACK MERGE — Phase 14+15 stack onto main, sequential.

ORDER (do NOT reorder — dependencies):
  #281 → #286 → #319 → #320 → #321 → #322 → #323 → #324 →
  #325 → #326 → #327 → #328 → #330

Hard rules (carry forward from prior session):
- NEVER admin-merge if remaining CI failures are NEW (compare to most-
  recently-merged PR on main).
- NEVER skip ahead in stack — sequential dependencies.
- NEVER bulk-squash — each PR keeps own commit history.
- Squash-merge (not merge-commit, not rebase-merge) — one PR = one commit
  on main, authorship preserved.
- Between each merge: refresh main, rebase next PR, re-run CI, wait for it.

Per-PR loop:
  PR=<num>

  # 1. Rebase PR onto current main in its worktree
  cd /private/tmp/lds-<wt>     # use existing stack worktree
  git fetch origin --quiet
  git rebase origin/main
  # resolve conflicts manually, NEVER --force-skip

  # 2. Push with safety net
  git push --force-with-lease=<branch>:<remote-tip-sha>

  # 3. Wait for CI
  gh pr checks $PR --watch

  # 4. Verify failures are pre-existing catalogue only
  gh pr view $PR --json statusCheckRollup --jq '[.statusCheckRollup[] | select(.conclusion=="FAILURE") | .name]' | sort > /tmp/fail_$PR.txt
  baseline=$(gh pr list --state merged --base main --limit 1 --json number -q '.[0].number')
  gh pr view $baseline --json statusCheckRollup --jq '[.statusCheckRollup[] | select(.conclusion=="FAILURE") | .name]' | sort > /tmp/fail_baseline.txt
  new_fails=$(comm -23 /tmp/fail_$PR.txt /tmp/fail_baseline.txt)
  if [ -n "$new_fails" ]; then
    echo "NEW FAILURES — STOP. Investigate:"
    echo "$new_fails"
    exit 1
  fi

  # 5. Merge (admin-merge ok ONLY if step 4 passed)
  gh pr merge $PR --squash --admin --delete-branch

  # 6. Local refresh
  cd /Users/duskolicanin/git/LeadDataScraper
  git fetch origin --prune
  git reset --hard origin/main

  # 7. Worktree cleanup for merged PR
  git worktree remove /private/tmp/lds-<wt> --force

Report after each merge: PR# / commit-sha-on-main / next-up.
```

---

## PROMPT 4 — Delete closed/merged local branches

```
LOCAL BRANCH CLEANUP — remove branches with closed/merged PRs.

Hard rules:
- NEVER delete branch attached to a worktree (git worktree list).
- NEVER delete branch with OPEN PR.
- NEVER delete branch with unpushed commits (git log origin/<branch>..<branch>).

cd /Users/duskolicanin/git/LeadDataScraper

# Collect worktree-bound branches (skip these)
worktree_branches=$(git worktree list --porcelain | awk '/^branch /{sub("refs/heads/","",$2); print $2}' | sort -u)

# Walk all local branches except main + current
for br in $(git branch --format='%(refname:short)' | grep -vE '^(main|\* )'); do
  # Skip worktree-bound
  if echo "$worktree_branches" | grep -qx "$br"; then
    echo "[WORKTREE-SKIP] $br"
    continue
  fi

  # Find PR for branch
  pr_data=$(gh pr list --state all --head "$br" --json number,state -L 1 -q '.[0]' 2>/dev/null)
  pr_num=$(echo "$pr_data" | jq -r '.number // empty')
  pr_state=$(echo "$pr_data" | jq -r '.state // empty')

  if [ -z "$pr_num" ]; then
    # No PR ever — risky to auto-delete. Skip + flag.
    echo "[NO-PR-SKIP] $br (operator must decide)"
    continue
  fi

  if [ "$pr_state" = "OPEN" ]; then
    echo "[OPEN-SKIP] $br (PR#$pr_num)"
    continue
  fi

  # MERGED or CLOSED — safe to delete
  git branch -D "$br" && echo "[DELETED] $br (PR#$pr_num $pr_state)"
done

# Prune remote-tracking refs for deleted remote branches
git remote prune origin

# Report final branch count
echo "---"
git branch --format='%(refname:short)' | wc -l
git branch
```

---

## PROMPT 5 — Worktree cleanup (merged PRs only)

```
WORKTREE CLEANUP — remove worktrees whose PR is merged.

cd /Users/duskolicanin/git/LeadDataScraper

# Walk every /private/tmp/lds-* worktree
for wt in $(git worktree list --porcelain | awk '/^worktree/{print $2}' | grep '^/private/tmp/lds-'); do
  basename=${wt##*/lds-}

  # Numeric basename = PR number
  if [[ "$basename" =~ ^[0-9]+$ ]]; then
    state=$(gh pr view "$basename" --json state -q .state 2>/dev/null)
    if [ "$state" = "MERGED" ] || [ "$state" = "CLOSED" ]; then
      git worktree remove "$wt" --force
      echo "[REMOVED] $wt (PR#$basename $state)"
    else
      echo "[KEEP] $wt (PR#$basename $state)"
    fi
    continue
  fi

  # Named-branch basename — match to branch name
  branch=$(git worktree list --porcelain | awk -v p="$wt" '$0=="worktree "p{found=1} found && /^branch /{sub("refs/heads/","",$2); print $2; exit}')
  if [ -n "$branch" ]; then
    state=$(gh pr list --state all --head "$branch" --json state -L 1 -q '.[0].state // empty' 2>/dev/null)
    if [ "$state" = "MERGED" ] || [ "$state" = "CLOSED" ]; then
      git worktree remove "$wt" --force
      echo "[REMOVED] $wt (branch=$branch PR=$state)"
    else
      echo "[KEEP] $wt (branch=$branch PR=$state)"
    fi
  else
    # Detached HEAD with non-numeric basename — manual review
    echo "[MANUAL] $wt (detached, non-numeric name)"
  fi
done

git worktree prune
echo "---"
git worktree list
```

---

## PROMPT 6 — Full pipeline (do everything in correct order)

```
FULL PIPELINE — pre-flight + stack merge + cleanup.

Run ALL of these in this exact order:

1. PROMPT 1 (pre-flight). STOP if any check fails.
2. PROMPT 3 (stack merge). Run per-PR loop for 13 PRs. STOP on first NEW
   failure.
3. PROMPT 4 (local branch cleanup).
4. PROMPT 5 (worktree cleanup).
5. Final sanity:
   git fetch origin --prune
   git status (clean)
   git log --oneline -20 (verify stack landed in order)
   gh pr list --state open --limit 50 (only intentionally-deferred remain)
   pgrep -c "claude --dangerously" (still == 1)

Report final state. Do NOT merge docs PRs (#306, #329) until operator
confirms — those are independent and need separate review.
```

---

## PROMPT 7 — Emergency recovery (if stack merge breaks)

```
RECOVERY — if stack merge produces a broken main.

Hard rule: NEVER revert by force-pushing to main. Always use revert PR.

1. Identify bad commit:
   git log --oneline -10
   # find the commit-sha that caused the break (test failure, missing file, etc.)

2. Create revert PR:
   git checkout -b revert/<short>-<sha>
   git revert <sha>
   git push -u origin revert/<short>-<sha>
   gh pr create --base main --title "revert: <PR-title> (#<original-PR>)" \
     --body "Reverts <sha>. Reason: <why>. Original PR #<num> will need re-work + re-submission."

3. After revert green + merged:
   - Reopen original PR (or open new one) with the fix on top.
   - Document the failure mode in CLAUDE.md if it's a pattern worth pinning.
```

---

## Quick reference — current stack state (2026-05-26)

```
Stack PRs (sequential, MUST merge in order):
  #281  feature/email-resend-sender                           UNKNOWN — needs rebase
  #286  feature/email-schema-pr2                              UNKNOWN — needs rebase
  #319  feature/dispatcher-provider-cols                      UNSTABLE — 6 NEW fails vs baseline
  #320  feature/instantly-dispatcher-14.1                     UNSTABLE
  #321  feature/dispatch-hardening-14.2-A-suppressions        UNSTABLE
  #322  feature/dispatch-hardening-14.2-B-thread-rfc8058      UNSTABLE
  #323  feature/dispatch-hardening-14.2-C-webhook             UNSTABLE
  #324  feature/dispatcher-roundtrip-14.3                     UNSTABLE
  #325  feature/sequencing-15.1-schema                        UNSTABLE
  #326  feature/sequencing-15.2-dispatch-tick                 UNSTABLE
  #327  feature/sequencing-15.3-render                        UNSTABLE
  #328  feature/sequencing-15.4-advance                       UNSTABLE
  #330  chore/ruff-cleanup-phase14-15                         UNSTABLE

Independent docs (merge AFTER stack):
  #306  Render restore
  #329  Session log

DO NOT auto-merge (needs-investigation):
  #220  numpy bump (real test failures)
  #230, #250, #309  (needs-investigation labels)
  #316  gitleaks baseline (incomplete — broader audit needed)

Major bumps (per-PR changelog review):
  #216 @types/node, #218 lucide-react, #222 eslint 9→10,
  #310 typescript 5.9→6.0, #312 google-genai 1→2,
  #313 aiofiles 23→25, #332 npm-prod group
```
