# Tech Debt Register

Sweep date: **2026-05-22**
Search: `grep -rEn "TODO|FIXME|HACK|XXX|@deprecated"` over
`src/` + `backend/` + `frontend/app/` + `frontend/utils/`
(excluding `node_modules`, `.next/`).
Operator: Duško Ličanin (sole contributor).

## Headline

| Metric | Value |
|---|---:|
| Markers in **tracked** files on `origin/main` | **0** |
| Markers in **untracked** working-tree files | **5** |
| `TODO` | 5 |
| `FIXME` / `HACK` / `XXX` / `@deprecated` | 0 |

**The project ships zero shipped-debt markers.** Every `TODO` in
the working tree belongs to files that have never been pushed —
they're documentation of known limitations inside the in-flight
work the operator is staging. There is no decade-old `FIXME` rot
to triage.

## Counts per directory

| Directory | Tracked | Untracked |
|---|---:|---:|
| `src/` | 0 | 1 |
| `backend/` | 0 | 0 |
| `frontend/app/` | 0 | 0 |
| `frontend/utils/` | 0 | 4 |

## Register (ranked oldest first, then by severity)

Age proxy: file `mtime`. None of these files exist in git history,
so `git blame` returns nothing; the sole-operator git config
attributes every line to Duško Ličanin
(`duskolicanin1234@gmail.com`) by construction.

### 1. `src/scripts/check_db_bloat.py:4`

- **Marker**: `TODO` (inside the module docstring)
- **Author**: Duško Ličanin
- **Age**: 0 days (file untracked; `mtime = 2026-05-22`)
- **Type**: feature follow-up
- **Severity**: low (advisory script, runs weekly, optional enhancement)
- **Verdict**: **ticket**

> Three checks per core table (and on first run, a snapshot for WoW
> comparison via a CI artifact — left as a follow-up TODO since
> artifact-fetch-across-runs adds workflow complexity)

Wants week-over-week (WoW) growth comparison. Blocked on the
`actions/cache@v4` keyed-baseline pattern that other reports in
`security.yml` already use (e.g. `storage_report.py`). When this lands,
copy the cache wiring from the storage report verbatim. Not a
blocker — the script's current dead-tuple-ratio + table-size checks
still ship value on day one.

### 2. `frontend/utils/supabase/cookie-floor-fuzz.test.mjs:170`

- **Marker**: `TODO` (inside `test.skip('TODO: Domain wider than current origin should be narrowed', …)`)
- **Author**: Duško Ličanin
- **Age**: 0 days (file untracked; `mtime = 2026-05-22`)
- **Type**: security hardening (defense in depth)
- **Severity**: medium-security — current defense relies on the
  browser refusing `Domain=.com` at the `Set-Cookie` parser layer.
  Belt-and-braces narrow-domain validation in the cookie floor would
  catch malformed origin-wider values before they leave the server.
- **Verdict**: **ticket**

Documented in CLAUDE.md as one of "2 documented-skip TODOs: domain
narrowing + `__Host-` prefix". Tracked invariant. Promote the
`test.skip` to a live test once `hardenCookieOptions` learns the
current host (API surface change). The skipped test body already
contains the failing assertion the operator promotes.

### 3. `frontend/utils/supabase/cookie-floor-fuzz.test.mjs:184`

- **Marker**: `TODO` (inside `test.skip("TODO: __Host- prefixed cookies must have Path=/ and no Domain", …)`)
- **Author**: Duško Ličanin
- **Age**: 0 days (file untracked; `mtime = 2026-05-22`)
- **Type**: security hardening (defense in depth)
- **Severity**: medium-security — `__Host-` prefix semantics are
  browser-enforced today (Chrome / Firefox / WebKit reject malformed
  `__Host-*` cookies). A server-side check is belt-and-braces only.
- **Verdict**: **ticket**

Documented in CLAUDE.md alongside #2 above. Blocked on the floor's
API surface growing to include the cookie name (currently it only
sees options, not the name).

### 4. `frontend/utils/supabase/cookie-floor-fuzz.test.mjs:16`

- **Marker**: `TODO` (inside file-header docstring)
- **Author**: Duško Ličanin
- **Age**: 0 days
- **Type**: documentation pointer (refers to #2 below in the file)
- **Severity**: low — pure documentation cross-reference
- **Verdict**: **delete with #2** — when the domain-narrowing
  `test.skip` is promoted, drop this header-comment line too.

> as a TODO with a failing assertion the operator can promote

### 5. `frontend/utils/supabase/cookie-floor-fuzz.test.mjs:19`

- **Marker**: `TODO` (inside file-header docstring)
- **Author**: Duško Ličanin
- **Age**: 0 days
- **Type**: documentation pointer (refers to #3 below in the file)
- **Severity**: low — pure documentation cross-reference
- **Verdict**: **delete with #3**

> The floor doesn't know the cookie name; left as a TODO.

---

## Categorization

| Category | Count | Items |
|---|---:|---|
| **Security hardening (defense in depth)** | 2 | #2, #3 — cookie-floor follow-ups |
| **Feature follow-up** | 1 | #1 — bloat report WoW growth |
| **Doc cross-reference** | 2 | #4, #5 — collapse into their referenced items |

## Verdicts summary

| Verdict | Count |
|---|---:|
| **fix now** | 0 |
| **ticket** | 3 (#1, #2, #3) |
| **delete (stale)** | 0 |
| **delete on resolution of linked item** | 2 (#4, #5) |

No marker is itself a fix-now signal — every one is a deliberate
"known limitation, here is the test or doc that would activate it"
note. The `test.skip`'d cases (#2, #3) are the canonical
write-failing-test-first pattern, just with the test parked rather
than failing CI.

---

## What the absence says

A zero-shipped-debt finding is itself a finding. The codebase
trades off "fix it later" via:

1. Out-of-source design records — `CLAUDE.md`,
   `docs/ci-architecture.md`, `docs/secret-inventory.md` —
   instead of in-source comment debt.
2. `test.skip('TODO: …')` so a parked test sits next to its
   matching production code; promotion is a single keyword swap.
3. Dedicated `tests/quality/*-report.md` (dead-code, complexity,
   type-coverage) instead of `TODO` strings inside the relevant
   files.

Recommendation: **keep this register in `docs/` and re-run weekly**
alongside the type-coverage and complexity reports. The 5-minute
sweep at `grep -rEn "TODO|FIXME|HACK|XXX|@deprecated"` is cheap
enough to be a Monday-morning ritual, and the round-number `0` on
the headline is the right signal that the convention is holding.

If/when a new marker ships into tracked code, add a row here with:

```
- File:line
- Author (git blame `<filename>` -L `<n>,<n>`)
- Age (today - commit date of the blame entry)
- Type (security | bug | feature | docs)
- Severity (low | medium-security | high-security | bug | blocker)
- Verdict (fix-now | ticket | delete-stale)
- One-line rationale + link to ticket / linked test
```

## Reproducing

```sh
grep -rEn "TODO|FIXME|HACK|XXX|@deprecated" \
  src/ backend/ frontend/app/ frontend/utils/ \
  | grep -vE "node_modules|/.next/"

# Per finding, author + age:
git blame -L <n>,<n> -- <file>
# (returns nothing if the file is untracked — note in the register)

# File mtime fallback for untracked:
stat -f '%Sm' -t '%Y-%m-%d' <file>
```

---

## Next sweep

**Run again**: Monday 2026-05-25 (or after PR #185 / #186 / #187
land, whichever is later, in case the merge introduces or surfaces
new markers).
