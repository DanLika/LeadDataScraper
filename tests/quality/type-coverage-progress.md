# Type Coverage Progress

Tracker. Update **weekly** (Monday). Each entry is one row in the
delta table: new total error count, target-dir count, files moved
to clean, notes.

Tools: `mypy 2.1.0 --strict src/`, `tsc --strict` (already on in
`frontend/tsconfig.json`).

---

## Headline (2026-05-22 — baseline)

| Metric | Value |
|---|---|
| `mypy --strict src/` exit code | **1 (FAIL)** |
| Total errors | **593** (mypy summary) |
| Files with errors | **35 / 42** (mypy reports `35 in 42 source files`) |
| Files already strict-clean | **8** |
| **CI gate status** | **NOT YET ENFORCED** — `.github/workflows/ci.yml` is untracked locally and absent from `origin/main` (only `security.yml` is deployed). The pre-commit comment "Selective scope until `mypy --strict src/` is green" remains aspirational. **Committing `ci.yml` today breaks the build on the first push** until target errors drop to 0. |
| Frontend `tsc --strict` | already on (`frontend/tsconfig.json`) |
| Frontend `: any` usages | 0 in `frontend/app/` + `frontend/utils/` |
| Frontend `as any` casts | 0 |

### Why 593 errors include `backend/main.py`

`mypy --strict src/` follows imports. `src/utils/query_profiler.py`
imports `backend` (the profiler can reflect on FastAPI handlers), so
mypy walks into `backend/main.py` and surfaces its **150 errors**
alongside the actual src/ total. This is a real CI surface — when
the eventual `python-lint` job runs `mypy --strict src/`, all 593
will fire.

---

## Error distribution

### By directory (target dirs in **bold**, sums to 593)

| Dir | Errors | LOC | err/100 LOC | In CI scope? |
|---|---:|---:|---:|---|
| `src/core/` | 228 | 1622 | 14.1 | yes |
| `backend/main.py` (transitively) | 150 | (1+ KLOC) | — | yes |
| **`src/utils/`** | **72** | 1278 | 5.6 | yes |
| **`src/scrapers/`** | **53** | 889 | 6.0 | yes |
| **`src/processors/`** | **48** | 998 | 4.8 | yes |
| `src/scripts/` | 34 | 2955 | 1.2 | yes |
| `src/integrations/` | 8 | 115 | 7.0 | yes |
| **TOTAL** | **593** | — | — | |

Target-dir sum (`src/utils/` + `src/scrapers/` + `src/processors/`):
**173 errors** over 3165 LOC.

The user-stated **95% type coverage target** translates to ≤ ~150
errors total in the target dirs (no clean way to compute "coverage %"
from mypy — counts are the operational metric). Phases 3-4 below land
under that ceiling.

### By error code (sums to 593)

| Code | Count | Meaning | Fix template |
|---|---:|---|---|
| `no-untyped-def` | 145 | Function missing annotations | Add `-> T:` + arg types |
| `union-attr` | 125 | `.attr` on `T \| None` / `Any \| T` unions | Narrow with `if x is not None:` or typed return shape |
| `name-defined` | 65 | Name not in scope (often from `if TYPE_CHECKING:` blocks or guarded imports) | Move import to runtime or add to `TYPE_CHECKING` import set |
| `no-untyped-call` | 61 | Calling untyped function from typed code | Fix the callee; `cast` at site as escape hatch |
| `type-arg` | 52 | `list`/`dict`/`Optional` without type args | `list[str]`, `dict[str, Any]`, `Optional[int]` |
| `arg-type` | 43 | Wrong arg type at call site | Real bug or upstream signature change |
| `assignment` | 21 | Re-assignment changes inferred type | Use a typed local or annotate the binding |
| `valid-type` | 18 | Invalid type expression in annotation | Often `dict[str, ]` (missing arg) or forward-ref typo |
| `import-not-found` | 14 | Module missing (psycopg) | CI-only dep; install in mypy CI step |
| `index` | 12 | Indexing into untyped container | Type-narrow before subscript |
| `attr-defined` | 10 | Calling method that doesn't exist on the static type | Real bug or stub gap |
| `import-untyped` | 8 | Module has no stubs (pandas) | Install `pandas-stubs` |
| `no-any-return` | 5 | Annotated `-> T`, actually returns `Any` | Add `cast` or fix upstream type |
| `return-value` | 3 | Wrong return shape | Real bug |
| `call-overload` | 3 | No overload signature matches | Add a cast or new overload |
| `unused-ignore` | 2 | `# type: ignore` no longer needed | Remove the comment |
| `operator` | 2 | Operator on untyped operands | Annotate |
| `var-annotated` | 1 | Variable needs annotation | Annotate |
| `return` | 1 | Missing return | Add return statement |
| **TOTAL** | **593** | | |

