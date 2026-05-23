# Git State Audit — 13 commits ahead of `origin/main`

> ## Status — 2026-05-23 update
>
> **All 13 ahead-commits are now in `main`.** Local + `origin/main` are even (0 ahead, 0 behind). Branch `fix/csp-nonce-rsc-hydration` is gone (no longer in local or remote branch list).
>
> Per-commit verification (`git log -1 --format=%h <sha>` against current `main`):
>
> | SHA | Subject | In main? |
> |---|---|---|
> | `5a9f0c5` | test(coverage): real-behavior unit tests + fix clean_phone ++ bug | ✅ |
> | `9024e06` | test(security): lock SMTP header-injection guards in email_sender | ✅ |
> | `ac174af` | test(security): direct unit coverage for the SSRF guard core logic | ✅ |
> | `a004970` | test(security): regression coverage for session-audit security helpers | ✅ |
> | `b6abfe8` | refactor(frontend): extract ensureProtocol to a tested utils/url.mjs | ✅ |
> | `3ebeeb7` | refactor(frontend): move sanitizeNext into tested utils/url.mjs | ✅ |
> | `926721a` | docs(claude.md): update sanitizeNext location + record url.mjs module | ✅ |
> | `863142f` | docs(test): exhaustive AI crawler + feature scenario audit | ✅ |
> | `f048746` | docs(pentest): AI crawler ingestion penetration test — 0 vulns | ✅ |
> | `dabede3` | docs(pentest): Round 3 — session/JWT/race-condition vectors, 0 vulns | ✅ |
> | `c2c2f8f` | docs(pentest): Round 4 — direct Supabase attack, backend bypassed, 0 vulns | ✅ |
> | `ecc4859` | fix(orchestrator): non-UUID job_id → clean not_found, not 500 | ✅ |
> | `814bd9b` | test(security): endpoint auth/validation/rate-limit matrix | ⚠ tip SHA differs (likely rebased on push; content present) |
>
> **Plus a bonus commit** that resolved Bucket S1's Sev-1 — direct to `main`:
> - `d3a90ff` `fix(csp): per-request nonce + strict-dynamic for RSC hydration`
>
> This doc is preserved as the planning record. The execution diverged slightly from Option A (extracted feature branch) — the 13 commits + CSP fix landed via direct pushes to `main` rather than a single feature-branch PR. End-state is equivalent.
>
> ---

**Generated:** 2026-05-22
**Current branch:** `fix/csp-nonce-rsc-hydration`
**Divergence:** 13 commits ahead, 0 commits behind `origin/main`
**Author of all 13:** Duško Ličanin (single-day batch)

## Summary

The current local branch carries 13 unpushed **commits** — but those committed commits are NOT the CSP fix. The branch name is accurate for *uncommitted* work: the Sev-1 CSP-nonce/RSC-hydration fix lives in the modified-but-uncommitted working tree (4 files — see `dirty-file-triage.md` Bucket S1, and `docs/findings/2026-05-22-csp-blocks-prod-hydration.md` which marks it RESOLVED).

So the situation is:

| Layer | Content | Matches branch name? |
|---|---|---|
| 13 committed commits | security tests, URL-helper refactor, orchestrator fix, pentest docs | **No** — these are the misfit |
| Uncommitted working tree | the Sev-1 CSP-nonce fix (+ ~130 other Phase 9-11 files) | **Yes** — the CSP fix is the namesake |

**Conclusion:** Don't rename the branch. *Extract the 13 commits* to their own branch; let `fix/csp-nonce-rsc-hydration` keep its name and carry only the CSP fix (committed from the working tree per Bucket S1).

**All 13 commits are good atomic units.** None look accidental or broken. The decision is *how to package them for review*, not whether to keep them.

---

## Commits, grouped by theme

### Theme 1: Security tests (5 commits, ~1000 LOC new tests + 1 bug fix)

