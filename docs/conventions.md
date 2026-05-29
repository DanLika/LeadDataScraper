# Conventions

## Frontend layout

```
frontend/
  app/
    lib/        utilities, API clients, helpers (no React)
    hooks/      custom React hooks (useFoo)
    types/      shared type defs (no React, no runtime)
    components/ React components (.tsx)
    <route>/    page.tsx, layout.tsx, route.ts, server actions
```

Import via path alias `@/app/<lib|hooks|types|components>/<name>`.

### Placement rules

- **Pure utility (no React, no JSX)** → `app/lib/`
- **Custom hook (`useFoo`)** → `app/hooks/`
- **Type shared across ≥2 consumers** → `app/types/`
- **Single-consumer type** → stay colocated with the consumer
- **Next.js convention files** (`page.tsx`, `layout.tsx`, `route.ts`, server actions like `actions.ts`) → colocated with route, never moved
- **`.mjs` siblings** (`X.mjs` + `X.d.mts` + `X.test.mjs`) → move as an atomic triple

### History

Prior layout had a top-level `frontend/utils/` mixed with hooks and types. Reorganised
2026-05-29 (chore/frontend-lib-reorg) into the canonical `app/{lib,hooks,types}` tree.

### Test coverage

The `.mjs` test files (`url.test.mjs`, `cookie-floor.test.mjs`, `cookie-floor-fuzz.test.mjs`)
stay alongside their target module under `app/lib/`. CI runs them via `node --test`.
External references in `tests/test_open_redirect.py` and `.github/scripts/auth-smoke.mjs`
point to the new paths.
