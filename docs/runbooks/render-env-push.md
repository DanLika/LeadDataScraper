# Runbook: Push Phase 14+15 env vars to Render

Operator recipe for `scripts/render_env_push.sh`. Use before the first dogfood batch deploy after Phase 14+15 merge. Idempotent — re-running with same values is a no-op on Render (no version bump, no redeploy trigger).

## Prerequisites

- `RENDER_API_KEY` exported in your shell (`~/.zshenv`)
- `~/.bookbed-secrets` exists at `chmod 600` (script will refuse otherwise)
- Render service ID: `srv-d89bisbbc2fs73f1pjpg` (lead-scraper-backend); script default — override with `SERVICE_ID=...` env if needed
- Auto-deploy preferably toggled OFF in Render dashboard during push (avoid 7 redeploys; trigger one manual deploy at end)

## The 7 required keys

| Key | Source | Notes |
|-----|--------|-------|
| `INSTANTLY_API_KEY` | Instantly dashboard → Settings → API | |
| `INSTANTLY_DEFAULT_CAMPAIGN_ID` | Instantly dashboard → Campaigns → cold-outreach UUID | |
| `INSTANTLY_WEBHOOK_SIGNING_SECRET` | `openssl rand -hex 32` | 64-char hex |
| `UNSUBSCRIBE_TOKEN_SECRET` | `openssl rand -hex 32` | 64-char hex |
| `UNSUBSCRIBE_BASE_URL` | `https://lead-scraper-backend.onrender.com` | **Backend host, NOT frontend** — handler is a FastAPI route |
| `OPERATOR_SIGNATURE` | Operator's email signature, multi-line | See "Multi-line signature gotcha" below |
| `SEND_WINDOW_DEFAULT_TZ` | `Europe/Sarajevo` | IANA name; Phase 15 spec |

## Step 1 — Populate `~/.bookbed-secrets`

```bash
chmod 600 ~/.bookbed-secrets
cat >> ~/.bookbed-secrets <<'EOF'
INSTANTLY_API_KEY=<paste from Instantly>
INSTANTLY_DEFAULT_CAMPAIGN_ID=<paste campaign UUID>
UNSUBSCRIBE_BASE_URL=https://lead-scraper-backend.onrender.com
SEND_WINDOW_DEFAULT_TZ=Europe/Sarajevo
EOF
```

### Multi-line signature gotcha

POSIX `source` of `KEY=value` lines cannot expand `\n` literals. Use ANSI-C bash quoting:

```bash
echo "OPERATOR_SIGNATURE=\$'Dusko\\nLeadDataScraper\\nhttps://leaddatascraper.com'" >> ~/.bookbed-secrets
```

Or write the file with `cat`+heredoc:

```bash
cat >> ~/.bookbed-secrets <<EOF
OPERATOR_SIGNATURE="Dusko
LeadDataScraper
https://leaddatascraper.com"
EOF
```

The script's `set -a; source` will load it correctly. `jq -nc --arg val "$v"` inside the script escapes real newlines to `\n` for Render's JSON API.

## Step 2 — Pre-flight only (no push)

Run the script and answer `N` at the prompt:

```bash
bash scripts/render_env_push.sh
# Preflight (srv-d89bisbbc2fs73f1pjpg):
#   ✅ INSTANTLY_API_KEY ready (XX chars)
#   ✅ INSTANTLY_DEFAULT_CAMPAIGN_ID ready (36 chars)
#   ✅ INSTANTLY_WEBHOOK_SIGNING_SECRET ready (64 chars)
#   ✅ UNSUBSCRIBE_TOKEN_SECRET ready (64 chars)
#   ✅ UNSUBSCRIBE_BASE_URL ready (45 chars)
#   ✅ OPERATOR_SIGNATURE ready (XX chars)
#   ✅ SEND_WINDOW_DEFAULT_TZ ready (15 chars)
#
# Push these 7 vars to srv-d89bisbbc2fs73f1pjpg? [y/N] N
# aborted
```

If any key shows `❌ missing` or `❌ placeholder`, fix `~/.bookbed-secrets` and re-run.

### Manual spot-checks worth running before push

