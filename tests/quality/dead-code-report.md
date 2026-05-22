# Dead-Code Report

Generated: 2026-05-22
Branch: `main` (HEAD `814dd9b`)
Tools: vulture 2.16, deptry 0.25.1, ts-prune (npx), knip (npx), depcheck (npx).
`pip-autoremove` 0.10.0 unusable on Python 3.14 (`pkg_resources` removed
from modern `setuptools`); replaced with `deptry --requirements-files
requirements.in`.

## Summary

| Tool | Raw findings | True positives | False positives | Action |
|---|---|---|---|---|
| vulture | 2 | 0 | 2 | none |
| deptry | 86 | 1 (lxml) + 1 advisory | 84 | doc — lxml deferred to next lockfile regen |
| ts-prune | 34 | 0 | 34 | none (Next.js conventions + `.next/**` generated) |
| knip | 2 unused files + 1 type | 2 files | 1 type | **delete 2 files in this PR** |
| depcheck | 0 | 0 | 0 | none |

This PR ships **the 2 confirmed unused frontend files only**. Everything
else is documented for the next pass.

---

## True positives (this PR)

### `frontend/components/ExportButtons.tsx` — DELETE

Default-export React component. **Zero importers** in `frontend/` (excluding
`node_modules/` + `.next/`). Tracked, unmodified. `git rm` is safe — the
PR diff is exactly the file contents going away.

```
$ grep -rn "ExportButtons" frontend --include="*.ts" --include="*.tsx"
frontend/components/ExportButtons.tsx:7:export default function ExportButtons() {
```

### `frontend/utils/supabase/client.ts` — DELETE

Browser-side `createClient()` factory. Every prod caller hits the **server**
variant (`@/utils/supabase/server`) — see `app/api/proxy/[...path]/route.ts`,
`app/api/auth/signout/route.ts`, `app/login/actions.ts`. E2E specs spawn
their own `createClient` from `@supabase/supabase-js` directly with the
service-role key, not via this wrapper.

The browser-client pattern is part of the standard `@supabase/ssr`
template; this project's auth gate consciously routes every read/write
through the SSR server client, so the browser variant is dead. If a
future feature legitimately needs an in-browser RLS-scoped Supabase
session, regenerate from the Supabase SSR scaffold.

---

## True positives (deferred — out of scope for this PR)

### `lxml` (`requirements.in`) — remove on next lockfile regen

```
$ pip show lxml | grep -i required-by
Required-by:
```

True orphan. Not imported anywhere in `src/`, `backend/`, or `tests/`.
`beautifulsoup4` defaults to the stdlib `html.parser` (verified — every
`BeautifulSoup(html, 'html.parser')` site uses the stdlib parser
explicitly) and does **not** declare `lxml` as a requirement. Cited as a
dep in `requirements.in` only.

Not removed in this PR because:

1. `requirements.in` is **untracked** (`??` in `git status`) — the
   pip-tools migration is itself in-flight uncommitted work belonging
   to a separate change. Stomping on it here risks double-conflict on
   merge.
2. `requirements.txt` is tracked and was generated with
   `pip-compile --generate-hashes`. Editing `requirements.in` without
   regenerating the lockfile (which needs the operator's pip-tools
   environment + network) leaves the two out of sync, and the CI
   `lockfile-sync` gate (per `CLAUDE.md`) turns red.

**Action**: whoever finishes the `requirements.in` migration drops
`lxml==6.1.0` from line 24 then re-runs `make lock-python`.

### deptry DEP003 (advisory, follow-up only)

```
backend/main.py:16:1: DEP003 'pydantic' imported but it is a transitive dependency
backend/main.py:17:1: DEP003 'postgrest' imported but it is a transitive dependency
```

Both are imported directly but pulled in transitively via `fastapi` /
`supabase`. Explicit pins in `requirements.in` would protect against an
upstream dropping the transitive. Not in scope for a dead-code PR;
tracked here so the next dep-hygiene PR picks it up.

---

## False positives — DO NOT re-flag

### vulture

```
src/utils/query_profiler.py:132: unused variable 'exc_type' (100% confidence)
src/utils/query_profiler.py:132: unused variable 'tb' (100% confidence)
```

`__exit__(self, exc_type, exc_val, tb)` is the **Python context-manager
protocol signature**. The interpreter passes these positional args; the
implementation doesn't need to use them. Renaming to `_` would technically
silence vulture but breaks the readable protocol idiom and triggers IDE
tooling that special-cases the canonical names. Leave as-is.

### deptry DEP001 — first-party imports

55 of the 86 findings:

```
backend/main.py: 'src' imported but missing from the dependency definitions
src/core/agentic_router.py: 'src' imported but missing from the dependency definitions
src/utils/query_profiler.py: 'backend' imported but missing from the dependency definitions
```

