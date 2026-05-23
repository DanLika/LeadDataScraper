# Complexity Report

Generated: 2026-05-22
Branch: `refactor/outreach-score-cc` (base `origin/main` @ `ee2fa0c`)
Tools:
- Python — `radon 6.0.1` (`radon cc src/ backend/ -a -s -nb`)
- TypeScript — `eslint 9.39.4` + `eslint-plugin-sonarjs 4.0.3` +
  `typescript-eslint 8.59.4` with `sonarjs/cognitive-complexity` rule
  set to `["error", 15]`

## TL;DR

| Language | Functions over CC 15 | Worst | Action this PR |
|---|---|---|---|
| Python | 8 | `LeadHunter.calculate_outreach_score` — CC 37 (E rank) | **Refactored → CC 2** (orchestrator) + 6 helpers all ≤ CC 9 |
| TypeScript | 5 | `app/page.tsx` `DashboardInner` — Cognitive CC 54 | Documented; not in this PR |

`pip-autoremove` was already replaced with `deptry` for the dead-code
pass; reproducing-steps assume `make install-hooks` or hand-run.

---

## Top 10 worst — Python (radon CC > 15)

Ranked by Cyclomatic Complexity. Threshold per request: **> 15** (radon
rank C is 11-20, D is 21-30, E is 31-40, F is 41+).

| # | CC | Rank | Location | Symbol | Why | This PR |
|---|---|---|---|---|---|---|
| 1 | **37** | **E** | `src/processors/leadhunter.py:416` | `LeadHunter.calculate_outreach_score` | 70 lines, 4 scoring concerns inlined, multiple `or` chains across `s_data` + `lead`, nested try/except, two JSON-string-or-dict normalisations, `audit and audit.get(...)` short-circuit | ✅ refactored |
| 2 | 26 | D | `src/processors/leadhunter.py:215` | `LeadHunter._extract_socials` | 9 different scrape patterns, regex fallback per platform, network-error catch per branch | deferred |
| 3 | 26 | D | `src/core/parallel_auditor.py:286` | `ParallelAuditor.orchestrate_scaling` | concurrency knobs decision tree (CPU, mem, queue depth, error rate) | deferred |
| 4 | 25 | D | `src/scrapers/discovery_engine.py:95` | `DiscoveryEngine._extract_lead_data` | per-field DOM extraction with multiple selector fallbacks | deferred |
| 5 | 24 | D | `src/processors/leadhunter.py:82` | `LeadHunter.trazi_social_linkove_async` | async social-link search with retry / fallback / SSRF check | deferred |
| 6 | 23 | D | `src/core/parallel_auditor.py:173` | `ParallelAuditor.audit_single_lead` | per-lead audit dispatch + status writes + error path | deferred |
| 7 | 22 | D | `src/scripts/suggest_jsonb_indexes.py:70` | `main` | advisory CLI script — defer / keep flat | won't fix |
| 8 | 21 | D | `src/scrapers/seo_audit.py:141` | `_detect_portals_and_socials` | 12 portal-detection regexes + per-hit social classification | deferred |
| 9 | 18 | C | `src/utils/csv_helper.py:68` | `load_csv_with_unique_key` | encoding detection cascade + key fallback chain | deferred |
| 10 | 18 | C | `src/scripts/storage_report.py:65` | `main` | advisory CLI script | won't fix |

Aggregate: **109 blocks** analysed. Pre-refactor distribution
(`pytest src/ backend/` scope on `main`):
1 × E, 7 × D, 32 × C, 69 × B, 0 × A. **Avg complexity: C (10.5)**.

Post-refactor (worktree at `origin/main` + this PR's changes;
fewer files because `origin/main` doesn't yet have all 13 unreleased
script additions): **207 blocks**, avg **B (5.6)**. The per-file impact
is local to `leadhunter.py`; the worktree's smaller block count makes
the average artificially lower, so the meaningful comparison is the
per-function table above.

---

## Top 5 worst — TypeScript (sonarjs Cognitive Complexity > 15)

Sonarjs *Cognitive* Complexity counts nesting depth penalties on top of
control-flow branches, so the numbers run higher than radon's CC for
similarly-structured code. Threshold: **15** (sonarjs default).

