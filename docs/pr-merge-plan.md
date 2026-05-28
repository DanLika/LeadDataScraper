# PR Merge Plan — Phase 12.1

> ## Status — 2026-05-23 (afternoon update)
>
> **Superseded by full pre-merge review.** See
> [`tests/quality/pr-review-pass-2026-05-23.md`](../tests/quality/pr-review-pass-2026-05-23.md)
> for per-PR verdicts and
> [`docs/pr-merge-order-2026-05-23.md`](pr-merge-order-2026-05-23.md)
> for the phased sequence.
>
> Open PR count today: **49** (up from the 29 originally tracked
> here — drain PRs #201–#260 added a large session-2026-05-23
> backlog). The original #171–#200 work plan below is largely
> stale; treat as historical context.
>
> **Two P0 blockers to clear before any merge:**
>
> 1. **CI fails on every session + Dependabot PR** (`gh pr checks`
>    returns `fail,skipping` across the board). Systemic, not per-PR.
>    Diagnose via `gh run list --workflow=ci.yml --limit 5` against
>    `main`.
> 2. **PR #248 vs PR #255** disagree on 4 verification rows of
>    `docs/bookbed-crossover.md` (2.5 Flutter log CRLF, 2.5 CF logger
>    CRLF, 2.6 CF Origin gate, 2.12 CF rate-limit, 2.13 Firestore
>    type checks). Both held until a third re-verification in
>    `~/git/bookbed` settles each disputed row.
>
> **Net ready-when-CI-green: ~24 PRs.** Phased order in the
> companion `pr-merge-order-2026-05-23.md`.
>
> **Close list** (no merge — verified in the review pass):
> #233 (superseded by #251), #134, #137, #139, #140 (targets no
> longer exist), one of #239/#240 (identical Inter-drop;
> PR #238 already bundles the same change).
>
> **Hold-on-author-rebase**: #135, #136, #138 (tests/ → tests/unit/
> relocation needed post-Phase-14 reorg; for #138 also a code
> rebase since `_generate_campaign_strategy` moved L595→L688).
>
> ---
>
> ## Historical status — 2026-05-23 morning