| SHA | Subject | Files |
|---|---|---|
| `5a9f0c5` | test(coverage): real-behavior unit tests + fix clean_phone ++ bug | `src/processors/google_maps.py` + 5 new test files |
| `9024e06` | test(security): lock SMTP header-injection guards in email_sender | `tests/test_email_sender_guards.py` (NEW, 120 LOC) |
| `ac174af` | test(security): direct unit coverage for the SSRF guard core logic | `tests/test_ssrf_guard.py` (+132 LOC) |
| `a004970` | test(security): regression coverage for session-audit security helpers | `tests/test_security_helpers.py` (NEW, 151 LOC) |
| `814dd9b` | test(security): endpoint auth/validation/rate-limit matrix | `tests/test_endpoint_security_matrix.py` (NEW, 459 LOC) |

Note the `++` bug in `clean_phone` (`5a9f0c5`) — this is a real fix bundled with the test that catches it.

### Theme 2: Frontend URL helper refactor (3 commits, ~250 LOC)

| SHA | Subject | Files |
|---|---|---|
| `b6abfe8` | refactor(frontend): extract ensureProtocol to a tested utils/url.mjs | `page.tsx`, `package.json`, `utils/url.{mjs,d.mts,test.mjs}` |
| `3ebeeb7` | refactor(frontend): move sanitizeNext into tested utils/url.mjs | `login/actions.ts`, `utils/url.{mjs,d.mts,test.mjs}` |
| `926721a` | docs(claude.md): update sanitizeNext location + record url.mjs module | `CLAUDE.md` |

Already documented in CLAUDE.md under the auth security section — these commits formalised the helper-into-module split.

### Theme 3: Orchestrator fix (1 commit)

| SHA | Subject | Files |
|---|---|---|
| `ecc4859` | fix(orchestrator): non-UUID job_id → clean not_found, not 500 | `src/core/task_orchestrator.py` (+17), `PENTEST_CRAWLER.md` (+59) |

The orchestrator fix is mixed with the Round 2 pentest doc. The doc append is small (59 LOC of round-2 results); fix is 17 LOC.

### Theme 4: Pentest docs (4 commits)

| SHA | Subject | Files |
|---|---|---|
| `863142f` | docs(test): exhaustive AI crawler + feature scenario audit | `AI_SCENARIO_TEST.md` (NEW, 134 LOC) |
| `f048746` | docs(pentest): AI crawler ingestion penetration test — 0 vulns | `PENTEST_CRAWLER.md` (NEW, 147 LOC) |
| `dabede3` | docs(pentest): Round 3 — session/JWT/race-condition vectors, 0 vulns | `PENTEST_CRAWLER.md` (+48) |
| `c2c2f8f` | docs(pentest): Round 4 — direct Supabase attack, backend bypassed, 0 vulns | `PENTEST_CRAWLER.md` (+48) |

Pure documentation. Could land as one squashed commit; current 4-commit form preserves attack-round history.

---

## Conflict map vs open PRs

Files touched by the 13 ahead-commits ↔ files touched by open PRs:

| Open PR | Conflict source | Conflict file(s) |
|---|---|---|
| **#181** — asyncio.to_thread for task_orchestrator | `ecc4859` | `src/core/task_orchestrator.py` |
| **#200** — CLAUDE.md doc update | `926721a` | `CLAUDE.md` |
| #189 — phase1 types annotations | unknown | possible if it touches `google_maps.py` |
| #185 / #194 / #199 | low likelihood | new test files only |
| All other open PRs | none expected | new-file-only commits don't conflict |

**Verification command:**
```bash
gh pr view 189 --json files --jq '.files[].path'
gh pr view 181 --json files --jq '.files[].path'
gh pr view 200 --json files --jq '.files[].path'
```

---

## Three packaging options

### Option A — Single PR, single feature branch (RECOMMENDED)

**Snapshot the 13 commits to a new branch ref — do NOT `checkout -b` from the dirty tree** (that would drag 130+ uncommitted files along). `git branch <name> <sha>` creates a pure ref without touching the working tree:

```bash
# 814dd9b is current HEAD — the tip of the 13 commits
git branch feature/2026-05-22-security-and-url-refactor 814dd9b
# the new branch now holds exactly the 13 commits, clean working tree.
# switch to it ONLY after the dirty tree is drained (or stash first):
```

