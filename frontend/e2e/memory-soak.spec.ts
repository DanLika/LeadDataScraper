import { test, expect, type Page } from '@playwright/test'

// Long-session memory soak. Approximates a 4-hour operator session by
// looping 50 cycles of "open lead detail → close → change filter →
// audit → hunt". Heap is sampled before and after via Chromium CDP +
// JSHeapUsedSize from Performance.metrics. The contract:
//
//   - Some growth is fine — fixtures land in state, intervals tick,
//     React caches grow. Hard ceiling: 50 MB delta.
//   - Detached-DOM count must stay flat or near-flat across cycles.
//     A growing detached-node count signals forgotten event listeners,
//     setInterval that never clears, or modal subtrees React keeps alive.
//
// Chromium-only (CDP). Other browsers skip.
//
// Required env: E2E_BASE_URL, E2E_EMAIL, E2E_PASSWORD.

const EMAIL = process.env.E2E_EMAIL || ''
const PASSWORD = process.env.E2E_PASSWORD || ''
test.skip(!EMAIL || !PASSWORD, 'E2E_EMAIL and E2E_PASSWORD must be set')

const HEAP_GROWTH_CEILING_BYTES = 50 * 1024 * 1024
const DETACHED_GROWTH_CEILING = 500 // tolerate small leakage; runaway leaks land in the thousands
const CYCLES = 50

const FIXTURE_LEADS = Array.from({ length: 20 }, (_, i) => ({
  unique_key: `soak-${i.toString().padStart(2, '0')}`,
  name: `Soak Lead ${i}`,
  company_name: `Soak Co ${i}`,
  website: `https://example.com/soak/${i}`,
  email: `soak${i}@example.test`,
  phone: `+1-555-${(3000 + i).toString()}`,
  audit_status: i % 2 === 0 ? 'Completed' : 'Pending',
  seo_score: i % 2 === 0 ? 60 + i : null,
  outreach_score: i % 2 === 0 ? 50 + i : null,
  segment: ['Performance Optimization', 'Low Priority Prospect'][i % 2],
  high_risk_flag: false,
  retry_count: 0,
  lead_source: 'soak_fixture',
  created_at: `2026-05-01T00:00:${(i % 60).toString().padStart(2, '0')}Z`,
}))

async function login(page: Page) {
  await page.goto('/login')
  await page.fill('input[name="email"]', EMAIL)
  await page.fill('input[name="password"]', PASSWORD)
  await Promise.all([
    page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 15_000 }),
    page.click('button[type="submit"]'),
  ])
}

async function mockAll(page: Page) {
  // Lock the data plane so each cycle hits exactly the same payloads —
  // any real heap growth has to come from the frontend, not the backend.
  await page.route('**/api/proxy/leads**', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ leads: FIXTURE_LEADS, next_cursor: null, has_more: false }) }),
  )
  await page.route('**/api/proxy/insights**', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ insights: [], summary: '', top_priorities: [] }) }),
  )
  await page.route('**/api/proxy/orchestrator/active**', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ job: null }) }),
  )
  await page.route('**/api/proxy/audit-status**', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ active: false }) }),
  )
  await page.route('**/api/proxy/process-lead', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ job_id: 'soak-stub' }) }),
  )
  await page.route('**/api/proxy/hunt-lead', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ job_id: 'soak-stub' }) }),
  )
  await page.route('**/api/proxy/draft-outreach', (r) =>
    r.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ result: { draft: 'soak draft text', subject: 'soak', lead_name: 'Soak Co', lead_email: 'soak@example.test', operator_name: 'Op' } }),
    }),
  )
}

type HeapSample = { jsHeapUsedSize: number; nodes: number }

async function measure(page: Page): Promise<HeapSample> {
  const client = await page.context().newCDPSession(page)
  // Force GC first so we measure live retention, not garbage waiting to die.
  await client.send('HeapProfiler.enable').catch(() => undefined)
  await client.send('HeapProfiler.collectGarbage').catch(() => undefined)
  await page.waitForTimeout(150)
  const metrics = await client.send('Performance.getMetrics').catch(() => ({ metrics: [] as Array<{ name: string; value: number }> }))
  const find = (name: string) => metrics.metrics.find((m) => m.name === name)?.value ?? 0
  const sample: HeapSample = {
    jsHeapUsedSize: find('JSHeapUsedSize'),
    nodes: find('Nodes'),
  }
  await client.detach().catch(() => undefined)
  return sample
}

