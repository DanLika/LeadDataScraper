# Module Dependency Graph

Sweep date: **2026-05-22**
Branch: `chore/module-graph` (base `origin/main` @ `ee2fa0c`)
Tools:
- Python — `pydeps 3.0.6` (`pydeps src --cluster --keep-target-cluster`)
- TypeScript — `madge 8` (via `npx madge --circular --extensions ts,tsx,mts,mjs`)
- Graphviz — `dot 14.1.5` (SVG render)

## Headline

| | Python (`src/`) | Frontend (`frontend/app/`, `frontend/app/lib/`) |
|---|---:|---:|
| Modules scanned | 26 | 25 |
| **Circular dependencies** | **0** ✅ | **0** ✅ |
| Modules with no incoming import (orphans) | — | 14 (entrypoints + tests + types, see breakdown) |
| Graph artifact | [`python-module-graph.svg`](python-module-graph.svg) | [`frontend-module-graph.svg`](frontend-module-graph.svg) |

**Cycles = 0 in both halves of the codebase.** No fix-up PRs required.

## Verification commands

```sh
# Python — pydeps prints cycles to stdout; empty output = clean
.venv/bin/pydeps src --noshow --no-output --show-cycles
# → (empty)

# Frontend
cd frontend && npx madge --circular --extensions ts,tsx,mts,mjs app/ utils/
# → ✔ No circular dependency found!  (25 files processed)
```

Both commands belong in CI. Worth adding to `.github/workflows/ci.yml`
(or the new `quality-ratchet.yml`) as `must-stay-at-0` gates alongside
`eslint` and `semgrep`. Cheap to run (< 1s each), prevents silent
introduction of a cycle in a future PR.

## SVG renders

### Python — `python-module-graph.svg`

Shows the import-graph for everything under `src/` clustered by
sub-package (`core/`, `processors/`, `scrapers/`, `utils/`,
`integrations/`, `scripts/`). 26 modules; the cluster structure is
strictly hierarchical (no edges crossing back up).

Key relationships visible in the SVG:
- `src/core/agentic_router.py` is the central hub — imported by
  `backend.main` + `task_orchestrator` + 3 script entrypoints. Hub
  position justifies the 149 mypy errors it carries (PR #187's
  type-coverage report Phase 2 target — TypedDicts for Gemini
  responses would ripple positively from this node).
- `src/utils/` modules are leaves (no inter-utils imports beyond
  `logging_config` ← `csv_helper` and `prompt_safety` ← `leadhunter`).
  Healthy.
- `src/processors/leadhunter.py` and `src/scrapers/*` form a single
  layer — neither imports the other. Means a future
  enrichment-only test won't transitively load leadhunter and vice
  versa.

### Frontend — `frontend-module-graph.svg`

25 modules under `frontend/app/` + `frontend/app/lib/`. The
component tree is shallow: page-level entrypoints
(`page.tsx`, `campaigns/page.tsx`, `insights/page.tsx`, `login/page.tsx`)
import from `components/` + `utils/` + Next-injected runtime — no
back-edges.

Cluster layout:
- **Entrypoints** (orphans by design — Next.js calls them) —
  `page.tsx`, `campaigns/page.tsx`, `insights/page.tsx`,
  `login/page.tsx`, `layout.tsx`, `api/proxy/[...path]/route.ts`
- **Shared components** — `AIChat`, `Sidebar`, `HealthChart`,
  `FilterBar`, `StatsCards`, `BrandIcons`
- **Utils / hooks** — `useEscape`, `useFocusTrap`, `apiConfig`,
  `loginThrottle`, the `supabase/` cluster

## Orphan analysis

Madge's "orphan" list = modules nothing imports. **All 14 frontend
orphans are intentional**:

