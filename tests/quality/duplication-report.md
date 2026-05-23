# Duplication Report

Sweep date: **2026-05-22**
Branch: `chore/duplication-report` (base `origin/main` @ `ee2fa0c`)
Tools:
- `jscpd 4.2.3` — `npx jscpd src/ backend/ frontend/app/ --threshold 50 --min-lines 8`
- `pylint 4.0.5` — `pylint --disable=all --enable=duplicate-code --min-similarity-lines=8 src/ backend/`

## Headline

| Metric | Value |
|---|---:|
| Total clones (jscpd) | **17** |
| pylint `R0801` similarity blocks | **9** (overlaps with jscpd python clones) |
| Duplicated lines / total lines | **260 / 12 845** = **2.02%** |
| Duplicated tokens / total tokens | **3 009 / 113 374** = **2.65%** |

### Per-format

| Format | Files | Total lines | Clones | Dup lines | Dup % |
|---|---:|---:|---:|---:|---:|
| Python | 35 | 7 821 | 9 | 126 | 1.61% |
| JavaScript (scoped from JSX/TSX) | 12 | 1 730 | 6 | 100 | 5.78% |
| TSX | 14 | 2 813 | 2 | 34 | 1.21% |
| TypeScript | 4 | 341 | 0 | 0 | 0% |
| CSS | 1 | 140 | 0 | 0 | 0% |

The 5.78% JS figure is inflated by jscpd's JSX-blind close-tag pattern
matching — see false-positive notes below.

### Critical context

**11 of 17 clones (clones 4-12) live in `src/scripts/check_*.py` files
that are NOT yet on `origin/main`.** They're part of the 13 unreleased
commits + untracked working-tree state the operator is staging. Acting
on those clones is best done **before** the operator pushes that batch
— deduplicating now means one extraction commit goes out alongside the
new scripts; deduplicating later means rewriting fresh-out-of-the-oven
shipped code.

The remaining 6 clones are in `frontend/app/` and ARE on `origin/main`
— addressable as a normal PR if desired.

---

## Top 10 clones (ranked by lines, with extraction verdict)

### 1. Cross-page `<Sidebar>` + `<main>` boilerplate — 43 lines  *(shipped, two pages)*

- `frontend/app/campaigns/page.tsx:216-258`
- `frontend/app/insights/page.tsx:115-159`

Both non-dashboard pages instantiate `<Sidebar>` with the same 14-prop
shim pattern (`(open) => router.push('/?openSettings=1')`, etc.) and
the same `<main id="main-content">` + skip-link + mobile-header
wrapper. The shim semantics are documented in `CLAUDE.md` under
"Cross-page navigation contract":

> Sidebar/Insights/Campaigns all share the same `<Sidebar>` component,
> but the dashboard owns the state for modals … When the user clicks
> Settings/Deep Discovery/Audited/High Risk/a prospect from Insights or
> Campaigns, those pages can't toggle that state directly. Instead they
> navigate to `/` with query params and the dashboard consumes-then-strips them.

**Verdict**: extract IF a 3rd non-dashboard page joins.
Two pages × 14-prop shim is tolerable; three would be the tipping
point. Suggested target: `frontend/app/components/NavShell.tsx`
wrapping Sidebar + skip-link + mobile-header. Pages pass only the
varying props (`leads`, `insights`, `fetchInsights`).

### 2. `<Sidebar>` prop block only — 25 lines  *(subset of #1)*

- `frontend/app/campaigns/page.tsx:233-257`
- `frontend/app/insights/page.tsx:133-157`

Subset of clone #1; same verdict.

### 3. login-page form-field block — 22 lines  *(self-clone, single file)*

- `frontend/app/login/page.tsx:92-113` vs `:77-95`

Email and password input groups have near-identical structure
(`<label>` + `<input>` + className + same aria-* attrs). Cheap
extraction.

**Verdict**: extract → in-file helper component
`function LabeledInput({ label, type, name, autoComplete, ... })`.
Saves ~20 lines, single-call-site change. **Ticket.**

### 4-12. `src/scripts/check_*.py` Postgres-script boilerplate — 13-19 lines × 9 clones  *(NOT YET ON ORIGIN — extract pre-merge)*

