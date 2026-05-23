import { test, expect, type BrowserContext, type Page } from '@playwright/test'
import { createClient, type SupabaseClient } from '@supabase/supabase-js'

// Multi-tab resilience. Operators routinely run 2-3 tabs on the same
// dashboard; the contract this spec pins:
//
//   1. Two pages in one BrowserContext share the Supabase session cookies.
//   2. Tab A starts an orchestration job → Tab B's /orchestrator/active
//      poll (5s cadence in app/page.tsx) adopts that job and shows the
//      running indicator within a couple of poll intervals.
//   3. Sign out in Tab A → Tab B's next proxied call returns 401 → the
//      apiFetch 401 redirect bounces it to /login?next=… (added in the
//      network-resilience turn).
//   4. Tab A logs in again → Tab B does NOT auto-recover. The cookies are
//      valid again in the shared context, but nothing on the /login page
//      reacts to that — the operator must reload.
//   5. Two tabs trigger /process-all simultaneously → the orchestrator's
//      _job_lock + "resume existing running job" branch keeps the DB
//      sane. No second running job is created; no 5xx.
//
// "Two contexts, same session cookie" is implemented as two Pages in the
// same BrowserContext, which is closer to how real browsers handle tabs
// (cookies are jar-level, not tab-level). The cross-context-via-
// storageState variant is also exercised in the auth.spec.ts replay test.
//
// Required env: E2E_BASE_URL, E2E_EMAIL, E2E_PASSWORD, E2E_SUPABASE_URL,
//               E2E_SUPABASE_SERVICE_ROLE_KEY.

const EMAIL = process.env.E2E_EMAIL || ''
const PASSWORD = process.env.E2E_PASSWORD || ''
const SUPABASE_URL = process.env.E2E_SUPABASE_URL || ''
const SUPABASE_SERVICE_ROLE_KEY = process.env.E2E_SUPABASE_SERVICE_ROLE_KEY || ''

const MISSING = (
  [
    ['E2E_EMAIL', EMAIL],
    ['E2E_PASSWORD', PASSWORD],
    ['E2E_SUPABASE_URL', SUPABASE_URL],
    ['E2E_SUPABASE_SERVICE_ROLE_KEY', SUPABASE_SERVICE_ROLE_KEY],
  ] as const
)
  .filter(([, v]) => !v)
  .map(([k]) => k)

test.skip(MISSING.length > 0, `Missing env: ${MISSING.join(', ')}`)
test.describe.configure({ mode: 'serial' })

function admin(): SupabaseClient {
  return createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, {
    auth: { persistSession: false, autoRefreshToken: false },
  })
}

async function login(page: Page) {
  await page.goto('/login')
  await page.fill('input[name="email"]', EMAIL)
  await page.fill('input[name="password"]', PASSWORD)
  await Promise.all([
    page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 15_000 }),
    page.click('button[type="submit"]'),
  ])
}

async function openSecondTab(ctx: BrowserContext): Promise<Page> {
  // Same context = same cookie jar = same Supabase session. This is the
  // accurate simulation of "operator opens second tab".
  const tabB = await ctx.newPage()
  await tabB.goto('/')
  await tabB.waitForLoadState('networkidle')
  return tabB
}

// Stub-fulfill the orchestration_jobs DB row so the test doesn't drive a
// real audit pipeline. The /orchestrator/start handler kicks off
// asyncio.create_task(_process_in_chunks) which would try to audit real
// leads. We just need the DB row to exist with status=running for Tab B
// to detect it.
async function insertFakeRunningJob(db: SupabaseClient, jobId: string): Promise<void> {
  await db.from('orchestration_jobs').upsert({
    id: jobId,
    status: 'running',
    total_count: 100,
    processed_count: 0,
    current_phase: 'multi-tab-e2e-fixture',
  }).select()
}

async function deleteJob(db: SupabaseClient, jobId: string): Promise<void> {
  await db.from('orchestration_jobs').delete().eq('id', jobId)
}

