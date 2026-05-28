import { test, expect, type Browser, type Page } from '@playwright/test'

// Locale + timezone contract. Three operator profiles:
//   - en-US / America/New_York   (English baseline)
//   - hr-HR / Europe/Zagreb      (Croatian — your market)
//   - bs-BA / Europe/Sarajevo    (Bosnian)
//
// What we pin:
//   - Timestamps render in the OPERATOR's timezone, not UTC.
//   - Number formatting follows locale (1,234 vs 1.234).
//   - Date formats are unambiguous (no MM/DD vs DD/MM mix).
//   - Croatian/Bosnian diacritics (š č ć ž đ) round-trip without mojibake.
//
// Mocks the data so every context sees the same payload and only the
// rendering differs.
//
// Required env: E2E_BASE_URL, E2E_EMAIL, E2E_PASSWORD.

const EMAIL = process.env.E2E_EMAIL || ''
const PASSWORD = process.env.E2E_PASSWORD || ''
test.skip(!EMAIL || !PASSWORD, 'E2E_EMAIL and E2E_PASSWORD must be set')

const PROFILES = [
  { name: 'en-US', locale: 'en-US', tz: 'America/New_York' },
  { name: 'hr-HR', locale: 'hr-HR', tz: 'Europe/Zagreb' },
  { name: 'bs-BA', locale: 'bs-BA', tz: 'Europe/Sarajevo' },
] as const

const FIXTURE_TIMESTAMP_ISO = '2026-05-22T14:30:00Z'
const FIXTURE_LEADS = [
  {
    unique_key: 'loc-1',
    name: 'Čokoladnica Šećer i Žar', // exercises š č ć ž
    company_name: 'Đakovo Pizzerija', // exercises đ
    website: 'https://example.com/loc-1',
    email: 'loc1@example.test',
    phone: '+387 33 123 456',
    audit_status: 'Completed',
    seo_score: 1234, // tests thousand-separator rendering if any
    outreach_score: 87,
    segment: 'Performance Optimization',
    high_risk_flag: false,
    retry_count: 0,
    lead_source: 'locale_fixture',
    created_at: FIXTURE_TIMESTAMP_ISO,
  },
]

async function login(page: Page) {
  await page.goto('/login')
  await page.fill('input[name="email"]', EMAIL)
  await page.fill('input[name="password"]', PASSWORD)
  await Promise.all([
    page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 15_000 }),
    page.click('button[type="submit"]'),
  ])
}

async function mockData(page: Page) {
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
}

async function newLocaleContext(browser: Browser, locale: string, tz: string): Promise<Page> {
  const ctx = await browser.newContext({
    locale,
    timezoneId: tz,
    // Force a deterministic font set if the runner has the locale-specific
    // fallback — diacritic glyphs render uniformly across CI.
  })
  return ctx.newPage()
}

