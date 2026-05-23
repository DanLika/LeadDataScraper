import { test, expect, type Page } from '@playwright/test'

// Modal behaviour contract: click-outside, Esc, in-flight submit, form
// state across reopen, focus trap, focus return.
//
// Not tested here: "open modal B from inside modal A". The only modal
// triggers in the dashboard live in the Sidebar (z-index 100), and the
// modal backdrop sits at z-index 500 — sidebar buttons are visually +
// hit-test obscured while any modal is open. Cross-modal navigation
// from inside a modal isn't reachable in current product UI.
// `test.skip` block at the bottom documents that gap.
//
// Required env: E2E_BASE_URL, E2E_EMAIL, E2E_PASSWORD.

const EMAIL = process.env.E2E_EMAIL || ''
const PASSWORD = process.env.E2E_PASSWORD || ''
test.skip(!EMAIL || !PASSWORD, 'E2E_EMAIL and E2E_PASSWORD must be set')

async function login(page: Page) {
  await page.goto('/login')
  await page.fill('input[name="email"]', EMAIL)
  await page.fill('input[name="password"]', PASSWORD)
  await Promise.all([
    page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 15_000 }),
    page.click('button[type="submit"]'),
  ])
}

async function openSettings(page: Page) {
  // Settings opens via query param per the cross-page nav contract in CLAUDE.md.
  await page.goto('/?openSettings=1')
  await expect(page.getByRole('dialog', { name: /settings/i })).toBeVisible({ timeout: 5_000 })
}

async function openDiscovery(page: Page) {
  await page.goto('/?openDiscovery=1')
  await expect(page.getByRole('dialog', { name: /Lead Discovery/i })).toBeVisible({ timeout: 5_000 })
}