| # | CC | Location | Symbol | Why | This PR |
|---|---|---|---|---|---|
| 1 | **54** | `frontend/app/page.tsx:114` | `DashboardInner` (default export) | one component owns: filter state machine (segment / status / score / search / sort) + URL sync + cross-page bridge consume-then-strip + cursor pagination + modal state (settings / discovery) + lead-mutation handlers + offline queue wiring | deferred |
| 2 | 28 | `frontend/app/components/Sidebar.tsx:43` | `Sidebar` | render mixes nav structure + mobile drawer transform + setter-shim for cross-page modals + insights widget | deferred |
| 3 | 23 | `frontend/app/page.tsx:394` | nested inline handler in `DashboardInner` | lead-action click handler with 6 branches (audit / enrich / outreach / linkedin / delete / cancel) | deferred (subsumed by #1) |
| 4 | 22 | `frontend/app/components/AIChat.tsx:25` | `AIChat` `handleSubmit` | task-routing dispatch + 422-detail-list flatten + execute-plan diff + scroll-to-bottom on response | deferred |
| 5 | 21 | `frontend/app/api/proxy/[...path]/route.ts:75` | `forward()` | per-method branch + Origin gate + auth-revalidate + path-special-cases (`leads/clear`, `metrics`) + scheme assertion | deferred |
| 6 | 21 | `frontend/utils/loginThrottle.ts:36` | `consumeAttempt` | bucket sweep + eviction on `MAX_BUCKETS` + clock-skew tolerance | deferred |

`DashboardInner` is the highest-impact target but also the highest
regression risk (no unit tests around the page component itself —
verification depends on the 18-file Playwright suite). Deliberately
deferred to its own dedicated PR with a test-first approach.

---

## What this PR ships

`src/processors/leadhunter.py` — `calculate_outreach_score` decomposed
into 6 helpers + a thin orchestrator. Behaviour preserved verbatim;
the 9 existing tests in `tests/test_outreach_score_properties.py`
(fixed fixtures + 600 hypothesis examples) and the
`test_outreach_score_calculation` case in
`tests/test_cherry_picks_live.py` all pass unchanged.

| Helper | CC | Rank | Concern |
|---|---|---|---|
| `_score_contacts(lead, s_data)` | 7 | B | +20 email \| +10 phone \| +15 any social |
| `_score_reputation(lead)` | 9 | B | +15 rating<4.0 \| +10 reviews<20 |
| `_resolve_enrichment_data(lead)` | 7 | B | dict / JSON-string / top-level-fallback normaliser |
| `_score_enrichment(e_data)` | 5 | A | +10 leadership_team \| +10 company_size |
| `_resolve_audit_data(lead)` | 4 | A | dict / JSON-string normaliser |
| `_score_urgency(lead, audit)` | 8 | B | +20 high_risk \| pain_points |
| **`calculate_outreach_score` (orchestrator)** | **2** | **A** | sum of four scorers, then `min(score, 100)` |

The orchestrator dropped from **CC 37 → CC 2** (-95%). Every helper is
under the 15-threshold; each is independently testable; behaviour-pinning
tests still cover the same surface.

### Preserved invariants

Pulled from the property test suite and inlined here so a future
reviewer doesn't need to cross-reference:

1. `email` OR `EXTRACTED_EMAIL` truthy → +20
2. `phone` truthy → +10
3. ANY of `{s_data.fb/ig/li, lead.fb/ig/li}` truthy → +15
4. `rating < 4.0` (comma → dot parse, `ValueError`/`TypeError` swallowed) → +15
5. `reviews < 20` (`re.sub(r'\D','')` digit-extract, default 0 on empty) → +10
6. `leadership_team not in {'Unknown', '', None}` → +10
7. `company_size not in {'Unknown', '', None}` → +10
8. `high_risk_flag` OR `len(pain_points) > 0` → +20
9. final `min(score, 100)`
10. `enrichment_data` fall-through: when `enrichment_data` is falsy AND
   the key `'company_size'` or `'leadership_team'` exists in lead, use
   lead itself. **Key existence**, not value-truthiness — preserved.
11. `enrichment_data` and `audit_results` both parsed via `json.loads`
   when string-typed; bare `except` collapses any parse failure to `{}`.
12. `is_high_risk = lead.get('high_risk_flag') or (audit and audit.get('high_risk_flag'))`
   — the `audit and` short-circuit is now redundant because the
   `_resolve_audit_data` helper always returns a dict, but kept in
   `_score_urgency` as defense-in-depth.

### What deliberately did NOT change

- `seo_score` is **still not** an input. The
  `test_seo_score_does_not_affect_score` fixed-fixture test and the
  `test_seo_score_is_invariant_under_fuzz` hypothesis test (100
  examples) both pass on the refactor.
- Stale comment `# 1. Data Completeness (+60 max)` was wrong (actual max
  from email + phone + social + rating + reviews is 70). Replaced
  per-helper docstrings; old comment dropped.
- No new env vars, no new dependencies, no test additions or deletions.

---

## False positives / out of scope

### sonarjs `react-hooks/exhaustive-deps` error

```
frontend/app/page.tsx:148:5  error  Definition for rule 'react-hooks/exhaustive-deps' was not found
```

The measurement-only flat config doesn't load `eslint-plugin-react-hooks`
(it's pulled by `eslint-config-next` in the project config). False
positive of the standalone measurement run; not a real ESLint failure
under `next lint`.

### radon C-rank entries (CC 11-15)

Not over the > 15 threshold. 32 functions sit in C rank, mostly small
multi-branch helpers (`_get_reputation_segment` CC 12,
`extract_json_from_response` CC 11, etc.). Borderline; not worth
breaking without a behaviour change pulling them. Re-flag if
threshold tightens to 10.

### Advisory scripts

`src/scripts/suggest_jsonb_indexes.py::main` (CC 22),
`src/scripts/storage_report.py::main` (CC 18),
`src/scripts/check_*::main` (multiple CC 12-15) are CLI report scripts
with a long flat sequence of probes. Splitting them by probe makes them
harder to read end-to-end. Tolerated.

---

## Roadmap (deferred to follow-up PRs)

| Order | Target | Strategy |
|---|---|---|
| 1 | `LeadHunter._extract_socials` (CC 26) | Per-platform extractors (`_extract_facebook`, `_extract_instagram`, `_extract_linkedin`). Each one a CC ≤ 8 method. |
| 2 | `ParallelAuditor.orchestrate_scaling` (CC 26) | Extract a `ScalingPolicy` value object; each decision (CPU / mem / queue / error) becomes a single-method check returning a target concurrency. |
| 3 | `DiscoveryEngine._extract_lead_data` (CC 25) | Per-field extractors with the existing `_extract_address` pattern as the model. |
| 4 | `LeadHunter.trazi_social_linkove_async` (CC 24) | Split the SSRF gate, the fetch, and the parse into separate awaits. |
| 5 | `ParallelAuditor.audit_single_lead` (CC 23) | Already partially decomposed; finish by extracting the status-write paths. |
| 6 | `_detect_portals_and_socials` (CC 21) | Table-driven: regex + classifier as data, single dispatch loop. |
| 7 | `DashboardInner` TS CC 54 | Test-first: get the existing E2E specs running, extract the filter state-machine into a custom hook (`useLeadFilters`), then peel modal state into a reducer. Highest risk; biggest payoff. |
| 8 | `Sidebar` TS CC 28 | Split rendering from setter-shim wiring; the cross-page-bridge `(open) ⇒` callbacks belong in the parent page or a hook. |

---

## Reproducing this report

### Python

```sh
.venv/bin/pip install radon
.venv/bin/radon cc src backend -a -s -nb
```

### TypeScript

Standalone, no project config changes. Install both plugins
non-persistently (`--no-save` may still mutate `package-lock.json`;
do not commit lockfile or `package.json` changes from this measurement):

```sh
cd frontend && npm install --no-save --no-audit --no-fund \
  eslint-plugin-sonarjs typescript-eslint
```

Drop this flat config at `frontend/.cx_sonar.config.mjs` (gitignored
via `.gitignore` entry, or hand-deleted after measurement):

```js
import sonarjs from "eslint-plugin-sonarjs";
import tseslint from "typescript-eslint";
export default [{
  files: ["**/*.{ts,tsx,mts,mjs,js,jsx}"],
  ignores: ["**/node_modules/**","**/.next/**","**/dist/**","**/build/**","**/out/**","next-env.d.ts"],
  languageOptions: { parser: tseslint.parser, parserOptions: { ecmaVersion: "latest", sourceType: "module", ecmaFeatures: { jsx: true } } },
  plugins: { sonarjs },
  rules: { "sonarjs/cognitive-complexity": ["error", 15] },
}];
```

Then:

```sh
cd frontend && npx --no-install eslint --no-config-lookup \
  --config .cx_sonar.config.mjs --no-warn-ignored \
  "app/**/*.{ts,tsx}" "utils/**/*.{ts,tsx,mjs}" "components/**/*.{ts,tsx}"
```

### Adoption (optional, not in this PR)

If the project wants CC < 15 to be a CI gate (matching the
local-CI parity philosophy already in `.pre-commit-config.yaml`):

1. Add `sonarjs/cognitive-complexity: ["error", 15]` to
   `frontend/eslint.config.mjs` (already present `eslint-plugin-sonarjs`
   would need to be a real `devDependency`, not `--no-save`).
2. Add `radon cc src backend --total-average --no-assert -nc` to
   `.pre-commit-config.yaml` with a threshold flag (`-nc` = no rank ≥ C);
   start as advisory (don't fail) until the deferred list is worked
   through, then flip to required.
