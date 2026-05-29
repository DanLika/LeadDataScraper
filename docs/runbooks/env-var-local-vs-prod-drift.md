# Local `.env` vs Render prod drift

**Status**: UNRESOLVED. Operator action pending — choose rotate-prod-to-match
or update-local-to-match. Until then every QA recipe that trusts local
`.env` `API_SECRET_KEY` will spurious-FAIL against prod.

## Symptom

Live QA terminal-6 backend probe against
`https://lead-scraper-backend-x51l.onrender.com` (2026-05-28):

Every call with `X-API-Key: $API_SECRET_KEY` (from local `.env`) returns
**403** with body `{"detail": "Invalid API key"}`. 58 spurious FAILs in the
matrix.

Local `.env` inventory (lengths via shell `${#VAR}`):

```
API_SECRET_KEY:                   PRESENT len=128
ADMIN_TOKEN:                      PRESENT len=32
INSTANTLY_WEBHOOK_SIGNING_SECRET: PRESENT len=64
```

Prod Render env (pulled via Management API):

```
API_SECRET_KEY:                   PRESENT len=64   match_local=FALSE
ADMIN_TOKEN:                      PRESENT len=32   match_local=TRUE
INSTANTLY_WEBHOOK_SIGNING_SECRET: PRESENT len=64   match_local=TRUE
```

Only `API_SECRET_KEY` diverges. 128-char local key is probably a historic
`secrets.token_hex(64)` (yields 128-char hex) that was shortened to
`secrets.token_hex(32)` (64-char) on a previous rotation. Quarterly rotation
per `docs/secret-inventory.md` cadence updated Render but not the local `.env`.

## Root cause

Manual rotation workflow has no enforcement that local `.env` mirrors
remote-only secret stores (Render, GitHub Actions, Vercel). Operator updated
Render via dashboard / API, forgot to sync local file.

## Fix recipe (transient, no leak)

When running ANY live-prod probe, do NOT trust local `.env` `API_SECRET_KEY`
blindly. Pull prod into a shell var, use it, unset.

Backend service ID: `srv-d89bisbbc2fs73f1pjpg`. Owner ID:
`tea-d89bdph9rddc7394se1g`.

```bash
# Requires RENDER_API_KEY in environment (Personal Access Token, NOT service ID)
PROD_API_KEY=$(curl -sS \
  -H "Authorization: Bearer $RENDER_API_KEY" \
  "https://api.render.com/v1/services/srv-d89bisbbc2fs73f1pjpg/env-vars" \
  | python3 -c 'import json,sys
d = json.load(sys.stdin)
print([x["envVar"]["value"] for x in d if x["envVar"]["key"] == "API_SECRET_KEY"][0], end="")')

# Use it for the probe
curl -sS -H "X-API-Key: $PROD_API_KEY" \
  https://lead-scraper-backend-x51l.onrender.com/stats

# Unset before next prompt to avoid history echo
unset PROD_API_KEY
```

**Do NOT** `echo $PROD_API_KEY`, redirect into a file, or persist in shell
history. The Personal Access Token (`RENDER_API_KEY`) has account-wide
read/write on env vars — treat it as a session secret.

## Permanent fix (operator action)

One of two paths:

**Path A — sync local to prod** (lowest risk):

```bash
# In local .env, replace API_SECRET_KEY line with prod value
# (acquire prod value via the recipe above)
# Verify: curl -sS -H "X-API-Key: $(grep ^API_SECRET_KEY .env | cut -d= -f2-)" \
#         https://lead-scraper-backend-x51l.onrender.com/stats
# Expected: 200 OK
```

**Path B — rotate both** (recommended every quarter per `docs/secret-inventory.md`):

```bash
NEW_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
# 1. Set on Render via Management API
curl -sS -X PUT \
  -H "Authorization: Bearer $RENDER_API_KEY" \
  -H "Content-Type: application/json" \
  "https://api.render.com/v1/services/srv-d89bisbbc2fs73f1pjpg/env-vars" \
  -d "[{\"envVar\":{\"key\":\"API_SECRET_KEY\",\"value\":\"$NEW_KEY\"}}]"
# 2. Update local .env (manual edit, replace API_SECRET_KEY line)
# 3. Wait for Render auto-redeploy (~2 min)
# 4. Verify both sides match
unset NEW_KEY
```

## Recurrence guard

- **`docs/secret-inventory.md`** — already documents rotation cadence. Add a
  per-secret checklist column "local `.env` mirrored?" with a Y/N column.
- **`make verify-env`** target (not yet wired) — would compare local `.env`
  key fingerprints (SHA256 first 8 chars) against Render env-vars. Surface
  drifted keys without printing values.
- **Reminder cron** (not yet wired) — monthly Discord ping listing
  service IDs + last-rotated date per secret per env-source. Pull from
  `docs/secret-inventory.md` + Render Management API.

## Related secrets to audit for similar drift

Pattern likely repeats on other manually-rotated keys:

- `SUPABASE_SERVICE_ROLE_KEY` — see
  [reminder-supabase-key-rotation memory](./README.md#secret-rotation)
- `GEMINI_API_KEY`
- `ADMIN_TOKEN` (length match this time, but content could still differ)

Diff recipe (no values printed):

```bash
# Compare prod vs local fingerprints
for KEY in API_SECRET_KEY ADMIN_TOKEN SUPABASE_SERVICE_ROLE_KEY GEMINI_API_KEY; do
  LOCAL_FP=$(grep "^$KEY=" .env | cut -d= -f2- | python3 -c \
    'import sys,hashlib; print(hashlib.sha256(sys.stdin.read().strip().encode()).hexdigest()[:8])')
  PROD_FP=$(curl -sS -H "Authorization: Bearer $RENDER_API_KEY" \
    "https://api.render.com/v1/services/srv-d89bisbbc2fs73f1pjpg/env-vars" \
    | python3 -c "import json,sys,hashlib
d=json.load(sys.stdin)
v=[x['envVar']['value'] for x in d if x['envVar']['key']=='$KEY']
print(hashlib.sha256(v[0].encode()).hexdigest()[:8] if v else 'ABSENT')")
  echo "$KEY  local=$LOCAL_FP  prod=$PROD_FP  match=$([ "$LOCAL_FP" = "$PROD_FP" ] && echo YES || echo NO)"
done
```

## Related

- Memory: `bug_local_env_api_key_stale_2026-05-28.md`,
  `reminder_supabase_key_rotation.md`
- Docs: `docs/secret-inventory.md`
- Code: `backend/main.py:verify_api_key`
