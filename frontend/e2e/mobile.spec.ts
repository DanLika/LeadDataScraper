import { test, expect, type Page } from '@playwright/test'

// Mobile-viewport smoke. Runs on the `iphone-14` and `pixel-7` projects in
// playwright.config.ts (Desktop projects testIgnore this file). Verifies
// the responsive layout doesn't regress on:
//   - horizontal overflow
//   - off-screen CTAs
//   - sidebar collapse to hamburger
//   - modal fit + scrollability
//   - iOS-Safari input-font zoom (< 16px triggers focus-zoom crop)
//
// Required env: E2E_BASE_URL, E2E_EMAIL, E2E_PASSWORD.

const EMAIL = process.env.E2E_EMAIL || ''
const PASSWORD = process.env.E2E_PASSWORD || ''
test.skip(!EMAIL || !PASSWORD, 'E2E_EMAIL and E2E_PASSWORD must be set')

const AUTHED_ROUTES = ['/', '/insights', '/campaigns'] as const
const PUBLIC_ROUTES = ['/login'] as const

const HORIZONTAL_OVERFLOW_TOLERANCE_PX = 1
const MIN_INPUT_FONT_PX = 16

async function login(page: Page) {
  await page.goto('/login')
  await page.fill('input[name="email"]', EMAIL)
  await page.fill('input[name="password"]', PASSWORD)
  await Promise.all([
    page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 15_000 }),
    page.click('button[type="submit"]'),
  ])
}

async function assertNoHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
    bodyScrollWidth: document.body.scrollWidth,
  }))
  const horiz = overflow.scrollWidth - overflow.clientWidth
  expect(
    horiz,
    `horizontal overflow ${horiz}px (scrollWidth=${overflow.scrollWidth} clientWidth=${overflow.clientWidth})`,
  ).toBeLessThanOrEqual(HORIZONTAL_OVERFLOW_TOLERANCE_PX)
}

async function assertCtasInViewport(page: Page) {
  // Pull every visible <button> and every primary <a> with role=link.
  // Ignore zero-sized elements (collapsed in a closed drawer etc) and
  // elements positioned off-screen by design (sidebar @ translateX(-100%)).
  const offenders = await page.evaluate(() => {
    const out: { tag: string; label: string; rect: { x: number; y: number; w: number; h: number }; vw: number }[] = []
    const vw = window.innerWidth
    const nodes = Array.from(document.querySelectorAll('button, a[href]')) as HTMLElement[]
    for (const el of nodes) {
      if (el.offsetParent === null) continue // hidden via display:none / disconnected
      const rect = el.getBoundingClientRect()
      if (rect.width === 0 || rect.height === 0) continue
      const style = window.getComputedStyle(el)
      if (style.visibility === 'hidden' || style.display === 'none') continue
      // Off-screen drawer items: any element with x + width <= 0 (parked left).
      // We consider those "intentionally hidden" and skip.
      if (rect.right <= 0) continue
      // Off-screen-right is a fail — that's a real CTA the user can't reach.
      if (rect.left > vw + 1 || rect.right > vw + 1) {
        out.push({
          tag: el.tagName,
          label: (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 60),
          rect: { x: rect.x, y: rect.y, w: rect.width, h: rect.height },
          vw,
        })
      }
    }
    return out
  })
  expect(
    offenders,
    `CTAs extending past right viewport edge:\n${offenders
      .map((o) => `  <${o.tag}> "${o.label}" rect=${JSON.stringify(o.rect)} vw=${o.vw}`)
      .join('\n')}`,
  ).toEqual([])
}

async function assertSidebarCollapsedToHamburger(page: Page) {
  // On routes that own the sidebar (/, /insights, /campaigns), at mobile
  // widths the persistent sidebar must NOT take horizontal space.
  // Either the hamburger trigger is visible AND the sidebar is off-screen,
  // OR the sidebar element isn't rendered at all.
  const hamburger = page.getByRole('button', { name: 'Open menu' })
  await expect(hamburger, 'hamburger button must be visible on mobile').toBeVisible()

  // Sidebar should not extend rightwards into the content area at rest.
  const sidebarRight = await page.evaluate(() => {
    const sidebar = document.querySelector(
      'aside, nav[aria-label="Primary"], [class*="sidebar"]',
    ) as HTMLElement | null
    if (!sidebar) return null
    const r = sidebar.getBoundingClientRect()
    return { right: r.right, width: r.width, vw: window.innerWidth }
  })
  if (sidebarRight) {
    // At rest the sidebar must be parked off-screen OR not occupy meaningful
    // width (close to 0). A 256px sidebar visible at x=0 on a 390px viewport
    // would steal 2/3 of the content area — that's the regression.
    const intrudes = sidebarRight.right > 0 && sidebarRight.width > sidebarRight.vw / 3
    expect(
      intrudes,
      `sidebar intrudes on mobile content (right=${sidebarRight.right} width=${sidebarRight.width} vw=${sidebarRight.vw})`,
    ).toBe(false)
  }
}