(There are also 3 lines mypy emits without a `[bracket-code]` suffix —
counted at the file level via `grep ' error: '`. Excluded from this
code-table.)

---

## Top 15 files by error count

| # | Errors | File | Strategy |
|---|---:|---|---|
| 1 | **150** | `backend/main.py` | Largest file in the repo; touched by every PR. Will need a dedicated typed-handler pass with Pydantic-aware return types. Pre-commit selective scope should NOT expand to this file until Phase 6. |
| 2 | 149 | `src/core/agentic_router.py` | 124 of these are `union-attr` cascades from Gemini JSON returns typed as `dict[str, Any]`. **Single biggest leverage point**: introduce `src/utils/gemini_types.py` with TypedDicts per response shape, annotate the 8 Gemini call-sites' return types. Expected delta: -100 errors in this file alone. |
| 3 | 52 | `src/core/task_orchestrator.py` | Background-job state dicts + filter polymorphism. Needs an `OrchestrationFilter = Union[...]` discriminated union (drift gate `check_jsonb_shapes.py` already enforces the two shapes). |
| 4 | 37 | `src/processors/leadhunter.py` | The 6 helpers from refactor PR #186 (`_score_contacts`, `_score_reputation`, etc.) need annotations — knocks ~6 errors per pass. Easy. |
| 5 | 33 | `src/scrapers/seo_audit.py` | bs4 `Tag \| NavigableString \| None`; 5 helpers evolve a result dict shape. Frozen `AuditResult` TypedDict. |
| 6 | 33 | `src/utils/supabase_helper.py` | Untyped PostgREST returns. Auto-generate via `mcp__supabase__generate_typescript_types`, port to Python TypedDicts in `src/utils/supabase_types.py`. |
| 7 | 27 | `src/core/parallel_auditor.py` | Untyped queue items. |
| 8 | 16 | `src/utils/csv_helper.py` | Already in `pre-commit` selective scope; `--strict` finds 16 more. |
| 9 | 13 | `src/scripts/export_leads.py` | Pandas-shaped. Install `pandas-stubs` clears most. |
| 10 | 11 | `src/scrapers/enrichment_engine.py` | Playwright `Page` / `Browser` annotations via `cast` at the seam. |
| 11 | 9 | `src/scrapers/discovery_engine.py` | Same Playwright story. |
| 12 | 8 | `src/integrations/email_sender.py` | Small file (115 LOC); 8 errors fully clearable in one pass. **Phase 1 target.** |
| 13 | 7 | `src/utils/ssrf_guard.py` | Already in `pre-commit` "fully typed" allowlist but `--strict` finds 7 more. **Phase 1 target** — clears the seam between selective and strict. |
| 14 | 7 | `src/utils/query_profiler.py` | Monkey-patch returns `Any`; `__enter__`/`__exit__` annotations missing. |
| 15 | 7 | `src/processors/ai_mapper.py` | Gemini JSON parse → untyped dict. Same TypedDict pattern as #2. |

### Already strict-clean (8 files)

```
src/__init__.py
src/core/__init__.py
src/integrations/__init__.py
src/processors/__init__.py
src/scrapers/__init__.py
src/scripts/__init__.py
src/utils/__init__.py
src/utils/stats_cache.py
```