**Generated:** 2026-05-22
**Scope:** All open PRs (#171–#200) on `main` base
**Source:** `gh pr list --state open --json number,title,baseRefName,headRefName,isDraft,mergeable`

## Snapshot

| Bucket | Count | Notes |
|---|---|---|
| Non-draft, ready | 16 | #185–#200 — manual session work |
| Draft, auto-generated | 14 | #171–#184 — needs review before promotion |
| Stacked (depend on another open PR) | 4 | #189→#191, #192→#195 |
| `MERGEABLE` flag clean | 30 / 30 | No textual conflicts at audit time |

> **Note:** `mergeable: MERGEABLE` only tests against the PR base SHA at audit time, not against `origin/main` after intermediate merges. Re-check after each Stage 1 landing.

> **Hard prereq:** Phase 12.13 must be resolved before any of this runs. Local `main` is 13 commits ahead of `origin/main`. Pushing/rebasing while that divergence exists will produce conflicts on every PR.

---

## Stage 1 — Parallel, no inter-deps

Land in any order. Each rebases cleanly onto `main`.

| PR | Title | Type |
|---|---|---|
| #185 | chore(dead-code): drop unused ExportButtons + browser supabase client | cleanup |
| #186 | refactor(leadhunter): decompose calculate_outreach_score (CC 37 → 2) | refactor |
| #187 | docs(type-coverage): add weekly mypy --strict tracker | docs |
| #188 | docs(tech-debt): add tech debt register | docs |
| #190 | docs(duplication): add duplication report | docs |
| #193 | docs(component-audit): frontend component-size audit | docs |
| #194 | refactor(constants): centralize numeric policies | refactor |
| #196 | ci(quality-ratchet): 5-metric baseline + CI workflow | ci |
| #197 | docs(architecture): module graph + cycle audit | docs |
| #198 | docs(coverage): backfill prompt_safety + 9 Pydantic class docstrings | docs |
| #199 | chore(tests): reorganize 13 files into 5 subdirs + pytest.ini markers | refactor |

**Recommended sub-order within Stage 1:**
1. Pure docs first (#187 #188 #190 #193 #197 #198) — zero code risk, build confidence
2. CI/cleanup next (#185 #196) — small, locks in baselines
3. Refactors last (#186 #194 #199) — touch real code, run full test suite per merge

**Conflict watch:**
- #194 (constants centralisation) may touch files later edited by Stage 2 refactors. Land #194 BEFORE #189/#192 to avoid double-rebases.
- #199 (test reorganize) renames every test file location. Land LAST in Stage 1 or every other PR's test-path references go stale.

---

## Stage 2 — Stacked roots

Two two-PR stacks. Bottom PR must land first.

### Stack A: types/phase1 → csv-loader

```
#191 (refactor/csv-loader-decompose)
  └── base: types/phase1-quick-wins
      #189 (types/phase1-quick-wins)
        └── base: main
```

**Order:**
1. Merge #189 to `main`.
2. Rebase #191 onto `main`: `gh pr edit 191 --base main && git fetch origin && git checkout refactor/csv-loader-decompose && git rebase origin/main && git push --force-with-lease`
3. Merge #191.

### Stack B: campaigns-layered → errors-standardize

```
#195 (refactor/errors-standardize)
  └── base: refactor/campaigns-layered
      #192 (refactor/campaigns-layered)
        └── base: main
```

**Order:**
1. Merge #192 to `main`.
2. Rebase #195: `gh pr edit 195 --base main && git fetch origin && git checkout refactor/errors-standardize && git rebase origin/main && git push --force-with-lease`
3. Merge #195.

**Note on `--force-with-lease`:** Safer than `--force`. Aborts if upstream moved (someone else pushed). Always preferred over `--force` for shared branches.

---

## Stage 3 — Documentation last

| PR | Title |
|---|---|
| #200 | docs(claude.md): document session 2026-05-22 patterns (PR #185-#199) |

Per #200's own body: "Should land LAST… If a different order ships, the CLAUDE.md update may forward-reference code that isn't there yet."

Recovery if ordering breaks: amend or revert #200. Cheaper than re-sequencing everything else.

---

## Drafts (#171–#184) — separate decision

14 auto-generated draft PRs from a code-quality scanner. **Do not land as a batch.** Each needs human review:

| PR | Title | Risk |
|---|---|---|
| #171 | 🧹 Remove unused delete_all_jobs function | Low — if grep confirms zero callers |
| #172 | ⚡ Parallelize campaign stats queries | Medium — concurrency correctness |
| #173 | ⚡ Fix blocking sync Supabase calls in get_campaign | Medium — see CLAUDE.md async wrapper policy |
| #174 | 🧪 Add unit tests for export_leads.is_high_priority | Low |
| #175 | 🧪 Add edge case tests for check_vulnerability | Low |
| #176 | 🔒 Secure exception logging in supabase_helper | Medium — verify it doesn't drop diagnostic info |
| #177 | 🧪 Add missing tests for calculate_seo_score | Low |
| #178 | 🧹 Remove unused `update_audit` function | Low — verify zero callers |
| #179 | 🧪 Add unit tests for assert_safe_scheme in ssrf_guard | Low |
| #180 | 🔒 Fix potential CORS misconfiguration by removing default origin | **HIGH** — confirm against CLAUDE.md ALLOWED_ORIGINS policy |
| #181 | ⚡ Use asyncio.to_thread for blocking task orchestrator queries | Medium — overlap with #173 |
| #182 | 🧪 Add unit tests for LeadHunter extract_personal_name | Low |
| #183 | ⚡ Offload get_stats synchronous Supabase query to thread | Medium — overlap with stats_cache (already merged via untracked work) |
| #184 | ⚡ Optimize CMS detection regex string matching | Low — but verify regex still matches all cases |

**Recommended:** Land Stage 1+2+3 first, then triage drafts one-by-one against the post-merge baseline.

---

## Per-merge checklist

For every PR landed:

1. **CI green?** All required checks (see `docs/ci-architecture.md` for the ~20-check list) must pass. `gh pr checks <N>` to verify.
2. **Rebase if base moved.** `gh pr view <N> --json mergeStateStatus` should be `CLEAN`.
3. **Squash vs merge commit.** Match repo policy (verify in repo settings; if unset, prefer squash for cleanliness).
4. **Watch for follow-up:** Re-run `gh pr list` after each merge to spot newly-conflicting PRs.
5. **Local main pull:** `git checkout main && git pull origin main` after each merge.

---

## Open questions

- **CI status not audited at doc-write time.** `gh pr checks <N>` was NOT run for all 30 PRs (would be 30 sequential calls). Verify per-PR before clicking merge.
- **Required-check policy unknown.** Repo's branch-protection rules not inspected. If `ci.yml::pre-commit (local-CI parity)` is required and a PR predates pre-commit setup, may need fresh push to trigger.
- **Author identity.** Drafts may be from a bot account — confirm whether bot PRs need a human to push the merge button per org policy.

---

## Outstanding risk

The 13 ahead-commits on local `main` (Phase 12.13) WILL conflict with at least one PR if pushed naively. Resolve `git-state-2026-05.md` decisions FIRST.

---

## 2026-05-23 — docs-PR stack (CLAUDE.md drain documentation)

Three docs PRs all append to the same CLAUDE.md insertion point and were rebased into a deterministic stack:

```
main ← #253 ← #254 ← #258
```

| PR | Branch | Content | Base |
| --- | --- | --- | --- |
| **#253** | `docs/claude-md-drain-2026-05-23-opus47-v2` | Drain PRs #235–#251 (#238 backend headers, #242 web-vitals, #244 stats card, #245 insights, #246 slow-handler, #250 trigger fn) + A.8 dupe + A.9 already-on-main notes | `main` |
| **#254** | `docs/claude-md-phase15-session-2026-05-23` | Phase 15 finding matrix with refreshed rows (#3→#244, #5→#242, #6→#245) | rebased onto #253 |
| **#258** | `docs/claude-md-crossover-gaps-2026-05-23` | #237 cross-origin headers + #231 gitignore + #227 P0a retraction + docs-stack-rebase recipe | rebased onto #254 |

**Merge order: #253 → #254 → #258.** Each PR was rebased onto the previous PR's tip with `--force-with-lease=<branch>:<expected-tip>` so the line-1844 append conflict is pre-resolved. When the bottom merges to `main`, GitHub auto-rebases the next; the diff collapses to that PR's own additions.

**Do not merge out of order.** If #254 lands before #253, GitHub will rebase #254 onto main correctly, but #258 then has #254's content + an orphan reference to #253 (which has not landed). #253 will then merge cleanly as a separate diff; #258 still needs a final rebase. Lots of churn. Stick to the documented order.

Full recipe + invariants pinned in `CLAUDE.md` under "Docs-PR stack via sequential rebase".