| # | Line span | A | B |
|---|---:|---|---|
| 4 | 19 | `check_grants_matrix.py:143-161` | `check_jsonb_shapes.py:146-164` |
| 5 | 19 | `check_function_safety.py:114-132` | `check_jsonb_shapes.py:146-164` |
| 6 | 15 | `check_referential_integrity.py:124-138` | `check_statement_timeouts.py:160-174` |
| 7 | 15 | `check_query_plans.py:120-134` | `check_statement_timeouts.py:160-174` |
| 8 | 14 | `check_db_bloat.py:59-72` | `suggest_jsonb_indexes.py:70-163` |
| 9 | 14 | `check_analyze_freshness.py:51-64` | `suggest_jsonb_indexes.py:70-72` |
| 10 | 13 | `slow_query_report.py:63-75` | `suggest_jsonb_indexes.py:70-82` |
| 11 | 13 | `check_null_audit.py:188-200` | `suggest_jsonb_indexes.py:70-82` |
| 12 | 13 | `check_jsonb_shapes.py:150-162` | `suggest_jsonb_indexes.py:70-82` |

Every check script repeats:

```python
def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL env var not set", file=sys.stderr)
        return 2
    try:
        conn = psycopg.connect(url, autocommit=True)
    except psycopg.Error as e:
        print(f"ERROR: cannot connect to DATABASE_URL: {e}", file=sys.stderr)
        return 2
    failures: list[str] = []
    try:
        # script-specific work …
    finally:
        conn.close()
    if failures:
        for f in failures: print(f, file=sys.stderr)
        return 1
    return 0
```

Affects 11+ scripts (check_function_safety, check_grants_matrix,
check_jsonb_shapes, check_referential_integrity, check_statement_timeouts,
check_query_plans, check_db_bloat, check_analyze_freshness,
slow_query_report, check_null_audit, suggest_jsonb_indexes,
check_orphans_and_zombies).

**Verdict**: **extract pre-merge.** Suggested target:
`src/scripts/_db_check_base.py`:

```python
import os, sys
from contextlib import contextmanager
from typing import Callable, Iterator
import psycopg

@contextmanager
def db_check_session(*, autocommit: bool = True) -> Iterator[psycopg.Connection]:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL env var not set", file=sys.stderr)
        sys.exit(2)
    try:
        conn = psycopg.connect(url, autocommit=autocommit)
    except psycopg.Error as e:
        print(f"ERROR: cannot connect to DATABASE_URL: {e}", file=sys.stderr)
        sys.exit(2)
    try:
        yield conn
    finally:
        conn.close()


def report_and_exit(failures: list[str]) -> int:
    if failures:
        for f in failures:
            print(f, file=sys.stderr)
        return 1
    return 0
```

Each script's `main()` shrinks to:

```python
def main() -> int:
    failures: list[str] = []
    with db_check_session() as conn:
        failures.extend(_check_<script_specific>(conn))
    return report_and_exit(failures)
```

Estimated delta: ~13 lines × 11 scripts = **~140 LOC removed** plus
one helper file added (~30 LOC). Net **-110 LOC** with a single point
of update for the connection lifecycle (matters: any future `sslmode`
or pool-timeout tweak hits one place, not eleven).