`stats_cache.py` is the existence proof — a non-trivial async TTL
cache with explicit annotations + `asyncio.Lock`. Use it as the
template for the next file landed.

---

## Missing stub packages

| Module | Error count | Stub package | Recommended action |
|---|---:|---|---|
| `psycopg` | 14 | `psycopg[binary]` (ships own type info since v3.1) | **CI-only dep** per `CLAUDE.md`; runtime stays slim. Install in the eventual mypy CI step (`pip install psycopg[binary]` alongside `pip install ruff mypy` in `ci.yml::python-lint`). Do NOT add to `requirements.txt`. |
| `pandas` | 8 | `pandas-stubs` | Install in mypy CI job + dev. |
| `playwright` | (counted under `Any` returns) | none official | `cast(Browser, …)` at the seam in `enrichment_engine.py` + `discovery_engine.py`. |

---

## Plan to 95% coverage on target dirs

Order by lowest-friction-per-error (small files first to build a
pattern library, then large files with TypedDict-heavy strategy):

### Phase 1 — quick wins (~36 errors, 1 PR)

- **`src/integrations/email_sender.py`** (8 errors) — small file, no
  AI / Supabase complexity. Return types on the 4 helpers + the
  SMTPEmailSender class.
- **`src/utils/logging_config.py`** (5 errors) — handler-factory
  return types + `RotatingFileHandler` annotation.
- **`src/utils/ssrf_guard.py`** (7 errors) — already mostly typed;
  fix the 7 `--strict`-only errors. Drops into a state where the
  pre-commit selective regex genuinely matches "fully typed".
- **`src/utils/csv_helper.py`** (16 errors) — already in selective
  scope; finish.

Expected delta: **-36 errors**. Target-dir total: 173 → 137.

### Phase 2 — Gemini response TypedDicts (~110 errors, 1 PR)

Introduce `src/utils/gemini_types.py`:

```py
from typing import TypedDict, NotRequired

class OutreachDraftResult(TypedDict):
    draft: str
    subject: str
    lead_name: str
    lead_email: NotRequired[str]
    operator_name: str

class StrategicInsightsResult(TypedDict):
    summary: str
    next_actions: list[str]
    risk_signals: list[str]
    # ... (audit prompt snapshots in tests/fixtures/prompt_snapshots.json
    #      are the source of truth for shape)

# ... mapper, linkedin, pain_points, etc.
```

Then annotate `agentic_router.py` + `ai_mapper.py` return types. The
124 `union-attr` errors in agentic_router collapse because callers
stop seeing `Any`.

Expected delta: ~**-130 errors total** (mostly in `src/core/`, not
target dirs). Target dirs from this alone: -10 (ai_mapper.py +
processors-side calls).

**Landed 2026-05-23** in `chore/typecov-phase2-gemini-types`. Actual
delta: **-140** (607 → 467). Final scope went beyond TypedDict-only
to a "Gemini-boundary harden":

- `src/utils/gemini_types.py` — 5 response TypedDicts
  (`OutreachHooksResponse`, `EnrichmentDetailsResponse`,
  `DeepEnrichmentFieldsResponse`, `StrategicInsightsResponse`,
  `StrategicInsightsPriority`) + 4 per-task params TypedDicts
  (`UniqueKeyParams`, `DiscoverySearchParams`,
  `DatabaseQueryParams`, `FilteredParams`) + 3 narrowing helpers
  (`response_text`, `extract_function_call`, `typed_loads`).
- `src/utils/json_helper.py` — tightened return type to
  `Optional[dict[str, Any]]` + dict-isinstance guard before cast.
- 4 call-site files — `agentic_router.py` -123, `leadhunter.py` -5,
  `ai_mapper.py` -8, `enrichment_engine.py` -2.
- Supabase row narrowing — added local `client = self.db.client`
  pattern after every `if not self.db.client: return ...` guard,
  + `cast(Mapping[str, Any], leads[0])` at Supabase row reads.

