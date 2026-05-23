import { defineConfig, devices } from '@playwright/test'

// E2E tests for the auth + proxy + cookie-floor invariants documented in
// CLAUDE.md. They exercise a running Next.js instance — start it yourself
// (`npm run dev` or `npm run build && npm run start`) and point E2E_BASE_URL
// at it. Required env:
//   E2E_BASE_URL   default http://localhost:3000
//   E2E_EMAIL      Supabase Auth user provisioned for the operator
//   E2E_PASSWORD   matching password
// Optional:
//   E2E_PROD_COOKIE_SECURE=1  assert Secure=true on session cookies (HTTPS prod)

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  timeout: 30_000,
  expect: { timeout: 5_000 },
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://localhost:3000',
    ignoreHTTPSErrors: true,
    actionTimeout: 10_000,
    navigationTimeout: 15_000,
    // CI debug payload. Costs nothing on green runs (kept only on failure)
    // and turns a flaky-CI investigation from "guess from logs" into
    // "scrub the trace timeline". Pair with the e2e:trace npm script
    // to open the last failed trace locally.
    trace: 'retain-on-failure',
    video: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  // full-flow.spec.ts drives the real Google-Maps scrape + Gemini calls +
  // Supabase mutations (see file header). Tripling its cost across browsers
  // buys nothing — it tests pipeline state, not browser behaviour. Restrict
  // it to chromium and let auth + security-headers exercise the cross-browser
  // surface where it actually matters (Safari/WebKit cookie quirks on
  // localhost, Firefox tracking-protection blocking the same-origin proxy).
  // mobile.spec.ts is the inverse — viewport regressions are the *only*
  // thing it tests, so it runs ONLY on the iPhone 14 + Pixel 7 projects
  // (testMatch). Desktop projects testIgnore it.
  // visual.spec.ts is chromium-only: baselines are pixel-locked to a
  // Playwright Docker image (matches CI ubuntu-latest), so cross-browser
  // glyph drift would just churn noise. Auth + security-headers + a11y
  // already exercise the Firefox/WebKit rendering surface.
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
      testIgnore: /mobile\.spec\.ts$/,
    },
    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'] },
      testIgnore: /(full-flow|mobile|visual)\.spec\.ts$/,
    },
    {
      name: 'webkit',
      use: { ...devices['Desktop Safari'] },
      testIgnore: /(full-flow|mobile|visual)\.spec\.ts$/,
    },
    {
      name: 'iphone-14',
      use: { ...devices['iPhone 14'] },
      testMatch: /mobile\.spec\.ts$/,
    },
    {
      name: 'pixel-7',
      use: { ...devices['Pixel 7'] },
      testMatch: /mobile\.spec\.ts$/,
    },
  ],
})
