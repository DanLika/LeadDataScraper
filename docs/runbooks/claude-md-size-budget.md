# `CLAUDE.md` size budget (≤35 k soft / ≤40 k hard)

**Status**: RECURRING. PR #396 slimmed 46091 → 33508 chars on 2026-05-29.
Parallel-session growth re-breaches budget within hours/days. Slim recipe
codified; timing rules pinned to avoid PR #307 outcome (closed because main
grew 30k chars / 21 files in 8 h after extraction).

## Symptom

CI gate `.github/workflows/claude-md-size.yml` fails:

```
::warning::CLAUDE.md is 36204 chars (soft threshold: 36000)
# or
::error::CLAUDE.md is 41892 chars (hard threshold: 40000)
```

OR a fresh session boot consumes inordinate token budget on CLAUDE.md alone,
displacing actual conversation context.

## Root cause

CLAUDE.md accumulates load-bearing context faster than it sheds it. Parallel
sessions each add their own session-log pointer, runbook reference, or
invariant block. Without periodic slim passes, file grows by ~3–8 k per
busy week.

Per-session adds are correct in isolation — they pin facts a fresh boot
needs. Aggregate growth is the failure mode.

## Slim recipe (verified PR #396, 2026-05-29)

**Pattern**: extract reference-y blocks verbatim to
`docs/architecture/<topic>.md` or `docs/runbooks/<topic>.md`. Replace each
in CLAUDE.md with **one-line pointer + most-load-bearing distilled bullets**
— not a compressed paragraph, but pinned rules a fresh session boot needs
in 30 s.

**KEEP in CLAUDE.md**:

- Hard invariants — PEP 562 trap / RLS table count / X-API-Key + X-Admin-Token
  gates / `sanitize_dataframe_for_csv` / `<UNTRUSTED_DATA>` fence /
  `safe_constr` / cursor charset gate.
- 6-finding pinned list (load-bearing for AI not to make wrong claims).
- Op gotchas — parallel session HEAD race, `pkill` LAST-flag,
  chrome-devtools click no-op, Render no-server, `gh run view --log-failed`.
- context-mode hard injunctions.
- Recent session log pointers (last ~10 entries).

**EXTRACT to `docs/`**:

- Per-module dossiers (file maps, conventions, breakpoints).
- Sentry / Discord narrative.
- AI router internals.
- Discovery engine internals.
- Frontend file map + conventions.
- Full context-mode tool hierarchy.
- Full session-log hook list.
- Perf + observability detail.

## Pre-commit gate (mandatory)

Run BOTH a volume gate AND a content-faithfulness gate before pushing:

```bash
# 1. Volume — pure size check
WC=$(wc -c < CLAUDE.md)
test "$WC" -le 35000 || { echo "CLAUDE.md = $WC > 35000 budget"; exit 1; }

# 2. Content faithfulness — discriminating section diff
diff <(git show origin/main:CLAUDE.md | grep -E '^## ') \
     <(cat CLAUDE.md docs/architecture/*.md docs/runbooks/*.md | grep -E '^## ')
# Lines prefixed `<` = H2s on main with no match anywhere in (slim +
# extracted). Those are DROPPED CONTENT. Either re-extract verbatim OR
# confirm the linked doc already contains the prose.
```

`wc -c CLAUDE.md + sum(extracted) ≥ baseline` is a VOLUME gate, NOT a
content-faithfulness gate. Section-diff is the right one. PR #396 review
caught a dropped `## End-to-end smoke flow` recipe via section-diff that
the volume gate missed — was a re-runnable verification, not narrative.

## Timing rules

**Don't slim while `pgrep claude` ≥ 2**.

Per memory `feedback_claude_md_refactor_timing.md`: PR #307 closed because
main grew 30k chars / 21 unrelated files in 8 h after extraction. Parallel
sessions wrote new sections to CLAUDE.md that the slim PR didn't anticipate.
Merge conflict resolution loses load-bearing facts.

Per `claude_md_refactor_defer_2026-05-25.md`: at 164k chars on main with
6 parallel sessions active, slim was deferred. Wait until pgrep count drops
to 1.

**When session count IS safe (1 session)**:

```bash
# Branch off origin/main in a fresh worktree to avoid local in-flight conflicts
git worktree add ../lds-claude-slim -b chore/claude-md-slim origin/main
cd ../lds-claude-slim

# Do the extract + slim
# ... edits ...

# Rebase before merge
git fetch origin main
git rebase origin/main

# Resolve conflicts on H2 sections as CONTENT-conflicts (fold parallel
# additions into slim version + re-extract if reference-y). NEVER
# admin-merge through CLAUDE.md conflicts.
```

## Why not just a CI gate that auto-fails large CLAUDE.md?

There already is one (`.github/workflows/claude-md-size.yml` — 36k soft warn,
40k hard fail). It catches the symptom but not the cause. Without the slim
recipe + timing rules, every breach forces a manual recovery sprint.

## Recurrence guard

- **Monthly slim review** — when `wc -c CLAUDE.md > 38000`, schedule a slim
  pass within 7 days. Track in operator log.
- **Pre-add discipline** — when adding to CLAUDE.md, first check whether
  the content belongs in `docs/runbooks/` or `docs/architecture/` and only
  a one-line pointer belongs in CLAUDE.md.
- **PR template hint** (not yet wired) — frontmatter checkbox: "Does this
  add lines to CLAUDE.md? If yes, did I confirm `wc -c < CLAUDE.md` stays
  under 35000?"

## Related

- Memory: `claude_md_slim_pattern_2026-05-29.md`,
  `feedback_claude_md_refactor_timing.md`,
  `claude_md_refactor_defer_2026-05-25.md`
- PR: #396 (verified slim, 46091 → 33508), #307 (closed — parallel-session
  race demonstrated)
- CI: `.github/workflows/claude-md-size.yml`
