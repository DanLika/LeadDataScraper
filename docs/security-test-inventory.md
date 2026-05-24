# Security Test Inventory

Every defense in docs/security-invariants.md is locked in by a test below.

## Security test inventory

Every defense above is locked in by a test. When you change a defense,
the matching file fails loudly. Live-infra tests opt in via env var so
CI stays green without setup.

**Pure unit / fast (always run in `pytest tests/`):**
- `tests/test_validation_authz_gate.py` — 422 schema-leak gate
- `tests/test_execute_plan_model.py` — `/execute` Literal allowlist
- `tests/test_email_sender_guards.py` + `tests/test_crlf_injection.py` —
  SMTP CRLF / log-line forgery / `aiohttp` outbound-header rejection
  (12 tests + 77 subtests; one real bug fixed: SMTP regex `$` → `\Z`)
- `tests/test_ssrf_guard.py` + `tests/test_ssrf_deep.py` — IPv6
  classifications, DNS rebinding (mocked sequenced resolver), HTTP/0.9
  raw-socket rejection, static-scan for `max_redirects` / manual `Host`
  header / DNS-TXT lookups (26 tests)
- `tests/test_security_defenses.py` — `fenced_json` corpus + Playwright
  route guard
- `tests/test_prompt_injection_corpus.py` — 15-payload injection corpus
  through `fenced_json` + mocked-Gemini router/draft surfaces (12 tests
  + 34 subtests)
- `tests/test_redos.py` — Subject-parser regression + email-regex
  input-cap static scan (6 tests + 16 subtests; two real bugs fixed)
- `tests/test_json_pollution.py` — prototype pollution, duplicate-key
  smuggling, control chars, deep-nest 4xx (not 500), `NaN`/`Infinity`
  not crashing the 422 handler (104 tests; two real bugs fixed)
- `tests/test_error_message_leak.py` — fault-injected DB/Gemini/file
  errors scraped against an 18-regex sensitive-substring list; header
  fingerprint sweep; liveness probe + docs disabled checks (13 tests)
- `tests/test_upload_attacks.py` — `/upload` adversarial fuzz: boundary
  size, content-type / filename allowlists, traversal, NUL bytes,
  polyglot, BOMs, binary bombs, gzip lies (30 tests + 1 documented-skip)
- `tests/test_timing_attack.py` — `secrets.compare_digest` empirical
  timing distribution + source-grep assertion (4 tests; Welch's t-test
  via scipy if available)
- `tests/test_supabase_helper.py`, `tests/test_security_helpers.py`,
  `tests/test_csv_helper_health.py` — narrow utility-layer guards

**Frontend node tests (`cd frontend && node --test utils/...`):**
- `frontend/utils/url.test.mjs` — `sanitizeNext` open-redirect +
  decoded-payload rejection + `ensureProtocol` (57 cases)
- `frontend/utils/supabase/cookie-floor.test.mjs` — happy-path floor
- `frontend/utils/supabase/cookie-floor-fuzz.test.mjs` — full
  `(sameSite, httpOnly, secure)` adversarial matrix (1157 cases + 2
  documented-skip TODOs: domain narrowing + `__Host-` prefix)

**Opt-in e2e (env-gated; require running infra + real Supabase user):**
- `tests/test_supabase_anon_bypass.py` — PostgREST direct-hit with anon
  key (auto-loads creds from `frontend/.env.local`; skips if absent)
- `tests/test_proxy_origin_csrf_e2e.py` — Playwright cross-origin POST
  (`RUN_PROXY_ORIGIN_E2E=1`)
- `tests/test_jwt_manipulation.py` — 6 JWT tamper variants vs the proxy
  auth gate (`RUN_JWT_MANIPULATION_E2E=1`)
- `tests/test_open_redirect.py` — Playwright `/login?next=`
  (`RUN_OPEN_REDIRECT_E2E=1`)
- `tests/test_idor_sweep.py` — wrong-API-key, path-traversal,
  enumeration timing, extra-param ignored (`RUN_IDOR_SWEEP=1`).
  Parametrize IDs are opaque labels (`first-char-mutated`,
  `bearer-prefix`) — pytest collection never echoes the real key value.
- `tests/test_concurrency_rate_limit_e2e.py` — `asyncio.gather` burst
  against rate-limited endpoints (`RUN_CONCURRENCY_E2E=1`); the
  `/leads/clear` ×10 case requires the extra
  `ALLOW_DESTRUCTIVE_LEADS_CLEAR=1` opt-in.

**Test-infrastructure patterns to know:**
- Backend tests use `fastapi.testclient.TestClient` against
  `from main import app` (with `backend/` added to `sys.path`).
- `backend/main.py` resolves `db` / `router` / `auditor` /
  `orchestrator` via module `__getattr__` lazy load + a lifespan
  priming loop (`sys.modules[__name__]` attribute access — see the
  "PEP 562 trap" note in the cold-start invariants). The
  `TestClient`-driven tests don't run the lifespan, so they still hit
  the original "name not in globals" path. Pattern:
  `_prime_lazy_globals` autouse fixture injects `MagicMock` /
  `AsyncMock` replacements (see `tests/test_json_pollution.py` +
  `tests/test_error_message_leak.py`). The prod-mode fix and the
  test-fixture priming are independent layers — both stay.
- `/upload` + `/orchestrator/start` rate-limits trip during long test
  sweeps. Pattern: `_reset_rate_limiter` autouse fixture clears the
  slowapi `MovingWindowStorage` between tests.
- ReDoS tests bound `re.search` with `signal.SIGALRM` +
  `setitimer(ITIMER_REAL, ...)`. POSIX-only; falls back to wall-clock
  on Windows.
- Tests that touch real secrets (API keys etc.) MUST use opaque
  parametrize ids — `ids=["first-char-mutated", ...]` not the value
  itself — so pytest collection never echoes the secret to stdout /
  CI logs.

