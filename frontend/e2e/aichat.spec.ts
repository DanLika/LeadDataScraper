import { test, expect, type Page, type Route } from '@playwright/test'

// AIChat contract. Mocks /api/proxy/ask + /api/proxy/execute so the spec
// pins UI behaviour (plan card render, confirm/dismiss, auto-execute on
// read-only, clarification, rate-limit feedback) without spending real
// Gemini quota or depending on natural-language stability.
//
// Required env: E2E_BASE_URL, E2E_EMAIL, E2E_PASSWORD.

const EMAIL = process.env.E2E_EMAIL || ''
const PASSWORD = process.env.E2E_PASSWORD || ''
test.skip(!EMAIL || !PASSWORD, 'E2E_EMAIL and E2E_PASSWORD must be set')
test.describe.configure({ mode: 'serial' })

async function login(page: Page) {
  await page.goto('/login')
  await page.fill('input[name="email"]', EMAIL)
  await page.fill('input[name="password"]', PASSWORD)
  await Promise.all([
    page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 15_000 }),
    page.click('button[type="submit"]'),
  ])
}

async function openChat(page: Page) {
  // The chat mounts minimized — restore by clicking the floating button.
  const opener = page.getByRole('button', { name: /Open AI chat/i })
  if (await opener.isVisible().catch(() => false)) await opener.click()
  await expect(page.getByRole('region', { name: /AI assistant/i })).toBeVisible({ timeout: 5_000 })
}

async function sendMessage(page: Page, text: string) {
  const input = page.getByRole('textbox', { name: /Ask the AI assistant/i })
  await input.fill(text)
  await page.getByRole('button', { name: /Send message/i }).click()
}