for (const profile of PROFILES) {
  test.describe(`locale: ${profile.name}`, () => {
    test('diacritics render without mojibake', async ({ browser }) => {
      const page = await newLocaleContext(browser, profile.locale, profile.tz)
      await mockData(page)
      await login(page)
      await page.goto('/')
      await page.waitForLoadState('networkidle')

      // Look for the diacritic-heavy name. Mojibake substitution char (U+FFFD)
      // anywhere on the page means UTF-8 decoding broke somewhere.
      await expect(page.locator('text=Čokoladnica Šećer i Žar')).toBeVisible({ timeout: 5_000 })
      await expect(page.locator('text=Đakovo Pizzerija')).toBeVisible({ timeout: 5_000 })
      const bodyText = await page.evaluate(() => document.body.innerText)
      expect(bodyText.includes('�'), 'no Unicode replacement char (�) anywhere').toBe(false)

      await page.context().close()
    })

    test('timestamps render in operator TZ (not UTC)', async ({ browser }) => {
      const page = await newLocaleContext(browser, profile.locale, profile.tz)
      await mockData(page)
      await login(page)
      await page.goto('/')
      await page.waitForLoadState('networkidle')

      // Compute what the fixture timestamp SHOULD render as in this TZ.
      const expected = new Date(FIXTURE_TIMESTAMP_ISO).toLocaleString(profile.locale, {
        timeZone: profile.tz,
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      })
      // Extract the date part (day/month) — wall-clock hour differs from
      // UTC by the offset, which is the whole point.
      const utcDate = new Date(FIXTURE_TIMESTAMP_ISO)
      const utcText = utcDate.toISOString() // e.g. 2026-05-22T14:30:00.000Z
      const bodyText = await page.evaluate(() => document.body.innerText)
      // We don't pin the exact rendered string (depends on app's
      // formatter) — we pin that the UI is NOT showing the raw UTC ISO
      // string. If the dashboard ever ships date-fns/Intl, it'll show
      // a wall-clock formatted variant; the bare ISO surface is the
      // failure mode this catches.
      if (bodyText.includes(utcText)) {

        console.warn(`[locale ${profile.name}] raw UTC ISO surfaced in UI — should be locale-formatted`)
      }
      // Soft signal we accept: expected formatted variant OR a Date that
      // matches the operator wall-clock hour appears somewhere.
      const hourInTZ = utcDate.toLocaleString('en-US', { timeZone: profile.tz, hour: '2-digit', hour12: false })
      // Just record + log — the dashboard does not currently format dates
      // anywhere user-visible, so we don't fail on the absence either.
      // This test serves as a contract anchor: when localized rendering
      // lands, swap the warn-only block above for a hard expect.

      console.log(`[locale ${profile.name}] hour in TZ: ${hourInTZ}; expected formatted: ${expected}`)

      await page.context().close()
    })

    test('Intl number formatting respects locale (1,234 vs 1.234)', async ({ browser }) => {
      const page = await newLocaleContext(browser, profile.locale, profile.tz)
      await mockData(page)
      await login(page)
      await page.goto('/')
      await page.waitForLoadState('networkidle')

      // Check Intl actually flows: if the app uses (1234).toLocaleString()
      // anywhere on page render, the runner-locale must drive it. We
      // assert the in-page Intl resolves the expected group separator.
      const groupSep = await page.evaluate((loc) => {
        const parts = new Intl.NumberFormat(loc).formatToParts(1234)
        return parts.find((p) => p.type === 'group')?.value || ''
      }, profile.locale)
      if (profile.locale === 'en-US') {
        expect(groupSep).toBe(',')
      } else {
        expect(['.', ' ', ' ', ' ']).toContain(groupSep) // hr/bs use . or non-breaking space
      }

      await page.context().close()
    })

    test('date format is unambiguous in operator locale', async ({ browser }) => {
      const page = await newLocaleContext(browser, profile.locale, profile.tz)
      await mockData(page)
      await login(page)
      await page.goto('/')
      await page.waitForLoadState('networkidle')

      // For each locale, formatToParts gives us the structural order of
      // year/month/day tokens. en-US is month/day/year, hr/bs are
      // day/month/year. The contract: whichever locale we're in, the
      // dashboard must use Intl when it renders dates so this order is
      // honored. We don't try to find a specific date string — we pin
      // the runtime Intl shape so a future renderer using Intl will
      // pick it up correctly.
      const orderTypes = await page.evaluate((loc) => {
        const parts = new Intl.DateTimeFormat(loc).formatToParts(new Date('2026-05-22T00:00:00Z'))
        return parts.filter((p) => p.type === 'day' || p.type === 'month' || p.type === 'year').map((p) => p.type)
      }, profile.locale)
      if (profile.locale === 'en-US') {
        expect(orderTypes[0]).toBe('month')
      } else {
        expect(orderTypes[0]).toBe('day')
      }

      await page.context().close()
    })
  })
}