// Count detached DOM nodes — nodes that React has dropped but JS still
// references (forgotten ref/listener pattern). The CDP Performance.Nodes
// metric counts ALL nodes, attached or not; subtract document-tree count
// to estimate detached.
async function countDetached(page: Page): Promise<number> {
  const cdpNodes = await measure(page).then((s) => s.nodes)
  const liveNodes = await page.evaluate(() => document.getElementsByTagName('*').length)
  // CDP counts include #text nodes and shadow trees that aren't in
  // document.getElementsByTagName('*'). Take the delta as a coarse signal
  // — what we care about is whether THIS number grows monotonically.
  return Math.max(0, cdpNodes - liveNodes)
}

test('memory soak: 50 cycles, heap growth < 50 MB, no detached-DOM creep', async ({ page, browserName }) => {
  test.setTimeout(15 * 60_000)
  test.skip(browserName !== 'chromium', 'Performance.metrics + HeapProfiler require Chromium')

  await mockAll(page)
  await login(page)
  await page.goto('/')
  await page.waitForLoadState('networkidle')

  const before = await measure(page)
  const detachedBefore = await countDetached(page)

  // The cycle: open lead detail (Draft Email modal counts), close,
  // toggle a filter, click per-row audit, click per-row hunt. Mirrors
  // a real operator iterating through their inventory.
  for (let i = 0; i < CYCLES; i++) {
    const lead = page.locator('tbody tr.table-row-hover').first()
    await expect(lead).toBeVisible()

    // Open lead detail (outreach modal).
    const draftBtn = page.getByRole('button', { name: /Draft email outreach for/i }).first()
    await draftBtn.click()
    const dialog = page.getByRole('dialog', { name: /Outreach for/i })
    await expect(dialog).toBeVisible({ timeout: 5_000 })
    await page.keyboard.press('Escape')
    await expect(dialog).toBeHidden({ timeout: 3_000 })

    // Change filter — toggle audit_status to provoke memo recomputes.
    await page.selectOption('#filter-audit-status', i % 2 === 0 ? 'Completed' : 'all')

    // Per-row re-audit (process-lead) and hunt (hunt-lead) — both mocked.
    const auditBtn = page.getByRole('button', { name: /Re-audit|Process/i }).first()
    if (await auditBtn.isVisible().catch(() => false)) await auditBtn.click().catch(() => undefined)
    const huntBtn = page.getByRole('button', { name: /Deep digital hunt|Harvest contact/i }).first()
    if (await huntBtn.isVisible().catch(() => false)) await huntBtn.click().catch(() => undefined)

    // Brief breath between cycles. Mirrors operator cadence and gives
    // the polling intervals a tick to fire (so any leak from those
    // accumulates in the sample).
    await page.waitForTimeout(40)
  }

  const after = await measure(page)
  const detachedAfter = await countDetached(page)

  const heapDelta = after.jsHeapUsedSize - before.jsHeapUsedSize
  const detachedDelta = detachedAfter - detachedBefore

  // Print so a CI log shows the actual numbers — useful for tuning the
  // ceiling and for spotting near-misses before they turn into bugs.

  console.log(`[memory-soak] heap ${(before.jsHeapUsedSize / 1024 / 1024).toFixed(1)} MB → ${(after.jsHeapUsedSize / 1024 / 1024).toFixed(1)} MB (Δ ${(heapDelta / 1024 / 1024).toFixed(1)} MB) ; detached ${detachedBefore} → ${detachedAfter} (Δ ${detachedDelta})`)

  expect(
    heapDelta,
    `heap grew ${(heapDelta / 1024 / 1024).toFixed(1)} MB across ${CYCLES} cycles — exceeds ${HEAP_GROWTH_CEILING_BYTES / 1024 / 1024} MB ceiling. Look for forgotten setIntervals, unclosed CDP sessions, listeners that survive component unmount.`,
  ).toBeLessThan(HEAP_GROWTH_CEILING_BYTES)
  expect(
    detachedDelta,
    `detached-node count climbed ${detachedDelta} across ${CYCLES} cycles. Common culprits: refs holding closed-modal subtrees, event listeners on window not removed in cleanup.`,
  ).toBeLessThan(DETACHED_GROWTH_CEILING)
})
