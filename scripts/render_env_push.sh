#!/usr/bin/env bash
#
# Push the Phase 14+15 required env-var set to the LDS backend service on
# Render. Reads values from ~/.bookbed-secrets (chmod 600), preflights
# every key for presence + non-placeholder, then PUTs each one via the
# per-key endpoint (idempotent upsert; does NOT touch existing vars).
#
# Render API reference:
#   PUT /v1/services/{serviceId}/env-vars/{key}  ← per-key upsert
#   PUT /v1/services/{serviceId}/env-vars        ← whole-set replace (AVOID)
#
# Phase 14+15 required set sourced from #329 ("Phase 14+15 stack handoff"):
#   INSTANTLY_API_KEY                — Instantly dashboard → Settings → API
#   INSTANTLY_DEFAULT_CAMPAIGN_ID    — cold-outreach campaign UUID
#   INSTANTLY_WEBHOOK_SIGNING_SECRET — `openssl rand -hex 32`
#   UNSUBSCRIBE_TOKEN_SECRET         — `openssl rand -hex 32`
#   UNSUBSCRIBE_BASE_URL             — backend host (handler is FastAPI route)
#   OPERATOR_SIGNATURE               — multi-line email signature
#   SEND_WINDOW_DEFAULT_TZ           — `Europe/Sarajevo` per Phase 15 spec
#
# Usage:
#   1. Fill ~/.bookbed-secrets with `KEY=value` lines for each required key.
#      `INSTANTLY_WEBHOOK_SIGNING_SECRET` + `UNSUBSCRIBE_TOKEN_SECRET` were
#      pre-populated during the 2026-05-26 redeploy prep.
#   2. Optionally toggle Render auto-deploy OFF first to avoid 7 deploys.
#   3. Run: ./scripts/render_env_push.sh
#   4. Trigger a single manual deploy after the push completes.
#
# Pre-existing service env vars are NOT modified — per-key PUT is upsert
# only. Re-running the script with the same values is a no-op on Render's
# side (same value → no version bump → no redeploy trigger).

set -euo pipefail

: "${RENDER_API_KEY:?must be set (e.g. via ~/.zshenv export)}"

SERVICE_ID="${SERVICE_ID:-srv-d89bisbbc2fs73f1pjpg}"  # lead-scraper-backend
SECRETS_FILE="${SECRETS_FILE:-${HOME}/.bookbed-secrets}"

if [ ! -f "$SECRETS_FILE" ]; then
  echo "❌ secrets file not found: $SECRETS_FILE"
  echo "   Create with: chmod 600 + KEY=value lines"
  exit 1
fi

# Make every KEY=value in the secrets file available as a shell var.
# shellcheck disable=SC1090
set -a; source "$SECRETS_FILE"; set +a

REQUIRED=(
  INSTANTLY_API_KEY
  INSTANTLY_DEFAULT_CAMPAIGN_ID
  INSTANTLY_WEBHOOK_SIGNING_SECRET
  UNSUBSCRIBE_TOKEN_SECRET
  UNSUBSCRIBE_BASE_URL
  OPERATOR_SIGNATURE
  SEND_WINDOW_DEFAULT_TZ
)

echo "Preflight ($SERVICE_ID):"
preflight_failed=0
for k in "${REQUIRED[@]}"; do
  v="${!k:-}"
  if [ -z "$v" ]; then
    echo "  ❌ $k missing"
    preflight_failed=1
  elif [[ "$v" == "..." ]] || [[ "$v" == "<"*">" ]]; then
    echo "  ❌ $k still has placeholder value: ${v}"
    preflight_failed=1
  else
    # Show length only, never the value
    echo "  ✅ $k ready (${#v} chars)"
  fi
done

if [ "$preflight_failed" -ne 0 ]; then
  echo
  echo "Fill the missing/placeholder vars in $SECRETS_FILE and re-run."
  exit 1
fi

echo
read -rp "Push these ${#REQUIRED[@]} vars to $SERVICE_ID? [y/N] " ok
[[ "$ok" == "y" || "$ok" == "Y" ]] || { echo "aborted"; exit 0; }

echo
for k in "${REQUIRED[@]}"; do
  v="${!k}"
  resp=$(curl -sw $'\n%{http_code}' -X PUT \
    -H "Authorization: Bearer $RENDER_API_KEY" \
    -H "Content-Type: application/json" \
    "https://api.render.com/v1/services/${SERVICE_ID}/env-vars/${k}" \
    -d "$(jq -nc --arg val "$v" '{value:$val}')")
  code=$(printf '%s\n' "$resp" | tail -n1)
  case "$code" in
    200|201) echo "  → $k: HTTP $code" ;;
    *)
      echo "  ✗ $k: HTTP $code"
      printf '%s\n' "$resp" | sed '$d'  # body (drop trailing status line)
      exit 1
      ;;
  esac
done

echo
echo "All ${#REQUIRED[@]} env vars pushed."
echo "Next: confirm Render service status, then trigger manual deploy if"
echo "auto-deploy is OFF. Smoke-test sending one email + clicking the"
echo "unsubscribe link before queuing the dogfood batch."