```bash
set -a; source ~/.bookbed-secrets; set +a

# Confirm backend host, not frontend
echo "$UNSUBSCRIBE_BASE_URL" | grep -E "lead-scraper-backend.*onrender.com" \
  || echo "⚠️ wrong host — must be FastAPI backend, not Next.js"

# Confirm IANA timezone resolves
python3 -c "from zoneinfo import ZoneInfo; ZoneInfo('$SEND_WINDOW_DEFAULT_TZ'); print('valid')"

# Confirm Render API reachable + service exists
curl -s -o /dev/null -w "%{http_code}\n" \
  -H "Authorization: Bearer $RENDER_API_KEY" \
  "https://api.render.com/v1/services/srv-d89bisbbc2fs73f1pjpg"
# expect: 200
```

## Step 3 — Push

```bash
bash scripts/render_env_push.sh
# ... preflight ...
# Push these 7 vars to srv-d89bisbbc2fs73f1pjpg? [y/N] y
#   → INSTANTLY_API_KEY: HTTP 200
#   → INSTANTLY_DEFAULT_CAMPAIGN_ID: HTTP 200
#   → INSTANTLY_WEBHOOK_SIGNING_SECRET: HTTP 200
#   → UNSUBSCRIBE_TOKEN_SECRET: HTTP 200
#   → UNSUBSCRIBE_BASE_URL: HTTP 200
#   → OPERATOR_SIGNATURE: HTTP 200
#   → SEND_WINDOW_DEFAULT_TZ: HTTP 200
#
# All 7 env vars pushed.
```

Each PUT is a per-key upsert (`PUT /v1/services/{id}/env-vars/{key}`) — does NOT touch any other existing env vars on the service. If auto-deploy is OFF, the service does not redeploy yet.

### If a PUT returns non-2xx

Script exits 1 on the first non-200/201 and prints the response body. Common causes:
- 401: `RENDER_API_KEY` expired or wrong → rotate, see `docs/secret-inventory.md`
- 404: service ID wrong → check Render dashboard URL
- 422: value validation failed (e.g. empty string after trim) → fix `~/.bookbed-secrets`

## Step 4 — Trigger deploy

Render dashboard → service → **Manual Deploy** → **Deploy latest commit**. Or via API:

```bash
curl -s -X POST \
  -H "Authorization: Bearer $RENDER_API_KEY" \
  "https://api.render.com/v1/services/srv-d89bisbbc2fs73f1pjpg/deploys" \
  -H "Content-Type: application/json" -d '{"clearCache":"do_not_clear"}'
```

Watch deploy logs in dashboard or:

```bash
curl -s -H "Authorization: Bearer $RENDER_API_KEY" \
  "https://api.render.com/v1/services/srv-d89bisbbc2fs73f1pjpg/deploys?limit=1" | jq .
```

## Step 5 — Post-deploy smoke

Before queuing the first dogfood batch:

1. `curl https://lead-scraper-backend.onrender.com/` → `{"status":"ok"}`
2. Send one test outreach email through the Phase 15 dispatcher
3. Click the unsubscribe link in the test email — should hit `UNSUBSCRIBE_BASE_URL/unsubscribe/<token>` and return success page
4. Confirm the suppressed address appears in `suppressions` table (PostgREST or Studio)
5. Confirm a `webhook_events` row from Instantly was inserted by the Resend/Instantly webhook callback (HMAC verified against `INSTANTLY_WEBHOOK_SIGNING_SECRET`)

Only after all 5 pass: queue the dogfood batch.

## Rollback

If a bad env value broke prod:
1. Set the previous value via Render dashboard (Settings → Environment) — fastest path
2. Or PUT the old value via API:
   ```bash
   curl -X PUT -H "Authorization: Bearer $RENDER_API_KEY" \
     -H "Content-Type: application/json" \
     "https://api.render.com/v1/services/srv-d89bisbbc2fs73f1pjpg/env-vars/INSTANTLY_API_KEY" \
     -d '{"value":"<old-value>"}'
   ```
3. Render will redeploy automatically if auto-deploy was re-enabled

## Related

- Script: [`scripts/render_env_push.sh`](../../scripts/render_env_push.sh)
- Secret rotation: [`docs/secret-inventory.md`](../secret-inventory.md)
- Phase 15 dispatch invariants: memory `phase_15_dispatch_tick.md`
- Last pre-flight session: [`docs/sessions/session_2026-05-26_phase14-15-readiness.md`](../sessions/session_2026-05-26_phase14-15-readiness.md)