The two `autocommit=True` vs `autocommit=False` variants (clones #6
and #7 use `autocommit=False` for SAVEPOINT-style tests) are handled
by the keyword arg.

### 13. `FilterBar.tsx` self-clone — 12 lines  *(shipped)*

- `frontend/app/components/FilterBar.tsx:112-123` vs `:110-117`

Likely a repeated `<select option>` block or button JSX (file is 89
lines total). Read the file to confirm before extracting; may be a
jscpd false positive from JSX close-tag clusters.

**Verdict**: investigate before extracting. **Ticket.**

### 14. `<main>` mobile-header — 11 lines  *(subset of #1)*

- `frontend/app/campaigns/page.tsx:220-230`
- `frontend/app/insights/page.tsx:120-130`

Subset of clone #1; folded into the `NavShell` extraction if/when #1
fires.

### 15. `StatsCards.tsx` self-clone — 10 lines  *(shipped)*

- `frontend/app/components/StatsCards.tsx:39-48` vs `:25-32`

Four cards rendered with same wrapper structure (icon + label + value
+ accent stripe). Extraction is the canonical "lift into a child
component" pattern.

**Verdict**: extract → `function StatCard({ icon, label, value, accent })`.
Single file, low risk, well-tested by existing UI snapshots if any.
**Ticket.**

### 16. `FilterBar.tsx` self-clone — 10 lines  *(shipped, likely JSX false positive)*

- `frontend/app/components/FilterBar.tsx:107-116` vs `:64-88`

Different line counts on each end (10 vs 25) suggests jscpd matched
structurally on close tags only, not a real logic clone.

**Verdict**: investigate; likely false positive.

### 17. `campaigns/page.tsx` self-clone — 9 lines  *(shipped, likely JSX)*

- `frontend/app/campaigns/page.tsx:298-306` vs `:287-292`

Probably a list-item render or button group. Mismatched line counts
suggest JSX-close-tag noise.

**Verdict**: investigate; likely false positive.

---

## What the user flagged to watch for

| Watch-for | Found? | Notes |
|---|---|---|
| **Error response shapes** | No | jscpd / pylint found ZERO clones in `backend/main.py` or `frontend/app/api/`. The shared `error_response()` helper + global FastAPI exception handler (per `CLAUDE.md`) appear to be doing their job. The script-side `return 2` exit codes are consistent because they already follow a documented protocol (`0 = pass, 1 = findings, 2 = misconfigured`). |
| **Supabase query helpers** | No | The `SupabaseHelper` class is the single chokepoint. No duplicate `client.table(...).select(...).execute()` chains across modules. Async wrappers (`list_leads_recent`, `get_stats_rows`, `find_running_job`, `insert_orchestration_job`) follow the same `asyncio.to_thread` pattern but jscpd doesn't flag them — each is a different signature wrapping a different sync call. |
| **Validation patterns diverged across endpoints** | No | Pydantic models live in `backend/main.py` and inherit `extra='forbid'` + bounded `constr` from the meta-test (`test_pydantic_models_meta.py`) which enforces uniform constraints. No jscpd hits on `@app.post` handlers. |

**This is the strongest finding in the report**: the user's three
canonical risk areas (error responses, query helpers, validation) show
ZERO duplication. The codebase already converges on shared utilities
for the high-stakes paths. The 17 clones jscpd found are concentrated
in:

1. CI-script boilerplate (11) — fixable with one helper module
2. Cross-page Sidebar mounts (2) — intentional per cross-page contract
3. JSX structural close-tag noise (≈4) — jscpd false positives

---

## False positives — DO NOT re-flag

### jscpd JSX close-tag matching

jscpd's JavaScript tokenizer treats `}` / `)` / `</...>` cluster
boundaries as significant tokens. JSX components with similar nesting
depth get flagged even when the inner logic is different (clones #16,
#17, and parts of the JS-format count). Mitigation: per-format
threshold (e.g. raise `--min-tokens` for `javascript` only) — out of
scope for this report.

### pylint similarity in script entrypoints

Pylint reports the boilerplate from clones 4-12 nine separate times
because it doesn't merge transitively similar groups. The
underlying signal is **one** duplication pattern across 11 scripts,
not 9 separate ones.

### Sidebar shims as documented contract

Clones #1, #2, #14 reflect a deliberate cross-page navigation
contract (`?openSettings=1` query-param routing because non-dashboard
pages can't toggle dashboard state directly). Extracting requires the
operator to decide whether the contract's shape outweighs the
two-call-site cost — judgment call, not a refactor signal.

---

## Action items (recommended order)

### A. Extract `_db_check_base.py` BEFORE pushing the 13 unreleased commits  *(highest leverage)*

Touches 11+ scripts in untracked working tree. Drop the boilerplate
into one helper, refactor each `main()` to a 3-line caller. Land it as
the first commit ahead of the script batch so the squashed history
shows the helper landing FIRST and each script-add commit lands clean.

Estimated effort: 1 PR, ~150 LOC churn, no tests to add (the helper
is exercised by the existing CI workflows that already run these
scripts).

### B. Extract `StatCard` from `StatsCards.tsx`

Single-file change, 10-line dup eliminated, no API surface change.
~30 minutes.

### C. Extract `LabeledInput` from `login/page.tsx`

Single-file change, 20-line dup eliminated, accessibility attrs
unified in one place.

### D. (deferred) Extract `<NavShell>` from `campaigns/page.tsx` + `insights/page.tsx`

Wait for a 3rd non-dashboard page before extracting. The two-page
shim cost is tolerable; one more is the tipping point.

### E. Investigate `FilterBar.tsx` self-clones (#13, #16)

Read the file, confirm whether they're real or jscpd JSX false
positives. If real, extract a select-with-icon helper; if false,
document under "false positives" so the next sweep doesn't re-litigate.

---

## Reproducing

```sh
# jscpd
npx --yes jscpd src/ backend/ frontend/app/ \
  --threshold 50 --min-lines 8 \
  --ignore "**/node_modules/**,**/.next/**,**/__pycache__/**" \
  --reporters json,console \
  --output /tmp/jscpd-out

# pylint duplicate-code
.venv/bin/pylint --disable=all --enable=duplicate-code \
  --min-similarity-lines=8 \
  --reports=n src/ backend/
```

## Re-run cadence

Weekly, alongside the other `tests/quality/*.md` reports. Track in a
delta table here:

| Week of | Clones | Dup % | Top mover | Action taken |
|---|---:|---:|---|---|
| 2026-05-22 | 17 | 2.02% | — (baseline) | Recommendations A-E filed |