Why the actual delta differed from the initial -130 estimate: the
original plan assumed TypedDicts alone would kill ~130, but the
diagnostic showed the 125 `union-attr` errors broke into 4 buckets
(only one fixed by TypedDicts). Expanded scope to the full boundary
narrowing — call-site + Supabase Optional + Gemini SDK Optional.
Decision and four-bucket breakdown documented in the PR description.

### Phase 3 — Supabase row TypedDicts (~33 errors, 1 PR)

Run `mcp__supabase__generate_typescript_types`, port to Python
TypedDicts in `src/utils/supabase_types.py` (`LeadRow`, `CampaignRow`,
`CampaignMessageRow`, `OrchestrationJobRow`). Annotate
`supabase_helper.py` return types.

Expected delta: -33 in `src/utils/supabase_helper.py` (full file
clears). Target-dir total: 137 → 104.

### Phase 4 — scraper TypedDicts (~53 errors, 1 PR)

`AuditResult` TypedDict for `seo_audit.py` (33 errors). Playwright
`Page` / `Browser` annotations via `cast` in `enrichment_engine.py`
(11) + `discovery_engine.py` (9).

Expected delta: -53 in target dirs. Target-dir total: 104 → **51** —
well under the 95% goal (~150 ceiling).

### Phase 5 — leadhunter follow-on + stub installs

- Annotate the 6 new helpers from refactor PR #186 (`_score_contacts`,
  etc.): ~6 errors cleared. Target-dir total: 51 → **45**.
- Install `pandas-stubs` in CI: ~8 import errors cleared (mostly in
  `src/scripts/export_leads.py`, outside target dirs but moves the
  global total).
- Install `psycopg[binary]` in mypy CI step: ~14 errors cleared (all
  in `src/scripts/`, outside target dirs).

### Phase 6 — `backend/main.py` + `src/core/`

After 1-5, the remaining 150 errors in `backend/main.py` and the 50+
left in `src/core/task_orchestrator.py` + `parallel_auditor.py` are
the largest unblocking step toward exit-code-0. Sized as a separate
multi-PR initiative.

---

## Weekly tracking

Append one row per Monday. Keep chronological.

| Week of | Total | Δ total | Target-dir | Δ tgt | Files clean | Notes |
|---|---:|---:|---:|---:|---:|---|
| 2026-05-22 | 593 | — | 173 | — | 8 / 42 | First scan; ci.yml not yet enforced |
| 2026-05-23 | 467 | **-140** | (see notes) | -10 | 8 / 45 | Phase 2 landed — `src/utils/gemini_types.py` + boundary harden across 4 Gemini call sites. agentic_router 149→26 (-123), leadhunter 47→42 (-5), ai_mapper 10→2 (-8), enrichment_engine 13→11 (-2), json_helper 3→0. Total 607 baseline (drift from 593) → 467 post. See chore/typecov-phase2-gemini-types. |

---

## Reproducing

```sh
# Python — mypy --strict (errors include backend/main.py via transitive import)
.venv/bin/pip install mypy pandas-stubs  # add psycopg[binary] for src/scripts/
.venv/bin/mypy --strict src/

# Authoritative total (matches mypy summary):
.venv/bin/mypy --strict src/ 2>&1 | grep -c ' error: '

# Per-file ranked
.venv/bin/mypy --strict src/ 2>&1 \
  | grep -E ': error: .*\[[^]]+\]$' \
  | awk -F: '{print $1}' | sort | uniq -c | sort -rn

# Per-code ranked
.venv/bin/mypy --strict src/ 2>&1 \
  | grep -oE '\[[^]]+\]$' | sort | uniq -c | sort -rn

# Frontend — already strict; audit any usages
grep -rn ": any" frontend/app frontend/utils \
  --include="*.ts" --include="*.tsx" --include="*.mjs"
grep -rn " as any" frontend/app frontend/utils \
  --include="*.ts" --include="*.tsx" --include="*.mjs"
```

## When this stops being useful

When `mypy --strict src/` exits 0 in CI, drop the per-file table and
keep only the headline + weekly delta. At that point the
`.pre-commit-config.yaml` selective-mypy hook expands to the whole
tree and this file is archival.