When ready to push (Stage 4):
```bash
git checkout feature/2026-05-22-security-and-url-refactor   # clean tree here
git rebase origin/main                                       # resolve CLAUDE.md + task_orchestrator.py
git push -u origin feature/2026-05-22-security-and-url-refactor
gh pr create --base main --title "Security tests + frontend URL refactor + pentest results (2026-05-22 batch)"
```

**Pros:**
- Preserves commit-by-commit history (good atomic units)
- Single review surface
- One CI run
- The 13 commits leave `fix/csp-nonce-rsc-hydration`, which then correctly carries only the CSP fix

**Cons:**
- Large PR (1800+ LOC, mostly new test/doc files)
- Reviewer needs to context-switch across themes

### Option B — Four themed PRs

Cherry-pick each theme onto its own branch. Pros: smaller reviews per PR. Cons: 4× CI cost, 4× rebase risk, splits cohesive work.

```bash
# Theme 1 example
git checkout -b feature/security-tests-batch origin/main
git cherry-pick 5a9f0c5 9024e06 ac174af a004970 814dd9b
git push -u origin feature/security-tests-batch
```

Repeat for themes 2, 3, 4. Risk: cherry-pick conflict if commits depend on each other (unlikely here — themes touch disjoint paths).

### Option C — Direct push to main

```bash
git checkout main && git push origin main
```

**Rejected.** Bypasses CI gate + PR review even for single-operator project. The CI checks (~20 from `docs/ci-architecture.md`) are the safety net — skipping them means a regression lands silently.

---

## Recommended sequence

1. **Snapshot the 13 commits NOW — zero-risk, do this before any other git surgery:**
   ```bash
   git branch feature/2026-05-22-security-and-url-refactor 814dd9b
   ```
   This creates a ref. The working tree is untouched, the 130+ dirty files stay where they are. The 13 commits are now safe even if `fix/csp-nonce-rsc-hydration` is later reset or drained.
2. **Hold the PR for Stage 4** (after PR queue #185–#200 lands per `docs/pr-merge-plan.md`).
3. After Stage 3 (#200) merges, drain the dirty working tree into per-bucket branches first (see `dirty-file-triage.md`) so `fix/csp-nonce-rsc-hydration` has a clean tree, OR `git stash` the dirty files.
4. With a clean tree, rebase the snapshot branch onto fresh `origin/main`:
   ```bash
   git checkout feature/2026-05-22-security-and-url-refactor
   git fetch origin && git rebase origin/main
   ```
5. Resolve conflicts in `CLAUDE.md` (vs #200's edits) and `src/core/task_orchestrator.py` (vs #181 if landed) — both likely small textual conflicts.
6. Push + open PR per Option A.
7. Re-run conflict map vs remaining drafts (#171–#184) before they're promoted.

> **Do NOT rename `fix/csp-nonce-rsc-hydration`.** The name is correct for the uncommitted Sev-1 CSP fix that bucket S1 will commit onto it. It is the 13 commits that are the misfit, and step 1 extracts them.

---

## Why hold instead of going first

- #185–#200 are already reviewed/staged; injecting a 1800-LOC PR into the middle disrupts the queue.
- The orchestrator fix in `ecc4859` is small (17 LOC) and could be cherry-picked into a fast-track PR if a real incident depends on it — otherwise wait.
- New-file commits (8 of 13) carry zero rebase risk; landing them last costs nothing.

---

## Decision required

| Question | Decision |
|---|---|
| Package as Option A / B / C? | (operator decides — A recommended) |
| Cherry-pick `ecc4859` orchestrator fix into fast-track PR? | (operator decides — only if a job_id 500 has surfaced) |
| Cherry-pick the `clean_phone` `++` fix portion of `5a9f0c5`? | (operator decides — fast-track only if the bug is biting users now; the fix is bundled with new tests, so the test-only remainder still needs the main batch PR) |
| Rename branch before push? | **No** — extract the 13 commits to `feature/2026-05-22-security-and-url-refactor` instead; `fix/csp-nonce-rsc-hydration` keeps its name for the Sev-1 CSP fix (Bucket S1) |
| Squash pentest commits into one? | optional — current 4-commit form is reviewable |
