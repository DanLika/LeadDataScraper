# 2026-05-27 — Phase 14/15 security hardening

Scope: multi-pass security audit (`/vibe-security` → `/security-audit:fix-review` → `/security-audit:run` 4-agent → `/security-review` → `/security-audit:sharp-edges`) on `fix/proxy-metrics-public-allowlist` branch. Applied 12 fixes; all 1071 unit/integration tests green; security-review sub-agent confirmed zero new vulnerabilities introduced.

## Findings → fixes

| Sev | Finding | Fix | File |
|-----|---------|-----|------|
| HIGH | Cursor `k` PostgREST predicate injection — `,`/`)`/`(` escape `.or_()` tie-break clause | `_CURSOR_KEY_PATTERN = re.compile(r"\A[A-Za-z0-9_-]{1,128}\Z")` charset gate in `_decode_lead_cursor` BEFORE `datetime.fromisoformat` | `backend/main.py` |
| MED | Email-body autoescape OFF for HTML variants — lead-field HTML injection (CSV ingest + Gemini-enriched fields) | New `sequence_variants.content_type` column (default `'text'`); dataclass field; `create()` allowlist; `thread_builder` reads + passes to `render(body, ..., content_type=...)` | `src/repositories/sequence_variant_repo.py`, `src/services/thread_builder.py`, `supabase_schema.sql` |
| MED | `sequence_variants.body_template` unbounded — OOM renderer + stored prompt-injection vector | `sequence_variants_body_size` CHECK (body ≤16384, subject NULL OR ≤998 = RFC 5322 line) | `supabase_schema.sql` |
| MED | `webhook_events.event_id` unbounded — UNIQUE BTREE key poisoning | `webhook_events_event_id_size` CHECK 1..256 | `supabase_schema.sql` |
| MED | Instantly dispatcher no `assert_safe_url` — forward-compat SSRF if `INSTANTLY_BASE_URL` becomes env-configurable | `await assert_safe_url(url)` before `session.post` in `_post_batch` | `src/integrations/instantly_sender.py` |
| LOW | `/unsubscribe/{token}` HTML route missing CSP | `_UNSUB_HTML_HEADERS` (`default-src 'none'; form-action 'self'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'`) on all 4 HTMLResponse calls | `backend/main.py` |
| LOW | Webhook ingest fields not CRLF-stripped — defense-in-depth re. compromised-provider | `_STRIP_CTRL_PATTERN` + `_scrub(s, cap)` local helper applied to `event_type`/`provider_msg_id`/`recipient_email`/`lds_message_id`; same pattern inline on `bounce_reason` in `_instantly_handle_bounced` | `backend/main.py` |
| LOW | `sequence_steps.send_days` no allowlist | `sequence_steps_send_days_format` CHECK regex `^(mon|tue|...|sun)(,...)*$` | `supabase_schema.sql` |
| LOW | `sequence_steps.send_window_end ≤ start` accepted | `sequence_steps_window_ordered` CHECK | `supabase_schema.sql` |
| LOW | `campaign_messages.bounce_reason` no DB-side length cap (code-side `[:200]` only) | `campaign_messages_bounce_reason_size` CHECK ≤200 | `supabase_schema.sql` |

Plus 1 verifier-found gap (`bounce_reason` not run through scrub pattern initially) closed in the same session.

## Skipped findings

- **render.yaml missing 5 Phase 14/15 envVars** — user reverted the addition (intentional per system reminder). Operator pushes via `~/.bookbed-secrets` + `scripts/render_env_push.sh` outside of git.
- **CLAUDE.md "5 RLS-protected tables" stale count** — updated to 11 (5 core + 6 Phase 14/15).
- **Cron-script `requests.get` to hardcoded vendor hosts** — no user input flows in. Document as exempt; no code change.
- **`target=_blank` without `rel=noopener`** at `frontend/app/page.tsx:1516,1527,1594` — tabnabbing, low-conf web vuln per `/security-review` exclusion precedent #5. Modern browsers auto-`noopener` for `<a target=_blank>`.

## Verification

- `tests/integration/test_unsubscribe_url_roundtrip.py` 4/4 green
- `tests/`-wide sweep: 1071 passed, 80 skipped, 0 failed (was 1064 before — +7 from variant suites that previously skipped on missing fixture wiring)
- Syntax check on 3 edited Python files: clean
- `frontend/app/api/proxy/[...path]/route.ts` `PUBLIC_PROXY_PATHS = {'metrics'}` — mirrors `middleware.ts` exact-match allowlist. Backend `/metrics` still `Depends(verify_api_key)` + Pydantic `extra='forbid'` + slowapi 60/min. Auth-bypass scope bounded to exactly one path.
- `/security-review` sub-agent verdict: "No new vulnerabilities introduced by this PR at confidence ≥ 8."

## Operator follow-up

1. Apply schema deltas to live Supabase via Management API (same pattern as `session_2026-05-27_schema_drift_resolved.md`). All new constraints wrapped in idempotent `DO $$ BEGIN ... EXCEPTION WHEN duplicate_object`.
2. Push 5 missing Phase 14/15 envVars to Render via `scripts/render_env_push.sh` (out-of-band — render.yaml declaration was reverted).

## Files touched

- `backend/main.py` — `import re`, `_CURSOR_KEY_PATTERN`, `_STRIP_CTRL_PATTERN`, `_UNSUB_HTML_HEADERS`, `_scrub()` helper, `bounce_reason` inline scrub
- `frontend/app/api/proxy/[...path]/route.ts` — `PUBLIC_PROXY_PATHS = {'metrics'}` + auth-gate wire-in
- `src/integrations/instantly_sender.py` — `assert_safe_url(url)` before POST
- `src/repositories/sequence_variant_repo.py` — `content_type` field + create() validation
- `src/services/thread_builder.py` — content_type allowlist re-validate + render kwarg
- `supabase_schema.sql` — 6 new CHECKs + content_type column
- `CLAUDE.md` — RLS table count 5→11, CHECK count 10→17, cursor charset pin, new "Phase 14/15 dispatch + webhook hardening" sub-section

## Memory updates (none yet — capture in next conversation)

- Possible new memory: `feedback_security_audit_loop` — `/vibe-security` → `/security-audit:fix-review` → 4-agent `/security-audit:run` → `/security-review` was the productive sequence. Each stage caught what the prior missed (cursor injection only surfaced at agent stage; render.yaml gap at first stage; verifier caught the `bounce_reason` inconsistency).