`src/` and `backend/` are this repo's own packages, not PyPI deps. Future
runs should pass `--known-first-party=src,backend` (or migrate to
`pyproject.toml` with a `[tool.deptry]` section).

### deptry DEP001 — `psycopg`

14 hits across `src/scripts/check_*.py` + `tests/test_concurrent_writes.py`.
Per `CLAUDE.md`:

> **CI-only dep**: `psycopg[binary]>=3.1` is installed inline by every
> Supabase-DB job, not added to `requirements.txt` (backend talks to
> Supabase over PostgREST HTTPS, not Postgres wire — no need to ship a
> driver into the runtime image).

Deliberate. Add to the deptry ignore list when the config is migrated to
`pyproject.toml`.

### deptry DEP002 — framework-required pins

```
requirements.in: DEP002 'requests' defined as a dependency but not used in the codebase
requirements.in: DEP002 'urllib3' defined as a dependency but not used in the codebase
requirements.in: DEP002 'python-multipart' defined as a dependency but not used in the codebase
```

- **`requests`** — used by `tests/test_idor_sweep.py`,
  `tests/test_supabase_anon_bypass.py`,
  `tests/test_orchestrator_cooperative_cancel.py`. deptry only scans the
  paths passed on the CLI (`src backend`); tests aren't scanned. Split
  into a `requirements-dev.in` if a dev/runtime separation lands, but
  shipping `requests` in the prod image today is the simpler choice
  (~120 KB, already vendored via `urllib3 → requests`).
- **`urllib3`** — `Required-by: requests`. Explicit pin = security pin
  (forces a known-good version regardless of what `requests` resolves).
  Removing it lets requests pull any compatible urllib3. Keep.
- **`python-multipart`** — required by FastAPI's `UploadFile`/`File`/`Form`
  parameter parsing. Used by `backend/main.py:647`
  (`upload_leads(..., file: UploadFile = File(...))`). FastAPI 422s any
  multipart request without it installed. Genuine framework runtime
  requirement, never imported directly. Keep.

### ts-prune — Next.js framework conventions

24 of the 34 findings. Next 16 App Router exports default-export pages,
named HTTP-method handlers (`GET`/`POST`/...), and config exports
(`runtime`/`dynamic`/`metadata`) that the framework resolves at build
time. ts-prune doesn't model the Next router:

```
frontend/app/page.tsx:106 - default
frontend/app/layout.tsx:11 - default
frontend/app/layout.tsx:6 - metadata
frontend/app/api/proxy/[...path]/route.ts:208 - GET   # ...PUT/POST/DELETE/PATCH/OPTIONS
frontend/app/api/proxy/[...path]/route.ts:4 - runtime
frontend/app/api/proxy/[...path]/route.ts:5 - dynamic
frontend/proxy.ts:4 - proxy  # Next 16 root middleware convention
frontend/proxy.ts:8 - config
frontend/next.config.ts:69 - default
frontend/playwright.config.ts:13 - default
```

Document via a project-level ts-prune config (`.ts-prunerc` or
`tsconfig.dead-code.json`) to exclude `**/page.tsx`, `**/route.ts`,
`**/layout.tsx`, `*.config.ts`, `frontend/proxy.ts`.

### ts-prune — generated `.next/**`

8 findings under `frontend/.next/types/**` and
`frontend/.next/dev/types/**`. Build artifacts. Add `.next/` to the
ts-prune `--ignore` glob.

### ts-prune — `cookie-floor.d.mts` types

```
SameSite / SupabaseCookieOptions / HardenedCookieOptions  (used in module)
```

ts-prune emits "used in module" as a hint, not an unused warning. False
positive in the noise count.

### knip — `QueuedRequest` type

```
QueuedRequest  type  utils/offlineQueue.ts:24:13
```

Exported public type from the offline-queue API. Worth keeping exported
even if no current caller imports it — removing the export means an
in-progress consumer has to re-define a structurally-equivalent type.
Trivial to remove later if the offline-queue API stays single-consumer.
Borderline; leaving as-is for now.

---

## Reproducing this report

```sh
# Python (require venv at .venv/)
.venv/bin/pip install vulture deptry
.venv/bin/vulture src backend --min-confidence 80
.venv/bin/deptry src backend -rf requirements.in

# Frontend
npx --yes ts-prune --project frontend/tsconfig.json
npx --yes knip --directory frontend --no-progress
npx --yes depcheck frontend
```

## Net delta this PR

- `tests/quality/dead-code-report.md` (this file, +new)
- `frontend/components/ExportButtons.tsx` (delete)
- `frontend/utils/supabase/client.ts` (delete)

No code paths altered. No tests added or removed. `npm run build` +
`pytest tests/` should pass unchanged.