test.describe('modal behaviour', () => {
  test('click outside the modal card closes the modal', async ({ page }) => {
    await login(page)
    await openSettings(page)
    const dialog = page.getByRole('dialog', { name: /settings/i })

    // The backdrop fills the viewport but only closes when the click hits
    // the backdrop itself (e.target === e.currentTarget). Clicking near a
    // corner of the dialog backdrop region triggers the close path.
    const box = await dialog.boundingBox()
    expect(box, 'must have a dialog bbox').not.toBeNull()
    // Click just outside the card — top-left of viewport is reliably outside
    // any centred modal card.
    await page.mouse.click(8, 8)
    await expect(dialog).toBeHidden({ timeout: 3_000 })
  })

  test('Escape closes the modal', async ({ page }) => {
    await login(page)
    await openSettings(page)
    const dialog = page.getByRole('dialog', { name: /settings/i })
    await page.keyboard.press('Escape')
    await expect(dialog).toBeHidden({ timeout: 3_000 })
  })

  test('submit while open: modal stays open until the response lands', async ({ page }) => {
    // Discovery's "Start Deep Search" closes the modal AFTER the
    // /discovery/start response. Stall the POST so the in-flight window
    // is observable.
    let stallResolved = false
    await page.route('**/api/proxy/discovery/start', async (route) => {
      await new Promise((r) => setTimeout(r, 2_500))
      stallResolved = true
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ job_id: 'modal-e2e-stub', status: 'starting' }),
      })
    })

    await login(page)
    await openDiscovery(page)
    const dialog = page.getByRole('dialog', { name: /Lead Discovery/i })
    await page.fill('#discovery-query', 'dentists')
    await page.fill('#discovery-location', 'Mostar')
    await page.getByRole('button', { name: /Start Deep Search/i }).click()

    // Mid-flight (within the 2.5s stall): modal must still be visible
    // and the submit button must show its busy state.
    await page.waitForTimeout(400)
    expect(stallResolved, 'route should still be stalled').toBe(false)
    await expect(dialog, 'modal must remain visible during in-flight submit').toBeVisible()
    const submit = page.getByRole('button', { name: /Mining|Start Deep Search/i })
    await expect(submit).toHaveAttribute('aria-busy', 'true')

    // After response: handleStartDiscovery calls setShowDiscoveryModal(false).
    await page.waitForFunction(() => stallResolved === true).catch(() => undefined)
    void stallResolved
    // Modal auto-closes on success.
    await expect(dialog).toBeHidden({ timeout: 8_000 })

    await page.unroute('**/api/proxy/discovery/start')
  })

  test('form state is preserved across close + reopen (current behaviour)', async ({ page }) => {
    // Document the contract: closing the Discovery modal without submitting
    // leaves discoveryQuery / discoveryLocation in component state, so
    // reopening shows the same values. If you ever decide accidental-close
    // should reset, this test will tell you it changed.
    await login(page)
    await openDiscovery(page)
    await page.fill('#discovery-query', 'pizza places')
    await page.fill('#discovery-location', 'Sarajevo')
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog', { name: /Lead Discovery/i })).toBeHidden()

    // Reopen — state should be intact.
    await page.goto('/?openDiscovery=1')
    await expect(page.getByRole('dialog', { name: /Lead Discovery/i })).toBeVisible()
    await expect(page.locator('#discovery-query')).toHaveValue('pizza places')
    await expect(page.locator('#discovery-location')).toHaveValue('Sarajevo')
  })

  test('focus trap: Tab inside the modal does not escape to background', async ({ page }) => {
    await login(page)
    await openSettings(page)
    const dialog = page.getByRole('dialog', { name: /settings/i })

    // Walk Tab around the focus cycle a few extra times; if the trap were
    // broken, focus would leak to a body-level element (e.g. the page's
    // search input #search-leads or any header button).
    const focusableSelector =
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled])'
    const insideCount = await dialog.locator(focusableSelector).count()
    expect(insideCount, 'modal must have focusable elements to cycle').toBeGreaterThan(0)

    const tabs = insideCount * 2 + 3
    for (let i = 0; i < tabs; i++) {
      await page.keyboard.press('Tab')
      const inside = await page.evaluate((sel) => {
        const a = document.activeElement
        return !!a && !!a.closest(sel)
      }, '[role="dialog"]')
      expect(inside, `focus escaped on tab ${i + 1}`).toBe(true)
    }
  })

  test('focus returns to triggering control on close', async ({ page }) => {
    await login(page)
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    // Use the Sidebar Settings button as a real-world trigger. It's a
    // <button>, focused before the click, restored on close by
    // useFocusTrap's opener-capture branch.
    const trigger = page
      .getByRole('button', { name: /settings/i })
      .first()
    await trigger.focus()
    // Sanity-check the trigger is what's focused.
    const triggerHtmlBefore = await trigger.evaluate((el) => el.outerHTML.slice(0, 120))
    const activeBefore = await page.evaluate(() => (document.activeElement as HTMLElement | null)?.outerHTML.slice(0, 120) || '')
    expect(activeBefore).toBe(triggerHtmlBefore)

    await trigger.click()
    await expect(page.getByRole('dialog', { name: /settings/i })).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog', { name: /settings/i })).toBeHidden()

    // After close, the previously-focused element must be focused again.
    const activeAfter = await page.evaluate(() => (document.activeElement as HTMLElement | null)?.outerHTML.slice(0, 120) || '')
    expect(activeAfter, 'focus must return to the trigger after the modal closes').toBe(triggerHtmlBefore)
  })

  test.skip('open modal A, click "open modal B" inside it → A closes cleanly', () => {
    // No in-modal cross-modal trigger exists in the current dashboard.
    // The only modal openers are in the Sidebar (z-index 100), which is
    // visually + hit-test obscured by any open modal (z-index 500).
    // Leaving this case as a documented gap — if you add such a button
    // later (e.g. "Open Settings" inside the Discovery modal), unskip
    // and assert that A closes within one render tick of B opening.
  })
})
