# Webhook signature + replay-window security

How LDS verifies inbound webhooks from Instantly (and, in subsequent
phases, Resend + HeyReach). The shared primitives live in
[`src/utils/webhook_security.py`](../../src/utils/webhook_security.py);
each provider's handler stacks them in the same order:

1. Read raw body bytes (NOT `await request.json()` — providers HMAC
   the literal bytes, and a roundtrip through `json.loads` can change
   key order or whitespace).
2. Verify HMAC-SHA256 against the provider-issued signing secret.
3. Verify the timestamp header sits inside `±5 min` of the server clock.
4. INSERT into `webhook_events` (idempotent on `(provider, event_id)`).
5. Dispatch event-type-specific side effects in a `BackgroundTask`.

Every verification failure returns `401` with `{"detail": "webhook
verification failed"}` — a generic body. The handler MUST NOT signal
which check failed (sig vs timestamp vs missing header), because that
gives an attacker a probe oracle to refine forged requests.

## Why HMAC

| Choice | Why we picked it |
|---|---|
| `hmac.compare_digest` | Constant-time comparison — no side-channel leak. Standard library. |
| SHA-256 | Matches Instantly, Resend, GitHub, Stripe, Shopify conventions. |
| Raw body bytes, not JSON | Provider HMACs the bytes; re-encoding loses key order + whitespace. |
| Empty secret → `RuntimeError` | Operator misconfig fails loud at the call site, not silently accepts every request. |
| Empty signature → `MissingSignature` | Distinguishes "no header" from "wrong sig" for log triage (but the response body is uniform). |

## Why timestamp window

A valid HMAC by itself doesn't prove freshness. An attacker who
captures one signed request can replay it forever. The provider
includes a timestamp in a dedicated header; our gate rejects anything
older or newer than ±300s (`DEFAULT_TIMESTAMP_TOLERANCE_SECONDS`).

The symmetric `+/-` window catches both stale captures AND a
maliciously-stamped future request (in case the provider's signing
proof leaks via a different vector).

5 minutes is the Stripe + GitHub convention. Generous enough for
legitimate provider retry backoff, tight enough that a captured
signature has a very short replay shelf-life.

## Why `webhook_events` idempotency

Instantly's docs note retries on any non-2xx, on timeout (>2s
default), and "occasionally" on 2xx delivery. Without idempotency:

- A bounce event arrives → handler INSERTs suppression → returns 200
- Network blip between TLS write and our response → Instantly retries
- Second arrival → handler tries to INSERT same suppression → fails
  on the `suppressions_unique` constraint → 500 → Instantly retries
  again

The `(provider, event_id)` UNIQUE constraint on `webhook_events`
collapses every replay to a single set of side effects. The handler
checks for `code='23505'` and returns 200 with `{"ok": true,
"duplicate": true}` — Instantly stops retrying.

Stored payloads also give us:
- Replay during incident response ("the webhook DID fire; here's the body")
- Sweeper retry path via `idx_webhook_events_unprocessed`
  (`processed_at IS NULL`) for events whose background task crashed

## Event type → state transition matrix

| Instantly event | `campaign_messages.status` | `suppressions` |
|---|---|---|
| `email_sent` | `pending` → `sent` (+ stamp `provider_message_id`, `sent_at`) | — |
| `email_bounced` | → `bounced` (+ `bounce_reason`) | INSERT `reason='bounce_hard'`, `channel='email'`, `source_provider='instantly'` |
| `email_unsubscribed` | → `unsubscribed` | INSERT `reason='unsubscribe'`, `channel='all'`, `source_provider='instantly'` |
| `email_replied` | → `replied` | — (reply-classifier in Phase 16 may extend) |
| _anything else_ | _stored in `webhook_events`, no transition_ | — |

The `channel='all'` distinction on unsubscribes is deliberate: an
unsubscribe expresses "stop everything from this sender", not just
the email channel. A LinkedIn dispatcher (Phase 17.x) checking
suppression by `channel ∈ {'linkedin', 'all'}` will correctly skip
addresses that unsubscribed via email.

## Env vars

| Var | Required | Purpose |
|---|---|---|
| `INSTANTLY_WEBHOOK_SIGNING_SECRET` | yes | HMAC key shared with Instantly. Set in their dashboard + Render env. |
| `INSTANTLY_TIMESTAMP_HEADER` | no (`X-Timestamp` default) | Override if Instantly changes header name. |
| `INSTANTLY_SIGNATURE_HEADER` | no (`X-Signature` default) | Override if Instantly changes header name. |

## Configuring Instantly

1. Instantly dashboard → Settings → Webhooks
2. URL: `https://<your-lds-domain>/webhooks/instantly`
3. Events: enable `email_sent`, `email_bounced`, `email_unsubscribed`, `email_replied`
4. Copy the signing secret → Render env var `INSTANTLY_WEBHOOK_SIGNING_SECRET`
5. Re-deploy backend so the env reload picks up

Verification path:
- Send a test webhook from the Instantly dashboard
- Check Render logs for `instantly webhook` lines
- Confirm `webhook_events` row in Supabase Studio

## Operational gotchas

- **Body size cap = 256 KB.** Instantly events are typically <10 KB; the
  cap defends against a DoS body padded to fill PostgREST quota.
- **Background-task failures stay invisible without a sweeper.**
  `processing_error` captures the cause, but no automated retry runs
  until Phase 14.3+ adds the sweeper. Operator should grep logs for
  `instantly event ... processing failed` until then.
- **Provider `signature_scheme` differs.** Instantly + Resend use
  `sha256=...` prefix; HeyReach uses raw hex. The shared util's
  `signature_scheme=""` kwarg toggles between them.
