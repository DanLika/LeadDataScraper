# Gemini quota exhausted (upstream 429)

## Status

**RESOLVED for graceful surfacing** (PR #420, 2026-05-29). Operator-side
key rotation procedure is still manual — covered below.

## Symptom

Any AI-backed endpoint returns HTTP **503** with body
`{"error": "ai_quota_exceeded", "retry_after": "tomorrow"}`. Frontend
`AIChat` renders "AI temporarily unavailable, retry tomorrow." instead
of a raw error string. Affected surfaces:

- `POST /ask` (AIChat)
- `POST /execute` (plan-card confirm flow)
- `POST /draft-outreach` + `POST /draft-linkedin` (outreach modal)
- `GET /insights` (InsightsCharts page)
- `POST /campaigns/strategy` (campaign generator)

Non-Gemini paths stay healthy: `/leads`, `/stats`, discovery, SEO audit
(aiohttp), demo data, `/admin/gemini-budget` itself.

Distinguish from the local cap by body:

| Body | Cause | Operator action |
|---|---|---|
| `{"error": "ai_quota_exceeded", "retry_after": "tomorrow"}` | Upstream Google said 429 | Rotation OR wait for window reset |
| `{"error": "AI daily budget exhausted"}` | Our SQLite daily cap tripped | Bump `GEMINI_DAILY_TOKEN_CEILING` OR wait for UTC midnight reset |

Both return HTTP 503 — only the body distinguishes.

## Root cause

`google.genai.errors.ClientError` with `code=429` from the upstream
Gemini API. Two common triggers:

1. **Monthly billing cap** in `ai.studio` — operator-set spending limit
   reached. Window resets at billing-cycle boundary (typically month).
   `https://aistudio.google.com/u/0/billing` shows current spend vs cap.
2. **Free-tier RPM / RPD limit** — per-key rate ceiling. No paid plan or
   key has been throttled because of bursty calls.

The Python wrapper at `src/utils/gemini_call.py::_is_quota_error` matches
the exception by module-name prefix (`google.genai` or `google.api_core`)
**and** `code == 429`, then re-raises as
`src.errors.AIQuotaExceededError`. The FastAPI exception handler at
`backend/main.py::_ai_quota_exceeded_handler` maps that to the structured
503 body above.

The handler exists because the previous behaviour leaked the raw SDK
envelope to the client, which looked like a 5xx-shaped crash to the
operator.

## Fix recipe

### Option A — wait for upstream window reset

If the cause is the daily / monthly Google quota, no action restores
capacity faster than the reset. Confirm via Google AI Studio dashboard:

```
https://aistudio.google.com/u/0/usage   # current period usage
https://aistudio.google.com/u/0/billing # spending cap + reset date
```

Frontend already degrades gracefully — operator can use non-AI paths in
the meantime.

### Option B — rotate `GEMINI_API_KEY` (if cap is per-key, not per-billing-account)

1. Generate a new key at `https://aistudio.google.com/apikey`.
2. Verify locally before pushing:

   ```bash
   python3 -c "from google import genai; c=genai.Client(api_key='<NEW>'); \
     r=c.models.generate_content(model='gemini-flash-latest', contents='ping'); \
     print(r.text[:50])"
   ```

3. Push to Render via Management API (does NOT auto-redeploy — see
   [`render-env-push.md`](render-env-push.md) for the canonical recipe).
   Backend service ID: `srv-d89bisbbc2fs73f1pjpg`.
4. Persist to `~/.bookbed-secrets` AND `~/.env` (see
   [`env-var-local-vs-prod-drift.md`](env-var-local-vs-prod-drift.md) —
   one-side-only rotation is the common foot-gun).
5. Trigger a fresh backend deploy. Smoke `/ask` against prod with a
   trivial prompt — expect HTTP 200 with `response` body, NOT 503
   `ai_quota_exceeded`.

### Option C — raise the local SQLite cap (orthogonal — fixes `{"error":"AI daily budget exhausted"}`, not `ai_quota_exceeded`)

Edit `GEMINI_DAILY_TOKEN_CEILING` env on Render. Default 5,000,000. Do
NOT lower; do NOT rename to `GEMINI_DAILY_TOKEN_CAP` (would orphan
existing references in 7 call sites + 5 test files + Render env).

## Recurrence guard

- **Wrapper-level guard**: every Gemini call goes through
  `src/utils/gemini_call.py::guarded_generate_content[_async]`. A direct
  `client.models.generate_content` call somewhere in the codebase is
  enough to defeat the breaker — the
  `grep -rn "client\.models\.generate_content"` rule in CLAUDE.md is the
  regression guard.
- **Test-level guard**:
  `tests/unit/test_guarded_generate_content.py::TestQuotaExceededHandling`
  (7 cases) confirms sync + async wrappers translate 429 and only 429.
  `tests/unit/test_gemini_budget_endpoint.py::TestAIQuotaExceededExceptionHandler`
  (3 cases) confirms the FastAPI boundary emits the structured body on
  `/draft-outreach`, `/ask`, `/insights`.
- **Re-raise guard**: 5 `except (BudgetExceededError, AIQuotaExceededError): raise`
  clauses ahead of catch-all `except Exception` in
  `src/core/agentic_router.py`. Removing one would silently swallow the
  typed error and the FastAPI handler would never fire. No CI gate
  enforces this — review code-change PRs that touch agentic_router for
  the pattern.
- **Phantom pre-debit**: on a 429, the pre-call `check_budget` debit
  sticks (record_usage clamps delta ≥ 0, so no refund path). One 429 =
  estimate-sized phantom debit to the SQLite counter. Acceptable at the
  current volume; large 429 flood could prematurely trip the local cap.
  Tracked but not gated.

## Related

- [`render-env-push.md`](render-env-push.md) — Render env-var push recipe.
- [`env-var-local-vs-prod-drift.md`](env-var-local-vs-prod-drift.md) —
  local vs prod env-var drift class.
- `docs/secret-inventory.md` — `GEMINI_API_KEY` rotation cadence
  (quarterly default).
- Memory: `gemini_quota_handling_2026-05-29.md` (code-side PR #420),
  `gemini_key_rotation_pending_2026-05-29.md` (operator action).