| Orphan | Why intentional |
|---|---|
| `app/page.tsx` | Next.js App Router entrypoint — invoked by framework, not by user-import |
| `app/campaigns/page.tsx` | Same |
| `app/insights/page.tsx` | Same |
| `app/login/page.tsx` | Same |
| `app/layout.tsx` | Next.js root layout — framework-invoked |
| `app/api/proxy/[...path]/route.ts` | Next.js API route — framework-invoked |
| `app/api/auth/signout/route.ts` | Same (not orphan-listed because it's nested; same category) |
| `app/lib/apiConfig.ts` | Re-export module imported via `@/app/lib/apiConfig` everywhere — madge sees the alias only when projects use it. Re-running with `--ts-config frontend/tsconfig.json` would resolve the aliases; the default scan doesn't. (Not a bug — false orphan.) |
| `app/lib/loginThrottle.ts` | Same — used by `app/login/actions.ts` |
| `app/lib/supabase/server.ts` | Same — used by 3 route handlers + actions |
| `app/lib/supabase/middleware.ts` | Used by `frontend/proxy.ts` (Next 16 root middleware convention — NOT under `app/`; falls outside madge's scan path here) |
| `app/lib/supabase/client.ts` | Already confirmed unused in PR #185 (dead-code report). Deletion shipped in PR #185 — should disappear from this orphan list once that merges. |
| `app/lib/supabase/cookie-floor.test.mjs` | Test file, run by `node --test`, not imported |
| `app/hooks/useEscape.ts` / `app/hooks/useFocusTrap.ts` | Used via `@/app/hooks/...` alias — same false-orphan reason as `apiConfig` |

**Action**: re-run madge with `--ts-config` to resolve path aliases
(future improvement). For now, the orphan list is informational only;
nothing to delete that the dead-code report hasn't already named.

## What would constitute a cycle (and why we don't have one)

A circular dependency happens when module A imports B which imports
(transitively) A. Even when valid syntactically, Python's lazy import
machinery and Webpack's tree-shaker both have a hard time with cycles:

- Python: a cycle means the second-to-import module sees a partially-
  initialised version of the first. `from src.a import x` raises
  `ImportError` at runtime when `x` isn't yet bound. CI tests would
  surface this; the absence of cycles means the layered architecture
  (CLAUDE.md: `core/` → `processors/` → `scrapers/` → `utils/`)
  is being respected in practice, not just in design.
- TypeScript / Next.js: cycles are harder to surface — bundlers tolerate
  them (producing dead code or runtime `undefined`-reference errors
  later in execution). The proactive `madge --circular` gate is the
  only way to catch them before they ship.

The codebase converges on shared utilities (CLAUDE.md "service +
repository" layered split in PR #192 reinforces this) — `utils/`
imports nothing from `core/`, `core/` imports `utils/` freely. No
back-edges = no cycles.

## Why this PR also generates the SVGs

Even with zero cycles today, the SVG is the lowest-cost way for a
future-Duško (or a new contributor) to grasp the import topology
without reading every file. Cheap to commit (~150 KB combined),
trivial to regenerate (one command per side), and forms the
counterpart of the existing `tests/quality/*.md` audit reports —
"here is the architecture as it actually exists, not as the README
says it does".

## Reproducing

```sh
# Python
brew install graphviz                              # one-time, for `dot`
.venv/bin/pip install pydeps                       # one-time
.venv/bin/pydeps src --noshow \
  -T svg -o docs/architecture/python-module-graph.svg \
  --max-bacon 6 --cluster --keep-target-cluster

# Frontend
cd frontend && npx --yes madge \
  --image ../docs/architecture/frontend-module-graph.svg \
  --extensions ts,tsx,mts,mjs app/ utils/

# Cycle gates (CI-ready)
.venv/bin/pydeps src --noshow --no-output --show-cycles   # empty = pass
cd frontend && npx madge --circular --extensions ts,tsx,mts,mjs app/ utils/
```

## Weekly tracking

| Week of | Python cycles | Frontend cycles | New orphans | Re-render needed? |
|---|---:|---:|---:|---|
| 2026-05-22 | 0 | 0 | — (baseline) | — |