async function fulfillAsk(route: Route, body: object): Promise<void> {
  await route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

test.describe('AIChat', () => {
  test('plan card renders task + params + reasoning with Confirm/Dismiss', async ({ page }) => {
    await page.route('**/api/proxy/ask', (route) =>
      fulfillAsk(route, {
        response: "I'll discover dentists in Sarajevo for you.",
        plan: {
          task: 'DISCOVERY_SEARCH',
          params: { query: 'dentists', location: 'Sarajevo', limit: 3 },
          reasoning: 'User asked for a Google-Maps discovery on 3 dentists.',
        },
      }),
    )

    await login(page)
    await openChat(page)
    await sendMessage(page, 'find 3 dentists in Sarajevo')

    await expect(page.getByTestId('plan-card')).toBeVisible({ timeout: 8_000 })
    await expect(page.getByTestId('plan-task')).toHaveText('DISCOVERY_SEARCH')
    await expect(page.getByTestId('plan-params')).toContainText('Sarajevo')
    await expect(page.getByTestId('plan-reasoning')).toContainText(/discovery|dentist/i)
    await expect(page.getByRole('button', { name: /Confirm & Execute/i })).toBeVisible()
    await expect(page.getByRole('button', { name: /Dismiss|Cancel/i })).toBeVisible()

    await page.unroute('**/api/proxy/ask')
  })

  test('Dismiss removes the plan without executing', async ({ page }) => {
    let askCalled = 0
    let executeCalled = 0
    await page.route('**/api/proxy/ask', (route) => {
      askCalled += 1
      return fulfillAsk(route, {
        response: 'Plan ready.',
        plan: { task: 'SEO_AUDIT', params: { filters: 'high-risk' }, reasoning: 'Audit run' },
      })
    })
    await page.route('**/api/proxy/execute', (route) => {
      executeCalled += 1
      return route.fulfill({ status: 200, contentType: 'application/json', body: '{"result":{}}' })
    })

    await login(page)
    await openChat(page)
    await sendMessage(page, 'run a quick audit')
    await expect(page.getByTestId('plan-card')).toBeVisible()
    await page.getByRole('button', { name: /Dismiss|Cancel/i }).click()
    await expect(page.getByTestId('plan-card')).toBeHidden()

    expect(askCalled).toBe(1)
    expect(executeCalled, '/execute must NOT be called when user dismisses the plan').toBe(0)

    await page.unroute('**/api/proxy/ask')
    await page.unroute('**/api/proxy/execute')
  })

  test('Confirm & Execute fires /execute and the result shows in chat', async ({ page }) => {
    await page.route('**/api/proxy/ask', (route) =>
      fulfillAsk(route, {
        response: 'Plan ready.',
        plan: { task: 'DISCOVERY_SEARCH', params: { query: 'dentists', location: 'Sarajevo' }, reasoning: 'Discovery' },
      }),
    )
    await page.route('**/api/proxy/execute', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ result: { message: 'Discovery started — job e2e-stub-job.' } }),
      }),
    )

    await login(page)
    await openChat(page)
    await sendMessage(page, 'find 3 dentists in Sarajevo')
    await page.getByRole('button', { name: /Confirm & Execute/i }).click()

    // After execute resolves, plan card is removed AND a result message lands.
    await expect(page.getByTestId('plan-card')).toBeHidden({ timeout: 8_000 })
    await expect(page.locator('text=Discovery started')).toBeVisible({ timeout: 5_000 })

    await page.unroute('**/api/proxy/ask')
    await page.unroute('**/api/proxy/execute')
  })

  test('read-only query auto-executes — no plan card, answer appears inline', async ({ page }) => {
    // Backend /ask handler auto-runs DATABASE_QUERY / STATUS_CHECK /
    // GET_INSIGHTS and returns `{response: "<answer>"}` with NO plan field.
    await page.route('**/api/proxy/ask', (route) =>
      fulfillAsk(route, {
        response: 'You have 401 leads — 370 Completed, 30 Failed, 1 Pending.',
      }),
    )

    await login(page)
    await openChat(page)
    await sendMessage(page, 'how many leads do I have')

    await expect(page.locator('text=401 leads').first()).toBeVisible({ timeout: 8_000 })
    await expect(page.getByTestId('plan-card')).toBeHidden()
    await expect(page.getByRole('button', { name: /Confirm & Execute/i })).toHaveCount(0)

    await page.unroute('**/api/proxy/ask')
  })

  test('ambiguous query → assistant asks for clarification (free text, no plan)', async ({ page }) => {
    // UNKNOWN task per agentic_router contract — backend returns the
    // Gemini free-text reply as `response`, with no plan.
    await page.route('**/api/proxy/ask', (route) =>
      fulfillAsk(route, {
        response: "Could you tell me a bit more? I can audit a lead, draft outreach, or find new prospects — which one did you mean?",
      }),
    )

    await login(page)
    await openChat(page)
    await sendMessage(page, 'do the thing')

    await expect(page.locator('text=Could you tell me a bit more')).toBeVisible({ timeout: 8_000 })
    await expect(page.getByTestId('plan-card')).toBeHidden()

    await page.unroute('**/api/proxy/ask')
  })

  test('rate-limit feedback surfaces in UI (no silent 429)', async ({ page }) => {
    // 11 fast messages — flip the route to 429 once the 11th lands.
    let calls = 0
    await page.route('**/api/proxy/ask', async (route) => {
      calls += 1
      if (calls >= 11) {
        await route.fulfill({
          status: 429,
          contentType: 'application/json',
          body: JSON.stringify({ error: 'Rate limit exceeded: 10 per 1 minute' }),
        })
      } else {
        await fulfillAsk(route, { response: `ack ${calls}` })
      }
    })

    await login(page)
    await openChat(page)
    for (let i = 1; i <= 11; i++) {
      await sendMessage(page, `ping ${i}`)
      // Wait for handleSubmit to finish before firing next — the input is
      // disabled while isLoading, so this also matches the natural pace.
      await expect(page.getByRole('textbox', { name: /Ask the AI assistant/i })).toBeEnabled({ timeout: 5_000 })
    }
    // After the 11th call (429), the assistant message must surface the
    // rate-limit detail rather than swallow it.
    await expect(
      page.locator('text=/rate limit|too many|429|10 per 1 minute/i').last(),
    ).toBeVisible({ timeout: 5_000 })
    expect(calls).toBeGreaterThanOrEqual(11)

    await page.unroute('**/api/proxy/ask')
  })
})
