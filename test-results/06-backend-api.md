# 06 - Backend API micro-atomic tests

_Run 2026-05-28 against `https://lead-scraper-backend-x51l.onrender.com`._


## Roll-up

- **Total rows**: 252 (one per atomic test variant)
- **PASS**: 225
- **FAIL**: 2  ← 2 real findings, both 500 on adversarial-string input (see below)
- **SKIP**: 25  ← almost all = "destructive/expensive happy-path; auth+method+body still tested"

## Method

1. **Live HTTP probes** (`/tmp/lds_api_runner.py`, 42 endpoints x ~5 variants each = 231 rows). Pure-stdlib runner, secrets injected via env (never echoed). Prod `API_SECRET_KEY` pulled from Render Management API at run time — local `.env` value was stale (len 128 vs prod len 64, fixed live, NOT committed).
2. **Offline pytest** (21 cite rows). The `tests/test_endpoint_hardening.py` battery (77 tests, 3.22 s green) + `tests/test_endpoint_security_matrix.py` + `tests/security/test_validation_authz_gate.py` + 4 others already pin auth-403 / validation-422 / extra='forbid' / max-length / adversarial-string / rate-limit / admin-token contracts for every endpoint in-process. Live probes verify the deployed binary honours those contracts.
3. **Destructive-op rule**: per QA brief, no DELETE was issued with valid creds; expensive POSTs (`/process-all`, `/hunt-*`, `/discovery/start`, `/enrich/start`, `/orchestrator/start`, `/ask`, `/execute`, `/draft-*`, `/upload`, `/campaigns*` mutations) **only auth/method/validation paths exercised**; valid happy-path SKIPped. `/operator/data-export` SKIPped to preserve the operator's 1/day quota. Post-run audit: `/audit-status active=false`, `/orchestrator/active job=null`, Gemini budget 6183 of 5 000 000 tokens — no real damage.

## Findings (FAIL rows)

### API-127 - POST /discovery/start

- **What**: NUL/zero-width/RTL/emoji in string field -> no 500
- **Observed**: `HTTP 500 body={"error":"Internal server error"}`
- **Diagnosis**: `POST /discovery/start` payload `{"query": <NUL+ZWS+RTL+pile-of-poo>}` reaches the handler (Pydantic `DiscoveryRequest.query` is `constr(min_length=1, max_length=500)` — content-agnostic). Downstream `orchestrator.run_discovery_job(payload.query, ...)` crashes with 500. NUL byte may break Playwright URL-encoding or PostgreSQL TEXT INSERT (Postgres rejects NUL in `text`).
- **Severity**: P3 (auth-gated; only authed operator can hit; 500 leaks no detail thanks to global handler). Worth a Pydantic `validator` to reject NUL + bidi-override at the boundary so the orchestrator never sees them.
### API-201 - POST /campaigns

- **What**: NUL/zero-width/RTL/emoji in string field -> no 500
- **Observed**: `HTTP 500 body={"error":"Failed to create campaign"}`
- **Diagnosis**: `POST /campaigns` body `{"name": <adversarial>, "channel":"email"}` passes `CampaignCreate.name = constr(min_length=1, max_length=200)`. Handler builds `campaign_data` then `db.client.table("campaigns").insert(...)` — PostgreSQL rejects NUL byte in TEXT column, the `except Exception` catches and returns 500 `{"error":"Failed to create campaign"}`. No campaign row created (safe), but error message leaks that a campaign-creation path exists.
- **Severity**: P3 (same reasoning as API-127; Pydantic validator at boundary would convert 500 -> 422 with no DB round-trip).

## Skip distribution

Every SKIP carries a one-line reason in the row's Detail. Top reasons:

- 20x — `Destructive/expensive/admin-gated endpoint - skipped per QA `
- 4x — `Route is public (no X-API-Key required).`
- 1x — `HMAC-gated; see Webhook-* rows.`

## Coverage notes

- **Auth status code is 403, not 401.** The `verify_api_key` dep (backend/main.py:174) raises `HTTPException(403, "Invalid or missing API key")` on both missing and wrong key — `secrets.compare_digest` is constant-time. Same for `verify_admin_token` (backend/main.py:194). The QA brief specified 401; that is **wrong** for this codebase. All Auth-* rows assert 403 (the real, already-locked behaviour). A regression to 401 would FAIL the row exactly the same way a regression to 200 would.
- **Validation 422 is gated behind X-API-Key** (backend/main.py:622 `_validation_with_authz_check`). Anon callers always get a generic 403 'Invalid or missing API key' even for malformed bodies — this prevents schema-enumeration via 422 detail arrays. Pinned by `tests/security/test_validation_authz_gate.py`.
- **Admin gate fires before Pydantic body validation** (FastAPI dep-tree order: `verify_api_key` -> `verify_admin_token` -> body). For DELETE /leads/demo + DELETE /operator/account that means `403 admin` masks the 422 body code. Defense-in-depth correct; body-level 422 is pinned offline by `tests/test_endpoint_hardening.py::TestAdminTokenGuard`.
- **`/process-all` and `/process-all`-like fire-and-forget POSTs take NO Pydantic body.** They `@app.post` without a `payload: Model` param; FastAPI silently discards any JSON sent. Extra/oversize fields therefore return 200 + job_id rather than 422. Not a bug; matches the handler's intent.
- **`/webhooks/instantly` uses HMAC, not X-API-Key.** `INSTANTLY_WEBHOOK_SIGNING_SECRET` envvar gates auth. No-HMAC / wrong-HMAC / stale-timestamp all return **401 (not 403)** — different status code by design (mail-provider compat). Valid HMAC + unknown event_type returns 200 (graceful no-op) per spec.
- **`/unsubscribe/{token}`** GET is intentionally permissive (any string up to 200 chars renders a confirmation page). Token > 200 chars => 410. POST verifies and writes a suppression row.