async function assertModalFitsAndScrolls(page: Page) {
  // Use the Settings modal — opens via query param per CLAUDE.md's
  // cross-page-nav contract. Safe: no destructive side effects on open.
  await page.goto('/?openSettings=1')
  const dialog = page.getByRole('dialog').first()
  await expect(dialog).toBeVisible({ timeout: 5_000 })

  const sizing = await dialog.evaluate((el) => {
    const card = (el.querySelector('.card') as HTMLElement) || el
    const r = card.getBoundingClientRect()
    const style = window.getComputedStyle(card)
    return {
      cardW: r.width,
      cardH: r.height,
      vw: window.innerWidth,
      vh: window.innerHeight,
      overflowY: style.overflowY,
      overflow: style.overflow,
    }
  })

  // Fit: modal card width must not exceed the viewport.
  expect(
    sizing.cardW,
    `modal card width ${sizing.cardW} > viewport ${sizing.vw}`,
  ).toBeLessThanOrEqual(sizing.vw + 1)

  // Scrollable inside: either the card itself or its overflow-y allows
  // scrolling, OR the card already fits the viewport height.
  const scrollable = ['auto', 'scroll'].includes(sizing.overflowY) || ['auto', 'scroll'].includes(sizing.overflow)
  const fits = sizing.cardH <= sizing.vh + 1
  expect(
    scrollable || fits,
    `modal must scroll inside or fit vertically (h=${sizing.cardH} vh=${sizing.vh} overflowY=${sizing.overflowY})`,
  ).toBe(true)

  // Close so it doesn't bleed into the next nav.
  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden({ timeout: 3_000 })
}

async function assertFormInputsUsable(page: Page) {
  // iOS Safari auto-zooms (and crops) form inputs whose computed font-size
  // is < 16px. Fail the test if any input on /login violates the rule.
  await page.goto('/login')
  const violations = await page.evaluate((minPx) => {
    const inputs = Array.from(document.querySelectorAll('input, select, textarea')) as HTMLElement[]
    const bad: { name: string; type: string; fontPx: number }[] = []
    for (const el of inputs) {
      if (el.offsetParent === null) continue
      const fp = parseFloat(window.getComputedStyle(el).fontSize || '0')
      if (fp < minPx) {
        const i = el as HTMLInputElement
        bad.push({ name: i.name || i.id || '(unnamed)', type: i.type || el.tagName, fontPx: fp })
      }
    }
    return bad
  }, MIN_INPUT_FONT_PX)
  expect(
    violations,
    `form inputs below ${MIN_INPUT_FONT_PX}px (iOS will zoom-crop on focus):\n${violations
      .map((v) => `  ${v.type} name="${v.name}" font-size=${v.fontPx}px`)
      .join('\n')}`,
  ).toEqual([])
}

test.describe('mobile smoke — public routes', () => {
  for (const route of PUBLIC_ROUTES) {
    test(`${route} — no overflow, CTAs in viewport, inputs ≥16px`, async ({ page }) => {
      await page.goto(route)
      await page.waitForLoadState('networkidle')
      await assertNoHorizontalScroll(page)
      await assertCtasInViewport(page)
      if (route === '/login') await assertFormInputsUsable(page)
    })
  }
})

test.describe('mobile smoke — authed routes', () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
  })

  for (const route of AUTHED_ROUTES) {
    test(`${route} — no overflow, CTAs in viewport, sidebar → hamburger`, async ({ page }) => {
      await page.goto(route)
      await page.waitForLoadState('networkidle')
      await assertNoHorizontalScroll(page)
      await assertCtasInViewport(page)
      await assertSidebarCollapsedToHamburger(page)
    })
  }

  test('modal fits viewport and scrolls inside', async ({ page }) => {
    // Use the dashboard's Settings modal — covered by the cross-page nav
    // contract so it opens deterministically from a query param.
    await page.goto('/')
    await assertModalFitsAndScrolls(page)
  })
})
