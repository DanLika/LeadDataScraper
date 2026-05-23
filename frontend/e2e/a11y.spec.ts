import { test, expect, type Page } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// Accessibility spec.
//
// Two halves:
//   1. axe-core scan on every authed + public route. Fails on any
//      `impact ∈ {critical, serious}` violation that isn't on the allowlist.
//   2. Keyboard-only nav: Tab discovers every interactive element on the
//      dashboard, Esc closes modals, Enter submits the login form, and
//      focus is visually distinguishable (no naked `:focus { outline: none }`).
//
// Allowlist: frontend/axe-allowlist.json. Each entry must carry an
// `expires` date so a stale exception trips its own clock.

const EMAIL = process.env.E2E_EMAIL || ''
const PASSWORD = process.env.E2E_PASSWORD || ''
test.skip(!EMAIL || !PASSWORD, 'E2E_EMAIL and E2E_PASSWORD must be set')

type AllowlistEntry = {
  ruleId: string
  route?: string
  selector?: string
  reason: string
  expires: string
}
type Allowlist = { entries: AllowlistEntry[] }

const ALLOWLIST: Allowlist = JSON.parse(
  readFileSync(join(__dirname, '..', 'axe-allowlist.json'), 'utf8'),
)

const AUTHED_ROUTES = ['/', '/insights', '/campaigns'] as const
const PUBLIC_ROUTES = ['/login'] as const

async function login(page: Page) {
  await page.goto('/login')
  await page.fill('input[name="email"]', EMAIL)
  await page.fill('input[name="password"]', PASSWORD)
  await Promise.all([
    page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 15_000 }),
    page.click('button[type="submit"]'),
  ])
}

function allowlistMatches(entry: AllowlistEntry, route: string, ruleId: string, target: string): boolean {
  if (entry.ruleId !== ruleId) return false
  if (entry.route && entry.route !== route) return false
  if (entry.selector && !target.includes(entry.selector)) return false
  // Expiry: a stale waiver shouldn't keep hiding the violation.
  const exp = Date.parse(entry.expires)
  if (Number.isNaN(exp) || exp < Date.now()) return false
  return true
}

async function scanRoute(page: Page, route: string): Promise<void> {
  await page.waitForLoadState('networkidle')
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze()

  // Only fail on impact ∈ {critical, serious}. Moderate/minor surface in
  // logs but don't block — they're judgement calls more than bugs.
  const blocking = results.violations.filter(
    (v) => v.impact === 'critical' || v.impact === 'serious',
  )

  const remaining: string[] = []
  for (const v of blocking) {
    for (const node of v.nodes) {
      const target = node.target.join(' ')
      const waived = ALLOWLIST.entries.some((e) =>
        allowlistMatches(e, route, v.id, target),
      )
      if (!waived) {
        remaining.push(
          `[${v.impact}] ${v.id} @ ${target}\n    ${v.help}\n    ${v.helpUrl}`,
        )
      }
    }
  }

  expect(
    remaining,
    `accessibility violations on ${route} (${remaining.length}):\n${remaining.map((s) => '  ' + s).join('\n')}`,
  ).toEqual([])
}

test.describe('axe-core scan', () => {
  for (const route of PUBLIC_ROUTES) {
    test(`${route} (public)`, async ({ page }) => {
      await page.goto(route)
      await scanRoute(page, route)
    })
  }
  for (const route of AUTHED_ROUTES) {
    test(`${route} (authed)`, async ({ page }) => {
      await login(page)
      await page.goto(route)
      await scanRoute(page, route)
    })
  }
})