test('multi-tab pipeline: cross-tab job visibility, signout, recovery, concurrent /process-all', async ({ browser }) => {
  test.setTimeout(2 * 60_000)
  const db = admin()
  const ctx = await browser.newContext()
  const tabA = await ctx.newPage()

  // ---- setup: Tab A logs in, opens dashboard ----
  await login(tabA)
  await tabA.goto('/')
  await tabA.waitForLoadState('networkidle')

  // ---- 1+2) Tab B opens (same cookies); Tab A "starts" a job, Tab B sees it ----
  const tabB = await openSecondTab(ctx)

  // Use a recognisable fixture id so cleanup is precise.
  const crypto = await import('node:crypto')
  const fakeJobId = crypto.randomUUID()
  await insertFakeRunningJob(db, fakeJobId)

  try {
    // Tab B polls /orchestrator/active every 5s. Allow ~15s for it to land,
    // adopt the job into orchestratorJob state, and render the running
    // indicator. The exact selector: the "AI Orchestrate" button shows a
    // spinner (Loader2.animate-spin) when orchestratorJob is in the
    // running/starting state.
    const tabBRunning = tabB.getByRole('button', { name: /AI Orchestrate/i })
    await expect(tabBRunning).toBeDisabled({ timeout: 20_000 })
  } finally {
    // Even if assertion failed, scrub the fixture row.
    await deleteJob(db, fakeJobId)
  }

  // ---- 3) Sign out in Tab A → Tab B's next API call returns 401, redirects /login ----
  // /api/auth/signout invalidates the Supabase session server-side. Tab B's
  // cookies still exist but auth.getUser() on the proxy fails, so /api/proxy/*
  // returns 401 → apiFetch redirects.
  const signoutResp = await tabA.evaluate(async () => {
    const r = await fetch('/api/auth/signout', { method: 'POST', credentials: 'include' })
    return r.status
  })
  expect(signoutResp).toBe(200)

  // Force a fetch in Tab B that we know hits the proxy.
  await tabB.evaluate(async () => {
    try {
      await fetch('/api/proxy/leads?limit=1', { method: 'GET', credentials: 'include' })
    } catch {
      /* expected — apiFetch.ts throws after redirecting */
    }
  })
  // The 401-redirect path lives inside apiFetch (utils/apiConfig.ts). The
  // raw fetch above doesn't go through apiFetch, so it WON'T redirect on
  // its own — that codepath only triggers when the dashboard's
  // useEffect-driven polls call apiFetch. Force one by triggering the
  // 15s leads poll early via reload, then watch for the bounce.
  await tabB.reload()
  await tabB.waitForURL(/\/login(\?|$)/, { timeout: 15_000 })
  expect(tabB.url()).toMatch(/\/login\?next=/)

  // ---- 4) Tab A logs in again → Tab B does NOT auto-recover ----
  await login(tabA)
  expect(tabA.url()).not.toMatch(/\/login/)

  // Cookies are valid in the shared context now. Tab B is parked on /login;
  // nothing on the login page reacts to the cookie change, so the URL
  // should still be /login after a grace period.
  await tabB.waitForTimeout(3_000)
  expect(tabB.url(), 'Tab B must not auto-navigate off /login after Tab A re-auths').toMatch(/\/login(\?|$)/)

  // Manual reload should recover.
  await tabB.reload()
  await tabB.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 15_000 })

  // ---- 5) Concurrent /process-all from both tabs ----
  // Fire two POSTs in parallel; both should resolve 2xx. The orchestrator's
  // _job_lock + the "resume existing running job" branch in
  // run_massive_pipeline must keep the DB sane — at most one running job,
  // no 5xx, no PK collision.
  const fireProcessAll = (page: Page) =>
    page.evaluate(async () => {
      const r = await fetch('/api/proxy/process-all', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
      })
      return { status: r.status, body: await r.text().catch(() => '') }
    })

  const [resA, resB] = await Promise.all([fireProcessAll(tabA), fireProcessAll(tabB)])
  expect(resA.status, `Tab A /process-all returned ${resA.status}: ${resA.body}`).toBeLessThan(500)
  expect(resB.status, `Tab B /process-all returned ${resB.status}: ${resB.body}`).toBeLessThan(500)
  expect([200, 202, 409, 429]).toContain(resA.status)
  expect([200, 202, 409, 429]).toContain(resB.status)

  // DB invariant: at most ONE orchestration_jobs row in 'running'/'starting'
  // state for the user. Two simultaneous starts must not have spawned two
  // parallel pipelines.
  const { data: runningJobs, error: rjErr } = await db
    .from('orchestration_jobs')
    .select('id, status')
    .in('status', ['running', 'starting'])
  if (rjErr) throw rjErr
  expect(
    (runningJobs || []).length,
    `concurrent /process-all spawned ${(runningJobs || []).length} running jobs (expected ≤ 1)`,
  ).toBeLessThanOrEqual(1)

  // Cleanup: stop whatever job(s) we started so we don't leave a Playwright
  // run audit eating real network for an hour.
  for (const j of runningJobs || []) {
    await tabA.evaluate(async (id) => {
      await fetch(`/api/proxy/orchestrator/stop/${id}`, { method: 'POST', credentials: 'include' })
    }, j.id)
  }

  await ctx.close()
})
