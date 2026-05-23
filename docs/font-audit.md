# Inter font — silent-fallback verdict

**Confirmed bug, observed 2026-05-22 via chrome-devtools-mcp on
`http://localhost:3000/` (prod build, post-login).** `globals.css`
declares `font-family: 'Inter', system-ui, …` but no `next/font/google`
import and no `.woff*` ships, so the browser silently falls through to
`system-ui` for body/headings and to UA default `Arial` for form
controls (`<button>`, `<input>` don't inherit font from `body`).

Browser observations:
- `document.fonts` set is **empty** (0 FontFace objects).
- `performance.getEntriesByType('resource')` shows **zero** `.woff*`
  requests on cold load.
- Computed `font-family`: `body`, `h1`, `h2` = the declared fallback
  stack (Inter never resolves → `system-ui`). `button`, `input` =
  **`Arial`** (UA default — not even reading `body`'s stack).

Recommendation: pick one and commit.
- **Option A (recommended, zero net-new bytes):** drop `'Inter'` from
  `--font-main` in `frontend/app/globals.css`. The fallback already
  renders; just stop lying about it. Buttons/inputs need an explicit
  `font-family: inherit` to pick up the body stack.
- **Option B (ship Inter for real):** add `import { Inter } from
  'next/font/google'` in `frontend/app/layout.tsx`, instantiate with
  `display: 'swap'`, and wire `Inter.variable` onto `<html>`. CLS-safe
  (`size-adjust` from `next/font` matches the fallback metrics).