test.describe('keyboard-only nav', () => {
  test('Tab reaches every interactive element on the dashboard', async ({ page, browserName }) => {
    // Webkit reports document.activeElement differently after some Tab
    // presses; the contract is identical but the assertion below uses
    // outerHTML matching so both shapes work.
    await login(page)
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    // Move focus to <body> so Tab starts from the top.
    await page.evaluate(() => {
      ;(document.activeElement as HTMLElement | null)?.blur()
      ;(document.body as HTMLElement).focus()
    })

    // Snapshot every visible, enabled interactive element on the page. These
    // are the candidates Tab must be able to reach.
    const targets = await page.evaluate(() => {
      const visible = (el: Element) => {
        const e = el as HTMLElement
        if (e.offsetParent === null) return false
        const cs = window.getComputedStyle(e)
        if (cs.visibility === 'hidden' || cs.display === 'none') return false
        const r = e.getBoundingClientRect()
        return r.width > 0 && r.height > 0
      }
      const isInteractive = (el: Element) => {
        const e = el as HTMLElement
        if (e.hasAttribute('disabled')) return false
        if (e.getAttribute('aria-hidden') === 'true') return false
        const ti = e.getAttribute('tabindex')
        if (ti !== null && Number(ti) < 0) return false
        return true
      }
      const sel = 'a[href], button, input:not([type="hidden"]), select, textarea, [tabindex]:not([tabindex="-1"])'
      const nodes = Array.from(document.querySelectorAll(sel)) as HTMLElement[]
      return nodes
        .filter(visible)
        .filter(isInteractive)
        .map((el, i) => {
          const label =
            el.getAttribute('aria-label') ||
            (el as HTMLInputElement).name ||
            el.textContent?.trim().slice(0, 40) ||
            el.tagName
          return { i, tag: el.tagName, label, outerHTML: el.outerHTML.slice(0, 120) }
        })
    })
    expect(targets.length, 'dashboard must have at least one tabbable element').toBeGreaterThan(0)

    // Tab up to `targets.length + 5` times (slack for skip-link, browser
    // chrome focus stops). Collect the activeElement on each press; we
    // require every target's outerHTML to appear at least once.
    const maxTabs = targets.length + 5
    const visited = new Set<string>()
    for (let i = 0; i < maxTabs; i++) {
      await page.keyboard.press('Tab')
      const html = await page.evaluate(() => {
        const a = document.activeElement as HTMLElement | null
        return a ? a.outerHTML.slice(0, 120) : ''
      })
      if (html) visited.add(html)
    }

    const unreached = targets.filter((t) => !visited.has(t.outerHTML))
    expect(
      unreached,
      `${browserName}: tab order must reach every interactive element. Unreached:\n${unreached.map((u) => `  [${u.tag}] ${u.label}`).join('\n')}`,
    ).toEqual([])
  })

  test('Esc closes modals', async ({ page }) => {
    await login(page)
    // Settings modal is the cleanest test target — no side effects when opened.
    await page.goto('/?openSettings=1')
    const dialog = page.getByRole('dialog').first()
    await expect(dialog).toBeVisible({ timeout: 5_000 })
    await page.keyboard.press('Escape')
    await expect(dialog).toBeHidden({ timeout: 3_000 })
  })

  test('Enter submits the login form', async ({ page }) => {
    // Use a deliberately-wrong password so the test doesn't burn a real
    // session and so the in-process loginThrottle bucket counts this as
    // one of its allowed attempts (not the throttled 6th).
    await page.goto('/login')
    await page.fill('input[name="email"]', EMAIL)
    await page.fill('input[name="password"]', 'wrong-password-' + Math.random().toString(36).slice(2))
    await page.focus('input[name="password"]')

    // Hitting Enter must submit the form — the Server Action runs, then the
    // page re-renders with an error alert. If Enter were swallowed, the
    // alert would never appear.
    await page.keyboard.press('Enter')
    await expect(page.locator('[role="alert"]')).toBeVisible({ timeout: 8_000 })
    // Still on /login (auth failed).
    expect(page.url()).toMatch(/\/login(\?|$)/)
  })

  test('focus is visually distinguishable (no naked outline:none)', async ({ page }) => {
    await login(page)
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    // Tab to the first interactive control and check its focus styling.
    // A focused element MUST surface SOMETHING the keyboard user can see:
    // a non-`none` outline, a non-zero outline-width, or a box-shadow.
    // Naked `:focus { outline: none }` without a replacement is the bug.
    await page.evaluate(() => (document.body as HTMLElement).focus())
    await page.keyboard.press('Tab')

    const focusStyle = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null
      if (!el || el === document.body) return null
      const cs = window.getComputedStyle(el)
      // Some apps use `box-shadow` ring instead of outline. Both are
      // valid focus indicators.
      return {
        tag: el.tagName,
        outerHTML: el.outerHTML.slice(0, 120),
        outlineStyle: cs.outlineStyle,
        outlineWidth: cs.outlineWidth,
        outlineColor: cs.outlineColor,
        boxShadow: cs.boxShadow,
        borderColor: cs.borderColor,
      }
    })
    expect(focusStyle, 'something must be focused after Tab').not.toBeNull()

    const hasOutline =
      focusStyle!.outlineStyle && focusStyle!.outlineStyle !== 'none' &&
      parseFloat(focusStyle!.outlineWidth || '0') > 0
    const hasShadow = focusStyle!.boxShadow && focusStyle!.boxShadow !== 'none'
    expect(
      hasOutline || hasShadow,
      `focused <${focusStyle!.tag}> has no visible focus indicator (outline=${focusStyle!.outlineStyle} ${focusStyle!.outlineWidth}, box-shadow=${focusStyle!.boxShadow}). HTML: ${focusStyle!.outerHTML}`,
    ).toBe(true)
  })
})