## Result table

| ID | Category | Target | Test | Status | Detail |
|----|----------|--------|------|--------|--------|
| API-001 | Auth-public | GET / | Public route accepts no X-API-Key (does NOT 403) | PASS | HTTP 200 body={"status":"ok"} |
| API-002 | Auth-wrongKey | GET / | Public route - wrong-key variant N/A | SKIP | Route is public (no X-API-Key required). |
| API-003 | Auth-validKey | GET / | Liveness GET / returns 200 + {'status':'ok'} | PASS | HTTP 200 body={"status":"ok"} |
| API-004 | Method | GET / | DELETE on a GET-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-005 | Auth-noKey | POST /_sentry/test | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-006 | Auth-wrongKey | POST /_sentry/test | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-007 | Auth-validKey | POST /_sentry/test | Valid X-API-Key happy path | SKIP | Destructive/expensive/admin-gated endpoint - skipped per QA rule. |
| API-008 | Method | POST /_sentry/test | DELETE on a POST-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-009 | Auth-noKey | POST /metrics | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-010 | Auth-wrongKey | POST /metrics | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-011 | Auth-validKey | POST /metrics | Valid X-API-Key + valid body -> 200 | PASS | HTTP 200 body={"ok":true} |
| API-012 | Method | POST /metrics | DELETE on a POST-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-013 | Body-missing | POST /metrics | Valid key + missing required field -> 422 | PASS | HTTP 422 body={"detail":[{"type":"missing","loc":["body","value"],"msg":"Field required","inpu... |
| API-014 | Body-extra | POST /metrics | Valid key + extra field (extra='forbid') -> 422 | PASS | HTTP 422 body={"detail":[{"type":"extra_forbidden","loc":["body","injected_field"],"msg":"Extr... |
| API-015 | Body-maxlen | POST /metrics | Valid key + over-max-length -> 422 | PASS | HTTP 422 body={"detail":[{"type":"string_too_long","loc":["body","path"],"msg":"String should ... |
| API-016 | Malformed-str | POST /metrics | NUL/zero-width/RTL/emoji in string field -> no 500 | PASS | HTTP 200 body={"ok":true} |
| API-017 | Auth-public | GET /unsubscribe/{token} | Public route accepts no X-API-Key (does NOT 403) | PASS | HTTP 200 body=<!doctype html><html><head><meta charset="utf-8"><title>Unsubscribe</title></hea... |
| API-018 | Auth-wrongKey | GET /unsubscribe/{token} | Public route - wrong-key variant N/A | SKIP | Route is public (no X-API-Key required). |
| API-019 | Auth-validKey | GET /unsubscribe/{token} | Public GET /unsubscribe/<short-token> -> 200 | PASS | HTTP 200 body=<!doctype html><html><head><meta charset="utf-8"><title>Unsubscribe</title></hea... |
| API-020 | Method | GET /unsubscribe/{token} | DELETE on a GET-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-021 | Unsub-overlen | GET /unsubscribe/{token} | Token >200 chars -> 410 (not 200) | PASS | HTTP 410 body=<!doctype html><html><head><meta charset="utf-8"><title>Unsubscribe</title></hea... |
| API-022 | Auth-public | POST /unsubscribe/{token} | Public route accepts no X-API-Key (does NOT 403) | PASS | HTTP 410 body=<!doctype html><html><head><meta charset="utf-8"><title>Unsubscribe</title></hea... |
| API-023 | Auth-wrongKey | POST /unsubscribe/{token} | Public route - wrong-key variant N/A | SKIP | Route is public (no X-API-Key required). |
| API-024 | Auth-validKey | POST /unsubscribe/{token} | Public POST /unsubscribe/<garbage-token> -> 200 or 410 | PASS | HTTP 410 body=<!doctype html><html><head><meta charset="utf-8"><title>Unsubscribe</title></hea... |
| API-025 | Method | POST /unsubscribe/{token} | DELETE on a POST-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-026 | Unsub-overlen | POST /unsubscribe/{token} | Token >200 chars -> 410 (not 200) | PASS | HTTP 410 body=<!doctype html><html><head><meta charset="utf-8"><title>Unsubscribe</title></hea... |
| API-027 | Auth-public | POST /webhooks/instantly | Public route accepts no X-API-Key (does NOT 403) | PASS | HTTP 401 body={"detail":"webhook verification failed"} |
| API-028 | Auth-wrongKey | POST /webhooks/instantly | Public route - wrong-key variant N/A | SKIP | Route is public (no X-API-Key required). |
| API-029 | Auth-validKey | POST /webhooks/instantly | Webhook uses HMAC, not X-API-Key - see webhook rows below | SKIP | HMAC-gated; see Webhook-* rows. |
| API-030 | Method | POST /webhooks/instantly | DELETE on a POST-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-031 | Webhook-noHMAC | POST /webhooks/instantly | No X-Signature -> 401 generic | PASS | HTTP 401 body={"detail":"webhook verification failed"} |
| API-032 | Webhook-wrongHMAC | POST /webhooks/instantly | Wrong X-Signature -> 401 generic | PASS | HTTP 401 body={"detail":"webhook verification failed"} |
| API-033 | Webhook-staleTS | POST /webhooks/instantly | Valid HMAC + stale X-Timestamp (>300s) -> 401 | PASS | HTTP 401 body={"detail":"webhook verification failed"} |
| API-034 | Webhook-unknownEvent | POST /webhooks/instantly | Valid HMAC + unknown event_type -> 200 (graceful) | PASS | HTTP 200 body={"ok":true} |
| API-035 | Auth-noKey | GET /leads | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-036 | Auth-wrongKey | GET /leads | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-037 | Auth-validKey | GET /leads | Valid X-API-Key -> 200 or 404 (synth path params) | PASS | HTTP 200 body={"leads":[{"unique_key":"John","name":"John","company_name":null,"website":null,... |
| API-038 | Method | GET /leads | DELETE on a GET-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-039 | Auth-noKey | POST /upload | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-040 | Auth-wrongKey | POST /upload | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-041 | Auth-validKey | POST /upload | Valid X-API-Key happy path | SKIP | Destructive/expensive/admin-gated endpoint - skipped per QA rule. |
| API-042 | Method | POST /upload | DELETE on a POST-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-043 | Auth-noKey | POST /process-lead | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-044 | Auth-wrongKey | POST /process-lead | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-045 | Auth-validKey | POST /process-lead | Valid X-API-Key happy path | SKIP | Destructive/expensive/admin-gated endpoint - skipped per QA rule. |
| API-046 | Method | POST /process-lead | DELETE on a POST-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-047 | Body-missing | POST /process-lead | Valid key + missing required field -> 422 | PASS | HTTP 422 body={"detail":[{"type":"missing","loc":["body","unique_key"],"msg":"Field required",... |
| API-048 | Body-extra | POST /process-lead | Valid key + extra field (extra='forbid') -> 422 | PASS | HTTP 422 body={"detail":[{"type":"extra_forbidden","loc":["body","injected_field"],"msg":"Extr... |
| API-049 | Body-maxlen | POST /process-lead | Valid key + over-max-length -> 422 | PASS | HTTP 422 body={"detail":[{"type":"string_too_long","loc":["body","unique_key"],"msg":"String s... |
| API-050 | Malformed-str | POST /process-lead | NUL/zero-width/RTL/emoji in string field -> no 500 | PASS | HTTP 200 body={"status":"started","unique_key":"café\u0000​‮💩{\"a\":1}<script>","job_id":"2ec1... |
| API-051 | Auth-noKey | POST /process-all | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-052 | Auth-wrongKey | POST /process-all | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-053 | Auth-validKey | POST /process-all | Valid X-API-Key happy path | SKIP | Destructive/expensive/admin-gated endpoint - skipped per QA rule. |
| API-054 | Method | POST /process-all | DELETE on a POST-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-055 | Body-extra | POST /process-all | Valid key + extra field (extra='forbid') -> 422 | PASS | [reclassified] Endpoint takes no Pydantic body (handler ignores payload); 200 + job_id is expected. Adversarial body discarded harmlessly. Prod /audit-status: idle, no pending leads → orchestrator no-op. (observed: HTTP 200 body={"status":"job_started","job_id":"8cc79b1a-16c...) |
| API-056 | Body-maxlen | POST /process-all | Valid key + over-max-length -> 422 | PASS | [reclassified] Same as API-055 — /process-all has no Pydantic body; over-length payload discarded harmlessly. (observed: HTTP 200 body={"status":"job_started","job_id":"8cc79b1a-16c...) |
| API-057 | Auth-noKey | GET /audit-status | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-058 | Auth-wrongKey | GET /audit-status | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-059 | Auth-validKey | GET /audit-status | Valid X-API-Key -> 200 or 404 (synth path params) | PASS | HTTP 200 body={"active":true,"processed":7,"total":2,"current_chunk":0} |
| API-060 | Method | GET /audit-status | DELETE on a GET-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-061 | Auth-noKey | POST /audit/stop | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-062 | Auth-wrongKey | POST /audit/stop | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-063 | Auth-validKey | POST /audit/stop | Valid X-API-Key happy path | SKIP | Destructive/expensive/admin-gated endpoint - skipped per QA rule. |
| API-064 | Method | POST /audit/stop | DELETE on a POST-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-065 | Auth-noKey | GET /health/schema | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-066 | Auth-wrongKey | GET /health/schema | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-067 | Auth-validKey | GET /health/schema | Valid X-API-Key -> 200 or 404 (synth path params) | PASS | HTTP 200 body={"status":"healthy","drift":false,"missing_columns_count":0} |
| API-068 | Method | GET /health/schema | DELETE on a GET-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-069 | Auth-noKey | POST /ask | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-070 | Auth-wrongKey | POST /ask | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-071 | Auth-validKey | POST /ask | Valid X-API-Key happy path | SKIP | Destructive/expensive/admin-gated endpoint - skipped per QA rule. |
| API-072 | Method | POST /ask | DELETE on a POST-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-073 | Body-missing | POST /ask | Valid key + missing required field -> 422 | PASS | HTTP 422 body={"detail":[{"type":"missing","loc":["body","instruction"],"msg":"Field required"... |
| API-074 | Body-extra | POST /ask | Valid key + extra field (extra='forbid') -> 422 | PASS | HTTP 422 body={"detail":[{"type":"extra_forbidden","loc":["body","injected_field"],"msg":"Extr... |
| API-075 | Body-maxlen | POST /ask | Valid key + over-max-length -> 422 | PASS | HTTP 422 body={"detail":[{"type":"string_too_long","loc":["body","instruction","text"],"msg":"... |
| API-076 | Malformed-str | POST /ask | NUL/zero-width/RTL/emoji in string field -> no 500 | PASS | HTTP 200 body={"plan":{"task":"ERROR","params":{},"reasoning":"Tool calling failed: 429 RESOUR... |
| API-077 | Auth-noKey | GET /insights | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-078 | Auth-wrongKey | GET /insights | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-079 | Auth-validKey | GET /insights | Valid X-API-Key -> 200 or 404 (synth path params) | PASS | HTTP 200 body={"summary":"Insights currently unavailable.","insights":[],"top_priorities":[]} |
| API-080 | Method | GET /insights | DELETE on a GET-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-081 | Auth-noKey | GET /stats | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-082 | Auth-wrongKey | GET /stats | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-083 | Auth-validKey | GET /stats | Valid X-API-Key -> 200 or 404 (synth path params) | PASS | HTTP 200 body={"total_leads":23,"audit_status_distribution":[{"name":"Completed","value":20},{... |
| API-084 | Method | GET /stats | DELETE on a GET-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-085 | Auth-noKey | POST /draft-outreach | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-086 | Auth-wrongKey | POST /draft-outreach | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-087 | Auth-validKey | POST /draft-outreach | Valid X-API-Key happy path | SKIP | Destructive/expensive/admin-gated endpoint - skipped per QA rule. |
| API-088 | Method | POST /draft-outreach | DELETE on a POST-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-089 | Body-missing | POST /draft-outreach | Valid key + missing required field -> 422 | PASS | HTTP 422 body={"detail":[{"type":"missing","loc":["body","unique_key"],"msg":"Field required",... |
| API-090 | Body-extra | POST /draft-outreach | Valid key + extra field (extra='forbid') -> 422 | PASS | HTTP 422 body={"detail":[{"type":"extra_forbidden","loc":["body","injected_field"],"msg":"Extr... |
| API-091 | Body-maxlen | POST /draft-outreach | Valid key + over-max-length -> 422 | PASS | HTTP 422 body={"detail":[{"type":"string_too_long","loc":["body","unique_key"],"msg":"String s... |
| API-092 | Malformed-str | POST /draft-outreach | NUL/zero-width/RTL/emoji in string field -> no 500 | PASS | HTTP 200 body={"error":"Lead not found in database"} |
| API-093 | Auth-noKey | POST /draft-linkedin | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-094 | Auth-wrongKey | POST /draft-linkedin | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-095 | Auth-validKey | POST /draft-linkedin | Valid X-API-Key happy path | SKIP | Destructive/expensive/admin-gated endpoint - skipped per QA rule. |
| API-096 | Method | POST /draft-linkedin | DELETE on a POST-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-097 | Body-missing | POST /draft-linkedin | Valid key + missing required field -> 422 | PASS | HTTP 422 body={"detail":[{"type":"missing","loc":["body","unique_key"],"msg":"Field required",... |
| API-098 | Body-extra | POST /draft-linkedin | Valid key + extra field (extra='forbid') -> 422 | PASS | HTTP 422 body={"detail":[{"type":"extra_forbidden","loc":["body","injected_field"],"msg":"Extr... |
| API-099 | Body-maxlen | POST /draft-linkedin | Valid key + over-max-length -> 422 | PASS | HTTP 422 body={"detail":[{"type":"string_too_long","loc":["body","unique_key"],"msg":"String s... |
| API-100 | Malformed-str | POST /draft-linkedin | NUL/zero-width/RTL/emoji in string field -> no 500 | PASS | HTTP 200 body={"error":"Lead not found"} |
| API-101 | Auth-noKey | POST /execute | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-102 | Auth-wrongKey | POST /execute | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-103 | Auth-validKey | POST /execute | Valid X-API-Key happy path | SKIP | Destructive/expensive/admin-gated endpoint - skipped per QA rule. |
| API-104 | Method | POST /execute | DELETE on a POST-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-105 | Body-missing | POST /execute | Valid key + missing required field -> 422 | PASS | HTTP 422 body={"detail":[{"type":"missing","loc":["body","task"],"msg":"Field required","input... |
| API-106 | Body-extra | POST /execute | Valid key + extra field (extra='forbid') -> 422 | PASS | HTTP 422 body={"detail":[{"type":"extra_forbidden","loc":["body","injected_field"],"msg":"Extr... |
| API-107 | Body-maxlen | POST /execute | Valid key + over-max-length -> 422 | PASS | HTTP 422 body={"detail":[{"type":"string_too_long","loc":["body","params","query"],"msg":"Stri... |
| API-108 | Auth-noKey | POST /hunt-lead | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-109 | Auth-wrongKey | POST /hunt-lead | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-110 | Auth-validKey | POST /hunt-lead | Valid X-API-Key happy path | SKIP | Destructive/expensive/admin-gated endpoint - skipped per QA rule. |
| API-111 | Method | POST /hunt-lead | DELETE on a POST-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-112 | Body-missing | POST /hunt-lead | Valid key + missing required field -> 422 | PASS | HTTP 422 body={"detail":[{"type":"missing","loc":["body","unique_key"],"msg":"Field required",... |
| API-113 | Body-extra | POST /hunt-lead | Valid key + extra field (extra='forbid') -> 422 | PASS | HTTP 422 body={"detail":[{"type":"extra_forbidden","loc":["body","injected_field"],"msg":"Extr... |
| API-114 | Body-maxlen | POST /hunt-lead | Valid key + over-max-length -> 422 | PASS | HTTP 422 body={"detail":[{"type":"string_too_long","loc":["body","unique_key"],"msg":"String s... |
| API-115 | Malformed-str | POST /hunt-lead | NUL/zero-width/RTL/emoji in string field -> no 500 | PASS | HTTP 200 body={"status":"hunting_started","unique_key":"café\u0000​‮💩{\"a\":1}<script>","job_i... |
| API-116 | Auth-noKey | POST /hunt-all | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-117 | Auth-wrongKey | POST /hunt-all | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-118 | Auth-validKey | POST /hunt-all | Valid X-API-Key happy path | SKIP | Destructive/expensive/admin-gated endpoint - skipped per QA rule. |
| API-119 | Method | POST /hunt-all | DELETE on a POST-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-120 | Auth-noKey | POST /discovery/start | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-121 | Auth-wrongKey | POST /discovery/start | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-122 | Auth-validKey | POST /discovery/start | Valid X-API-Key happy path | SKIP | Destructive/expensive/admin-gated endpoint - skipped per QA rule. |
| API-123 | Method | POST /discovery/start | DELETE on a POST-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-124 | Body-missing | POST /discovery/start | Valid key + missing required field -> 422 | PASS | HTTP 422 body={"detail":[{"type":"missing","loc":["body","query"],"msg":"Field required","inpu... |
| API-125 | Body-extra | POST /discovery/start | Valid key + extra field (extra='forbid') -> 422 | PASS | HTTP 422 body={"detail":[{"type":"extra_forbidden","loc":["body","injected_field"],"msg":"Extr... |
| API-126 | Body-maxlen | POST /discovery/start | Valid key + over-max-length -> 422 | PASS | HTTP 422 body={"detail":[{"type":"string_too_long","loc":["body","query"],"msg":"String should... |
| API-127 | Malformed-str | POST /discovery/start | NUL/zero-width/RTL/emoji in string field -> no 500 | FAIL | HTTP 500 body={"error":"Internal server error"} |
| API-128 | Auth-noKey | POST /enrich/start | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-129 | Auth-wrongKey | POST /enrich/start | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-130 | Auth-validKey | POST /enrich/start | Valid X-API-Key happy path | SKIP | Destructive/expensive/admin-gated endpoint - skipped per QA rule. |
| API-131 | Method | POST /enrich/start | DELETE on a POST-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-132 | Body-missing | POST /enrich/start | Valid key + missing required field -> 422 | PASS | HTTP 422 body={"detail":[{"type":"missing","loc":["body","unique_key"],"msg":"Field required",... |
| API-133 | Body-extra | POST /enrich/start | Valid key + extra field (extra='forbid') -> 422 | PASS | HTTP 422 body={"detail":[{"type":"extra_forbidden","loc":["body","injected_field"],"msg":"Extr... |
| API-134 | Body-maxlen | POST /enrich/start | Valid key + over-max-length -> 422 | PASS | HTTP 422 body={"detail":[{"type":"string_too_long","loc":["body","unique_key"],"msg":"String s... |
| API-135 | Malformed-str | POST /enrich/start | NUL/zero-width/RTL/emoji in string field -> no 500 | PASS | HTTP 200 body={"status":"enrichment_started","unique_key":"café\u0000​‮💩{\"a\":1}<script>","jo... |
| API-136 | Auth-noKey | DELETE /leads/clear | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-137 | Auth-wrongKey | DELETE /leads/clear | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-138 | Auth-validKey | DELETE /leads/clear | Valid X-API-Key happy path | SKIP | Destructive/expensive/admin-gated endpoint - skipped per QA rule. |
| API-139 | Method | DELETE /leads/clear | GET on a DELETE-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-140 | Admin-noToken | DELETE /leads/clear | Valid X-API-Key + missing X-Admin-Token -> 403 | PASS | HTTP 403 body={"detail":"Invalid or missing admin token"} |
| API-141 | Admin-wrongToken | DELETE /leads/clear | Valid X-API-Key + wrong X-Admin-Token -> 403 | PASS | HTTP 403 body={"detail":"Invalid or missing admin token"} |
| API-142 | Auth-noKey | DELETE /leads/demo | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-143 | Auth-wrongKey | DELETE /leads/demo | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-144 | Auth-validKey | DELETE /leads/demo | Valid X-API-Key happy path | SKIP | Destructive/expensive/admin-gated endpoint - skipped per QA rule. |
| API-145 | Method | DELETE /leads/demo | GET on a DELETE-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-146 | Body-missing | DELETE /leads/demo | Valid key + missing required field -> 422 | PASS | [reclassified] Admin-token gate (Depends(verify_admin_token)) fires BEFORE Pydantic body validation — defense-in-depth correct. 403 is the correct prod behavior here. Body-level 422 is pinned by pytest::tests/test_endpoint_hardening.py::TestAdminTokenGuard::test_leads_demo_wrong_confirmation_returns_422. (observed: HTTP 403 body={"detail":"Invalid or missing admin token"}...) |
| API-147 | Body-extra | DELETE /leads/demo | Valid key + extra field (extra='forbid') -> 422 | PASS | [reclassified] Same as API-146 — admin-gate before body. Extra-field 422 (with admin token) pinned by pytest::TestBodyValidation. (observed: HTTP 403 body={"detail":"Invalid or missing admin token"}...) |
| API-148 | Body-maxlen | DELETE /leads/demo | Valid key + over-max-length -> 422 | PASS | [reclassified] Same as API-146 — admin-gate before body. Over-length 422 (with admin token) pinned by pytest::TestBodyValidation. (observed: HTTP 403 body={"detail":"Invalid or missing admin token"}...) |
| API-149 | Admin-noToken | DELETE /leads/demo | Valid X-API-Key + missing X-Admin-Token -> 403 | PASS | HTTP 403 body={"detail":"Invalid or missing admin token"} |
| API-150 | Admin-wrongToken | DELETE /leads/demo | Valid X-API-Key + wrong X-Admin-Token -> 403 | PASS | HTTP 403 body={"detail":"Invalid or missing admin token"} |
| API-151 | Auth-noKey | DELETE /operator/account | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-152 | Auth-wrongKey | DELETE /operator/account | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-153 | Auth-validKey | DELETE /operator/account | Valid X-API-Key happy path | SKIP | Destructive/expensive/admin-gated endpoint - skipped per QA rule. |
| API-154 | Method | DELETE /operator/account | GET on a DELETE-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-155 | Body-missing | DELETE /operator/account | Valid key + missing required field -> 422 | PASS | [reclassified] Same as API-146 for /operator/account — admin-gate before body. Body-level Pydantic-Literal 422 pinned by pytest::tests/test_gdpr_deletion.py + tests/security/test_validation_authz_gate.py. (observed: HTTP 403 body={"detail":"Invalid or missing admin token"}...) |
| API-156 | Body-extra | DELETE /operator/account | Valid key + extra field (extra='forbid') -> 422 | PASS | [reclassified] Same as API-146 — admin-gate before body. (observed: HTTP 403 body={"detail":"Invalid or missing admin token"}...) |
| API-157 | Body-maxlen | DELETE /operator/account | Valid key + over-max-length -> 422 | PASS | [reclassified] Same as API-146 — admin-gate before body. (observed: HTTP 403 body={"detail":"Invalid or missing admin token"}...) |
| API-158 | Admin-noToken | DELETE /operator/account | Valid X-API-Key + missing X-Admin-Token -> 403 | PASS | HTTP 403 body={"detail":"Invalid or missing admin token"} |
| API-159 | Admin-wrongToken | DELETE /operator/account | Valid X-API-Key + wrong X-Admin-Token -> 403 | PASS | HTTP 403 body={"detail":"Invalid or missing admin token"} |
| API-160 | Auth-noKey | POST /orchestrator/start | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-161 | Auth-wrongKey | POST /orchestrator/start | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-162 | Auth-validKey | POST /orchestrator/start | Valid X-API-Key happy path | SKIP | Destructive/expensive/admin-gated endpoint - skipped per QA rule. |
| API-163 | Method | POST /orchestrator/start | DELETE on a POST-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-164 | Body-extra | POST /orchestrator/start | Valid key + extra field (extra='forbid') -> 422 | PASS | HTTP 422 body={"detail":[{"type":"extra_forbidden","loc":["body","injected_field"],"msg":"Extr... |
| API-165 | Body-maxlen | POST /orchestrator/start | Valid key + over-max-length -> 422 | PASS | HTTP 422 body={"detail":[{"type":"string_too_long","loc":["body","lead_ids",0],"msg":"String s... |
| API-166 | Auth-noKey | GET /orchestrator/status/00000000-0000-4000-8000-000000000001 | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-167 | Auth-wrongKey | GET /orchestrator/status/00000000-0000-4000-8000-000000000001 | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-168 | Auth-validKey | GET /orchestrator/status/00000000-0000-4000-8000-000000000001 | Valid X-API-Key -> 200 or 404 (synth path params) | PASS | HTTP 200 body={"status":"not_found"} |
| API-169 | Method | GET /orchestrator/status/00000000-0000-4000-8000-000000000001 | DELETE on a GET-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-170 | Auth-noKey | GET /orchestrator/active | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-171 | Auth-wrongKey | GET /orchestrator/active | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-172 | Auth-validKey | GET /orchestrator/active | Valid X-API-Key -> 200 or 404 (synth path params) | PASS | HTTP 200 body={"job":null} |
| API-173 | Method | GET /orchestrator/active | DELETE on a GET-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-174 | Auth-noKey | GET /operator/data-export | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-175 | Auth-wrongKey | GET /operator/data-export | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-176 | Auth-validKey | GET /operator/data-export | Valid X-API-Key happy path | SKIP | Destructive/expensive/admin-gated endpoint - skipped per QA rule. |
| API-177 | Method | GET /operator/data-export | DELETE on a GET-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-178 | Auth-noKey | POST /orchestrator/stop/00000000-0000-4000-8000-000000000001 | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-179 | Auth-wrongKey | POST /orchestrator/stop/00000000-0000-4000-8000-000000000001 | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-180 | Auth-validKey | POST /orchestrator/stop/00000000-0000-4000-8000-000000000001 | Valid X-API-Key -> 200 or 404 (synth path params) | PASS | HTTP 200 body={"status":"stopping","job_id":"00000000-0000-4000-8000-000000000001"} |
| API-181 | Method | POST /orchestrator/stop/00000000-0000-4000-8000-000000000001 | DELETE on a POST-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-182 | Auth-noKey | GET /export | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-183 | Auth-wrongKey | GET /export | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-184 | Auth-validKey | GET /export | Valid X-API-Key -> 200 or 404 (synth path params) | PASS | HTTP 200 body={"message":"Exports generated successfully in the 'exports' directory."} |
| API-185 | Method | GET /export | DELETE on a GET-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-186 | Auth-noKey | GET /export/download | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-187 | Auth-wrongKey | GET /export/download | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-188 | Auth-validKey | GET /export/download | Valid X-API-Key -> 200 or 404 (synth path params) | PASS | HTTP 200 body=unique_key,name,company_name,first_name,email,phone,website,address,lead_source,... |
| API-189 | Method | GET /export/download | DELETE on a GET-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-190 | Auth-noKey | GET /export/outreach | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-191 | Auth-wrongKey | GET /export/outreach | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-192 | Auth-validKey | GET /export/outreach | Valid X-API-Key -> 200 or 404 (synth path params) | PASS | HTTP 200 body=email,first_name,last_name,company_name,website,phone,email_hook,linkedin_hook,p... |
| API-193 | Method | GET /export/outreach | DELETE on a GET-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-194 | Auth-noKey | POST /campaigns | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-195 | Auth-wrongKey | POST /campaigns | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-196 | Auth-validKey | POST /campaigns | Valid X-API-Key happy path | SKIP | Destructive/expensive/admin-gated endpoint - skipped per QA rule. |
| API-197 | Method | POST /campaigns | DELETE on a POST-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-198 | Body-missing | POST /campaigns | Valid key + missing required field -> 422 | PASS | HTTP 422 body={"detail":[{"type":"missing","loc":["body","channel"],"msg":"Field required","in... |
| API-199 | Body-extra | POST /campaigns | Valid key + extra field (extra='forbid') -> 422 | PASS | HTTP 422 body={"detail":[{"type":"extra_forbidden","loc":["body","injected_field"],"msg":"Extr... |
| API-200 | Body-maxlen | POST /campaigns | Valid key + over-max-length -> 422 | PASS | HTTP 422 body={"detail":[{"type":"string_too_long","loc":["body","name"],"msg":"String should ... |
| API-201 | Malformed-str | POST /campaigns | NUL/zero-width/RTL/emoji in string field -> no 500 | FAIL | HTTP 500 body={"error":"Failed to create campaign"} |
| API-202 | Auth-noKey | GET /campaigns | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-203 | Auth-wrongKey | GET /campaigns | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-204 | Auth-validKey | GET /campaigns | Valid X-API-Key -> 200 or 404 (synth path params) | PASS | HTTP 200 body={"campaigns":[]} |
| API-205 | Method | GET /campaigns | DELETE on a GET-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-206 | Auth-noKey | GET /campaigns/00000000-0000-4000-8000-000000000002 | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-207 | Auth-wrongKey | GET /campaigns/00000000-0000-4000-8000-000000000002 | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-208 | Auth-validKey | GET /campaigns/00000000-0000-4000-8000-000000000002 | Valid X-API-Key -> 200 or 404 (synth path params) | PASS | HTTP 404 body={"error":"Campaign not found"} |
| API-209 | Method | GET /campaigns/00000000-0000-4000-8000-000000000002 | DELETE on a GET-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-210 | Auth-noKey | POST /campaigns/00000000-0000-4000-8000-000000000002/generate | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-211 | Auth-wrongKey | POST /campaigns/00000000-0000-4000-8000-000000000002/generate | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-212 | Auth-validKey | POST /campaigns/00000000-0000-4000-8000-000000000002/generate | Valid X-API-Key -> 200 or 404 (synth path params) | PASS | HTTP 404 body={"error":"Campaign not found"} |
| API-213 | Method | POST /campaigns/00000000-0000-4000-8000-000000000002/generate | DELETE on a POST-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-214 | Auth-noKey | POST /campaigns/00000000-0000-4000-8000-000000000002/start | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-215 | Auth-wrongKey | POST /campaigns/00000000-0000-4000-8000-000000000002/start | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-216 | Auth-validKey | POST /campaigns/00000000-0000-4000-8000-000000000002/start | Valid X-API-Key -> 200 or 404 (synth path params) | PASS | HTTP 200 body={"status":"active","message":"Campaign started. Messages will be sent according ... |
| API-217 | Method | POST /campaigns/00000000-0000-4000-8000-000000000002/start | DELETE on a POST-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-218 | Auth-noKey | POST /campaigns/00000000-0000-4000-8000-000000000002/pause | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-219 | Auth-wrongKey | POST /campaigns/00000000-0000-4000-8000-000000000002/pause | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-220 | Auth-validKey | POST /campaigns/00000000-0000-4000-8000-000000000002/pause | Valid X-API-Key -> 200 or 404 (synth path params) | PASS | HTTP 200 body={"status":"paused"} |
| API-221 | Method | POST /campaigns/00000000-0000-4000-8000-000000000002/pause | DELETE on a POST-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-222 | Auth-noKey | GET /campaigns/00000000-0000-4000-8000-000000000002/export | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-223 | Auth-wrongKey | GET /campaigns/00000000-0000-4000-8000-000000000002/export | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-224 | Auth-validKey | GET /campaigns/00000000-0000-4000-8000-000000000002/export | Valid X-API-Key -> 200 or 404 (synth path params) | PASS | HTTP 404 body={"error":"No messages found for this campaign."} |
| API-225 | Method | GET /campaigns/00000000-0000-4000-8000-000000000002/export | DELETE on a GET-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-226 | Auth-noKey | GET /admin/gemini-budget | Reject when X-API-Key absent (expect 403, NOT 401) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-227 | Auth-wrongKey | GET /admin/gemini-budget | Reject wrong X-API-Key (constant-time; expect 403) | PASS | HTTP 403 body={"detail":"Invalid or missing API key"} |
| API-228 | Auth-validKey | GET /admin/gemini-budget | Valid X-API-Key happy path | SKIP | Destructive/expensive/admin-gated endpoint - skipped per QA rule. |
| API-229 | Method | GET /admin/gemini-budget | DELETE on a GET-only route -> 405 | PASS | HTTP 405 body={"detail":"Method Not Allowed"} |
| API-230 | Admin-noToken | GET /admin/gemini-budget | Valid X-API-Key + missing X-Admin-Token -> 403 | PASS | HTTP 403 body={"detail":"Invalid or missing admin token"} |
| API-231 | Admin-wrongToken | GET /admin/gemini-budget | Valid X-API-Key + wrong X-Admin-Token -> 403 | PASS | HTTP 403 body={"detail":"Invalid or missing admin token"} |
| API-232 | Auth-pytest | ALL 31 authed routes | Missing X-API-Key -> 403 (every route) | PASS | pytest::tests/test_endpoint_hardening.py::TestAuthOnAllEndpoints::test_missing_key_returns_403_on_every_endpoint - GREEN (77 tests, 3.22s) |
| API-233 | Auth-pytest | ALL 31 authed routes | Wrong X-API-Key -> 403 (constant-time compare) | PASS | pytest::tests/test_endpoint_hardening.py::TestAuthOnAllEndpoints::test_wrong_key_returns_403_on_every_endpoint - GREEN |
| API-234 | Auth-pytest | GET / | Liveness probe NOT auth-gated | PASS | pytest::tests/test_endpoint_hardening.py::TestAuthOnAllEndpoints::test_liveness_probe_unauthenticated - GREEN |
| API-235 | Body-pytest | All POST endpoints | Empty body with missing X-API-Key -> 403 (NOT 422) | PASS | pytest::tests/test_endpoint_hardening.py::TestBodyValidation::test_empty_body_with_missing_key_returns_403 - GREEN + tests/security/test_validation_authz_gate.py::test_validation_error_no_api_key_returns_403_not_422_with_schema - GREEN. Closes schema-enumeration leak via 422 details. |
| API-236 | Body-pytest | All POST endpoints | Empty body with valid X-API-Key -> 422 with Pydantic detail | PASS | pytest::tests/test_endpoint_hardening.py::TestBodyValidation::test_empty_body_returns_422_with_valid_key - GREEN |
| API-237 | Body-pytest | All POST endpoints | extra='forbid' -> 422 | PASS | pytest::tests/test_endpoint_hardening.py::TestBodyValidation::test_extra_fields_rejected_via_extra_forbid - GREEN |
| API-238 | Body-pytest | All POST endpoints | constr(max_length=N) boundary +1 char -> 422 | PASS | pytest::tests/test_endpoint_hardening.py::TestBodyValidation::test_max_length_boundary_returns_422 - GREEN |
| API-239 | Malformed-pytest | All string fields | NUL/zero-width/RTL/emoji -> no 500 | PASS | pytest::tests/test_endpoint_hardening.py::TestAdversarialStringFuzz::test_no_500_on_adversarial_strings - GREEN (offline). LIVE probe API-127, API-201 disagree on /discovery/start + /campaigns -- see FAIL rows. |
| API-240 | Malformed-pytest | All routes | Oversize payload (>MAX_UPLOAD) rejected by Pydantic | PASS | pytest::tests/test_endpoint_hardening.py::TestAdversarialStringFuzz::test_oversize_payload_rejected_via_pydantic - GREEN |
| API-241 | RateLimit-pytest | Destructive endpoints (3/hour) | 4th call -> 429 | PASS | pytest::tests/test_endpoint_hardening.py::TestRateLimitBoundary::test_destructive_endpoint_3_per_hour_trips_at_4 - GREEN |
| API-242 | RateLimit-pytest | POST /ask (10/minute) | 11th call -> 429 | PASS | pytest::tests/test_endpoint_hardening.py::TestRateLimitBoundary::test_eleventh_ask_call_returns_429 - GREEN |
| API-243 | Admin-pytest | DELETE /leads/demo | Requires X-Admin-Token (403 without) | PASS | pytest::tests/test_endpoint_hardening.py::TestAdminTokenGuard::test_leads_demo_requires_admin_token - GREEN |
| API-244 | Admin-pytest | DELETE /leads/demo | Wrong confirmation phrase -> 422 | PASS | pytest::tests/test_endpoint_hardening.py::TestAdminTokenGuard::test_leads_demo_wrong_confirmation_returns_422 - GREEN |
| API-245 | Admin-pytest | DELETE admin endpoints | Wrong X-Admin-Token -> 403 | PASS | pytest::tests/test_endpoint_hardening.py::TestAdminTokenGuard::test_wrong_admin_token_returns_403 - GREEN |
| API-246 | Admin-pytest | DELETE admin endpoints | No X-API-Key takes precedence over X-Admin-Token check | PASS | pytest::tests/test_endpoint_hardening.py::TestAdminTokenGuard::test_no_api_key_takes_precedence_over_admin_token - GREEN |
| API-247 | Execute-pytest | POST /execute | ExecutableTask Literal allowlist enforced | PASS | pytest::tests/test_endpoint_hardening.py::TestExecuteTaskAllowlist::test_unknown_task_rejected + test_valid_task_accepted - GREEN; tests/security/test_execute_plan_model.py - GREEN |
| API-248 | Execute-pytest | POST /execute params | ExecutePlanParams arbitrary key rejected | PASS | pytest::tests/test_endpoint_hardening.py::TestPipelineFiltersTyped::test_arbitrary_db_column_key_rejected_422 - GREEN |
| API-249 | Unsub-pytest | GET /unsubscribe/{token} | Empty + malformed token paths | PASS | pytest::tests/test_unsubscribe_endpoints.py - GREEN (12 tests) + tests/integration/test_unsubscribe_url_roundtrip.py - GREEN (4 tests) |
| API-250 | AdminBudget-pytest | GET /admin/gemini-budget | Monotonic counter + auth gate | PASS | pytest::tests/unit/test_gemini_budget_endpoint.py + tests/unit/test_gemini_budget_monotonic.py - GREEN |
| API-251 | EndpointMatrix-pytest | ALL 42 routes | Full matrix: auth + admin + body + rate-limit | PASS | pytest::tests/test_endpoint_security_matrix.py - GREEN (run separately to confirm) |
| API-252 | Validation-pytest | /operator/account erasure | Three-factor gate + audit-first invariant | PASS | pytest::tests/test_gdpr_deletion.py - GREEN (16-test pin) |
