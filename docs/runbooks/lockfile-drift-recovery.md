# `package-lock.json` drift recovery (cluster #1)

**Status**: RESOLVED 2026-05-29 by PR #398 (`02e99cc`). Cluster #1 of issue
#363 closed. Future `npm ci` reds are REAL regressions, not infra noise.

## Symptom

Seven CI jobs fail with `npm ci` errors on a fresh checkout:

- Container scan (Trivy)
- Playwright E2E (chromium / firefox / webkit)
- Lighthouse CI
- pytest (frontend asset build step)
- ESLint (no warnings)
- Quality ratchet (frontend stage)
- pre-commit (local-CI parity, frontend hook)

Each job logs variants of:

```
npm error code EUSAGE
npm error `npm ci` can only install packages when your package.json and
  package-lock.json are in sync.
  Missing: @swc/helpers@... from lock file
```

## Root cause

Next 16's swc toolchain pulls `@swc/helpers` as a transitive dependency.
The version range resolves differently between Node 18 (developer laptop) and
Node 20 (CI runner). Without regenerating `package-lock.json` on the CI Node
version, the lockfile omits the resolved `@swc/helpers` graph that Node 20
needs. `npm ci` fails-closed.

Symptom presents as a wide cluster of unrelated jobs because every CI job
that ships a frontend bundle starts with `npm ci`.

## Fix recipe

```bash
# 1. Match CI Node version (20.20.2)
nvm use 20.20.2  # or: nvm install 20.20.2

# 2. Clean install from package.json (DO NOT use `npm install` — that
#    promotes ranges; we want a clean regen against existing semver ranges)
cd frontend
rm -rf node_modules package-lock.json
npm install
npm ci  # second run validates the regen — should be silent

# 3. Verify @swc/helpers landed
grep -c '@swc/helpers' package-lock.json
# Expected: > 0 (was 5 → 7 in PR #398)

# 4. Build smoke-test
npm run build
```

`package.json` untouched. Diff is lockfile-only (PR #398: 1 file, +757/-659
lockfile lines).

## Recurrence guard

- **Always regen on CI Node version** — `nvm use 20.20.2` before any
  `package-lock.json` regen. Node 18-resolved lockfile breaks Node 20 CI.
- **Spot-check after regen**:
  ```bash
  grep -c '@swc/helpers' frontend/package-lock.json
  # Always > 0 on Node 20.
  ```
- **Don't admin-merge `npm ci` reds** going forward. Per cluster #1 close,
  any cluster of frontend-bundle job reds is a REAL regression. Investigate.
  Memory `ci_six_clusters_2026-05-28.md` CAVEAT: admin-merge precedent
  (#357 / #358 / #366) silently absorbed real Phase 14+15 regressions in
  earlier clusters. NEVER admin-merge again without
  `gh run view --log-failed | head -80` per failing job.

## Proof of close

- PR #398 admin-merged with all cluster #1 jobs GREEN: Container scan (3m2s),
  Playwright E2E (2m13s), Lighthouse CI (48s), pytest (1m26s).
- Throwaway docs-only PR #401 ran post-merge canary: same cluster #1 jobs
  GREEN. Proof run:
  <https://github.com/DanLika/LeadDataScraper/actions/runs/26629701439>

## Remaining reds (NOT cluster #1, continue to track)

- **ESLint** (`react-hooks/set-state-in-effect`) — 12 errors on `app/page.tsx`.
  See [project-page-tsx-split-deferred memory](./README.md#page-tsx-split).
- **npm test** — script path drift `utils/supabase/` vs actual
  `app/lib/supabase/`. Trivial 1-line `package.json` fix; bundle separately
  from any lockfile change to keep diff atomic.
- **Quality ratchet** — `mypy_strict 637 → 638 (+1)`. Pre-existing main drift.
  See `tests/quality/` + operator memory
  `quality_baseline_drift_exception_template.md` for the documented refresh
  procedure.
- **pre-commit (local-CI parity)** — `end-of-files` / `trailing-whitespace` /
  `ruff` on `backend/main.py`. Cluster #2 territory. Real investigation
  needed, not admin-merge bypass.

## Py3.10 vs Py3.12 drift (sibling concern)

Same general shape as
[py310-isoformat-tolerance](./py310-isoformat-tolerance.md). CI Python 3.12
hides Py3.10 stdlib gaps that only surface in prod. Eventually should wire
parallel Py3.10 pytest job — pending operator decision on CI cost budget.

## Related

- Memory: `cluster_1_lockfile_drift_resolved.md`,
  `ci_six_clusters_2026-05-28.md`, `ci_runner_allocation_failure_2026-05-23.md`,
  `issue363_ci_infra_cleanup_tracker.md`
- PR: #398 (`02e99cc`), #401 (closed canary)
- Issue: #363 (cluster #1 closed; clusters #2–6 still tracked)
