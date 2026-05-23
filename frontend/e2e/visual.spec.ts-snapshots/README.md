# Visual regression baselines

Pixel-locked screenshots consumed by `e2e/visual.spec.ts::toHaveScreenshot`.
Each PNG is the "golden" render — every CI run on macOS compares the live
DOM against the matching file and fails on >1% pixel drift
(`SNAP_OPTS.maxDiffPixelRatio = 0.01`).

## Where baselines run

**macOS only.** `visual.spec.ts` carries a
`test.skip(process.platform !== 'darwin')` guard at file top. CI ubuntu
runners skip the file entirely — Linux fontconfig resolves `Inter →
DejaVu Sans` while macOS resolves `Inter → SF Pro`, and that font
fallback blows past the 1% diff budget on body text alone.

The matching `frontend/playwright.config.ts` also excludes `visual.spec.ts`
from the Firefox + WebKit projects (`testIgnore: /(full-flow|mobile|visual)\.spec\.ts$/`)
so cross-browser glyph drift can't churn the diff for free.

## Capture environment (last regeneration)

| Field          | Value                                          |
| -------------- | ---------------------------------------------- |
| Date           | 2026-05-23                                     |
| OS             | macOS 26.5 (Sequoia)                           |
| Architecture   | arm64 (Apple M4 Pro)                           |
| Playwright     | 1.60.0                                         |
| Browser        | chromium-headless-shell 1223 (Chrome 148.0.7778.96) |
| Frontend build | `next start` (Next.js 16.2.6) on port 3100     |

If you regenerate on a different macOS major / Chromium binary, expect
some subpixel drift. The 1% diff budget absorbs anti-aliasing jitter
but not a full Chrome major upgrade.

## What's locked

| File                                            | Page             | Mock state          |
| ----------------------------------------------- | ---------------- | ------------------- |
| `login-chromium-darwin.png`                     | `/login`         | empty form          |
| `dashboard-empty-chromium-darwin.png`           | `/`              | 0 leads             |
| `dashboard-populated-chromium-darwin.png`       | `/`              | 20 fixture leads    |
| `insights-chromium-darwin.png`                  | `/insights`      | 20 fixture leads + mocked /stats |
| `campaigns-chromium-darwin.png`                 | `/campaigns`     | 1 active campaign   |
| `lead-detail-modal-chromium-darwin.png`         | `/` + outreach modal | mocked draft for Fixture Co 19 |
| `ai-plan-card-chromium-darwin.png`              | `/` + AI chat    | mocked `DISCOVERY_SEARCH` plan |

All upstream HTTP traffic (`/api/proxy/leads`, `/api/proxy/stats`,
`/api/proxy/insights`, `/api/proxy/orchestrator/active`, etc.) is
intercepted via `page.route` in the spec, so the screenshots reflect
**only the frontend render** — not Supabase / Gemini / backend drift.

## Regenerating

Three pre-conditions:

1. `frontend/.env.local` has `NEXT_PUBLIC_SUPABASE_URL` +
   `NEXT_PUBLIC_SUPABASE_ANON_KEY` (the spec's `login()` helper hits
   real Supabase Auth).
2. A Supabase Auth user matching `E2E_EMAIL` exists.
3. Next.js prod build is up on the host the spec targets.

Then:

```bash
# From frontend/
npm ci
npm run build
PORT=3100 npm run start &

E2E_BASE_URL=http://localhost:3100 \
E2E_EMAIL=<supabase-test-user> \
E2E_PASSWORD=<password> \
npx playwright test e2e/visual.spec.ts --update-snapshots --project=chromium

# Eyeball the diff in the new PNGs, then:
git add e2e/visual.spec.ts-snapshots/
git commit -m "test(e2e): regenerate visual baselines (<why>)"
```

The `--update-snapshots` flag tells Playwright to overwrite the existing
PNGs instead of failing. PR review covers the visual diff in the
snapshot files themselves — GitHub renders the before/after image diff
inline.

## When to regenerate

- Intentional UI change (layout / colour / typography). Diff the PNGs
  in the PR to confirm the change is what you wanted.
- Tailwind / `globals.css` token tweak that visibly shifts the design.
- Dependency bump that affects rendering (Next major, Recharts major,
  Lucide icon glyphs, etc.).

**Do not** regenerate just to make CI green — visual regression is the
canary that something rendered differently. Investigate first.

## Path convention

Playwright defaults: `{specFile}-snapshots/{name}-{project}-{platform}{ext}`.
Don't introduce a custom `snapshotPathTemplate` in the config without
updating this README — the `__screenshots__/` directory the older docs
reference does not exist.
