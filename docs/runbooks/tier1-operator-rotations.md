# Tier 1 operator credential rotations

Six high-leverage rotations that unblock multiple downstream systems. Each
section is copy-paste ready — dashboard URL, exact shell commands,
verification probe, rollback. Run from `~/git/LeadDataScraper` unless
noted.

After completing any rotation, message the orchestrator with the literal
string `<rotation-name> rotated` (e.g. `api-secret-key rotated`) so
dependent autonomous tasks unblock.

| § | Rotation | Time | Last applied | Unblocks |
|---|----------|------|--------------|----------|
| 1 | `API_SECRET_KEY` | ~10 min | **2026-05-31T07:15Z (live)** | Backend auth |
| 2 | Supabase DB password | ~5 min | — | EXPLAIN harness + 17 security.yml gates |
| 3 | Supabase PAT | ~3 min | — | Mgmt API (#476 schema apply, future audits) |
| 4 | `ANTHROPIC_API_KEY` mint + wire | ~5 min | — | #477 live bench + Phase 16 classifier in prod |
| 5 | Sentry auth token | ~5 min | — | Backend Sentry-API issue query + source-map upload |
| 6 | `RENDER_API_KEY` (dashboard-only — no self-mint API) | ~5 min | — | Render Mgmt-API ops + every other rotation in this doc |

---

## §1 — `API_SECRET_KEY` rotation (priority: high)

### Why

Audit on 2026-05-30 found `NEXT_PUBLIC_API_KEY` in `frontend/.env.local`
with `sha256[:12]=aa1bc60272cd` — **exact match** for the prod backend
`API_SECRET_KEY`. The key has been on every operator machine that ever
loaded that file, and the `NEXT_PUBLIC_*` prefix is Next.js's signal to
inline the value into the browser bundle. **Treat as leaked in the
don't-trust-history sense, even if no incident is currently visible.**

The local copy is dropped at audit time (see step 0); rotation closes
the loop.

### Status

**AUTO-ROTATED 2026-05-31T07:15Z** (see
[[api-secret-key-rotation-2026-05-30]] memory note for deploy IDs +
sha256 prefixes + smoke results). The steps below now apply to any
**future** incident — keep current for the next time a leak surfaces.

### Prerequisites

- `RENDER_API_KEY` in `$RENDER_API_KEY` env (mint at
  <https://dashboard.render.com/u/settings#api-keys> if absent)
- ~10 min uninterrupted (~3 min wall-clock per redeploy + smoke)

Service IDs (verified via `GET /v1/services` 2026-05-30):
- Backend: `srv-d89bisbbc2fs73f1pjpg` (`lead-scraper-backend`)
- Frontend: `srv-d89c246k1jcs73eupnl0` (`lead-scraper-frontend`)

### Two endpoint shapes — pick the SINGLE-KEY one

Render has TWO env-var endpoints. Mis-pick = prod incident:

| Endpoint | Effect | When to use |
|----------|--------|-------------|
| `PUT /v1/services/{id}/env-vars` body `[{key, value}, ...]` | **REPLACES the entire env-var list.** A partial list erases every var not in the body. | Bulk rewrite only — almost never. |
| `PUT /v1/services/{id}/env-vars/{envVarKey}` body `{"value": "..."}` | Updates ONE key. Leaves all other vars untouched. | This rotation. |

A previous draft of this runbook used the bulk endpoint with a 1-item
list — fixed 2026-05-31 after PR #480 review.

### Steps

```sh
# 0. Confirm local drop already happened (audit step):
test -f frontend/.env.local && grep -c '^NEXT_PUBLIC_API_KEY=' frontend/.env.local
# Expected: 0. If 1, re-run audit's drop step before continuing.

# 1. Mint new key (32-byte hex, 64-char string — matches existing format):
NEW_API_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
echo "NEW key len=${#NEW_API_SECRET_KEY} sha256[:12]=$(echo -n "$NEW_API_SECRET_KEY" | shasum -a 256 | cut -c1-12)"
# Save to password manager BEFORE proceeding. If you lose the value
# between mint and PUT, you lock yourself out of every direct backend
# probe until you mint another, PUT, redeploy again.

# 2. Backup current state (sha256 prefix only; do NOT save full value):
mkdir -p /tmp/lds-rotation && cd /tmp/lds-rotation
for SVC in srv-d89bisbbc2fs73f1pjpg srv-d89c246k1jcs73eupnl0; do
  curl -fsS -H "Authorization: Bearer $RENDER_API_KEY" \
    "https://api.render.com/v1/services/$SVC/env-vars?limit=100" \
    > "$SVC-envvars-pre-rotation.json"
  chmod 600 "$SVC-envvars-pre-rotation.json"
  COUNT=$(jq 'length' < "$SVC-envvars-pre-rotation.json")
  echo "$SVC: backed up $COUNT env-vars"
done

# 3. PUT new key to backend (SINGLE-KEY endpoint — leaves other 18 vars
#    untouched):
curl -fsS -X PUT \
  -H "Authorization: Bearer $RENDER_API_KEY" \
  -H 'Content-Type: application/json' \
  -d "{\"value\":\"$NEW_API_SECRET_KEY\"}" \
  'https://api.render.com/v1/services/srv-d89bisbbc2fs73f1pjpg/env-vars/API_SECRET_KEY'

# 4. PUT new key to frontend (proxy injects this server-side):
curl -fsS -X PUT \
  -H "Authorization: Bearer $RENDER_API_KEY" \
  -H 'Content-Type: application/json' \
  -d "{\"value\":\"$NEW_API_SECRET_KEY\"}" \
  'https://api.render.com/v1/services/srv-d89c246k1jcs73eupnl0/env-vars/API_SECRET_KEY'

# 5. SINGLE-KEY env-var PUT does NOT auto-redeploy on Render (verified
#    2026-05-31; bulk env-var PUT DID, per
#    render_cron_deploy_recipe). Trigger explicit redeploys IN
#    PARALLEL to minimise the mismatch window:
for SVC in srv-d89bisbbc2fs73f1pjpg srv-d89c246k1jcs73eupnl0; do
  curl -fsS -X POST \
    -H "Authorization: Bearer $RENDER_API_KEY" \
    -H 'Content-Type: application/json' \
    -d '{"clearCache":"do_not_clear"}' \
    "https://api.render.com/v1/services/$SVC/deploys" &
done
wait

# 6. Wait both deploys to status=live (~2-3 min each). Tail:
watch -n 10 'for SVC in srv-d89bisbbc2fs73f1pjpg srv-d89c246k1jcs73eupnl0; do
  echo "=$SVC="
  curl -fsS -H "Authorization: Bearer $RENDER_API_KEY" \
    "https://api.render.com/v1/services/$SVC/deploys?limit=1" \
    | jq -r ".[].deploy | \"\\(.id) \\(.status) \\(.startedAt)\""
done'
```

### Verification probe

```sh
# Backend direct (requires the new key locally — straight from your
# password manager; the rotation script writes it to a 600-chmod tmp
# file as a fallback, but PM is the source of truth):
NEW_API_SECRET_KEY=$(cat /tmp/lds-rotation/.lds_new_api_secret_key 2>/dev/null || read -rs -p 'paste new key: ' k; echo "$k")
curl -fsS 'https://lead-scraper-backend.onrender.com/leads?limit=1' \
  -H "X-API-Key: $NEW_API_SECRET_KEY" | jq '.[0].unique_key // "no rows"'

# Proxy path (auth required — mint a session per test-results/_auth_method.md):
COOKIE_JAR=/tmp/lds-rotation-cookies
# ... follow auth-mint recipe to populate $COOKIE_JAR ...
curl -fsS -b "$COOKIE_JAR" \
  'https://lead-scraper-frontend.onrender.com/api/proxy/stats' \
  | jq '.totalLeads'
```

### Update local files (after smoke green)

```sh
# 7. Replace the old API_SECRET_KEY in operator-side files:
sed -i.bak.$(date +%Y%m%d) "s|^API_SECRET_KEY=.*|API_SECRET_KEY=$NEW_API_SECRET_KEY|" ~/.bookbed-secrets
# Verify exactly one line matched:
grep -c '^API_SECRET_KEY=' ~/.bookbed-secrets  # → 1

# 8. Clear backup tmp files once value is safely in password manager:
shred -u /tmp/lds-rotation/.lds_new_api_secret_key 2>/dev/null || rm -f /tmp/lds-rotation/.lds_new_api_secret_key
```

### Rollback

If post-rotation smoke 401s consistently:

```sh
# Re-PUT the OLD value (from password manager or scrollback) using
# the same SINGLE-KEY endpoint — same shape, OLD value:
curl -fsS -X PUT \
  -H "Authorization: Bearer $RENDER_API_KEY" \
  -H 'Content-Type: application/json' \
  -d "{\"value\":\"$OLD_API_SECRET_KEY\"}" \
  'https://api.render.com/v1/services/srv-d89bisbbc2fs73f1pjpg/env-vars/API_SECRET_KEY'
# Repeat for frontend.
# Trigger redeploys (step 5).
# Smoke. Memory note `incident_api_key_rotation_rollback_<date>.md`.
```

The OLD key remains accepted until the new deploy's container hot-swaps,
which happens at the end of the build (Render does NOT support
both-keys-valid during deploy; the swap is atomic per container).

### Memory link

- [[n-key-audit-2026-05-30]] — original audit that surfaced this.
- [[api-secret-key-rotation-2026-05-30]] — the live rotation that
  applied this runbook (deploy IDs, sha256 prefixes, smoke results).

---

## §2 — Supabase DB password rotation

### Why

Unblocks the `tests/load/explain_hot_paths.py` EXPLAIN harness + every
`security.yml` gate that connects via `DATABASE_URL` (17 of them per
`docs/db-invariants.md`). Pre-rotation impact audit is in
`docs/audits/2026-05-30-db-password-consumers.md` — **zero Render
runtime impact** because the backend uses PostgREST via service-role,
not direct PG.

### Prerequisites

- Supabase project owner access
- `gh` CLI authenticated to GitHub
- Password manager open

### Steps

```sh
# 1. Open the project DB settings:
open 'https://supabase.com/dashboard/project/kbtkxpvchmunwjykbeht/settings/database'

# 2. Click "Reset database password" → copy the NEW connection string.
#    SAVE TO 1PASSWORD BEFORE CLOSING THE DIALOG (Supabase will not
#    show it again).
#
#    Format: postgresql://postgres.kbtkxpvchmunwjykbeht:<NEW_PW>@<host>:6543/postgres

# 3. Three update locations:

# 3a. GitHub Actions secret (17 CI scripts read this):
gh secret set SUPABASE_DATABASE_URL --body 'postgresql://postgres.kbtkxpvchmunwjykbeht:<NEW_PW>@aws-0-eu-central-1.pooler.supabase.com:6543/postgres'

# 3b. Local secrets file:
# Open ~/.bookbed-secrets in your editor and replace the
# SUPABASE_DATABASE_URL= line (do NOT echo into the file — quoting
# pitfall with special chars in passwords).

# 3c. Password manager: 1Password entry "Supabase LDS prod DB" — paste.
```

### Verification probe

```sh
# Smoke: trigger security.yml which exercises every DB-touching gate:
gh workflow run security.yml
sleep 60
gh run list --workflow security.yml --limit 1 \
  --json status,conclusion,databaseId -q '.[0]'

# OR locally:
source ~/.bookbed-secrets
psql "$SUPABASE_DATABASE_URL" -c 'SELECT now();'
```

### Rollback

There is no rollback — Supabase generates a new password and the old
becomes invalid immediately. If the new password is lost between
Reset and Save, repeat the Reset step. Backend runtime is unaffected
either way (service-role is a separate credential, see §3).

### Cross-ref

- Pre-rotation matrix: [[db-password-consumers-2026-05-30]]
- 17 gates inventory: `docs/db-invariants.md`

---

## §3 — Supabase PAT rotation

### Why

Personal Access Token for the Supabase Management API. Last value
leaked in transcript on 2026-05-27 ([[reminder-supabase-key-rotation]]);
operator deferred rotation. Required to land the Phase 16 schema
(`scripts/migrations/2026-05-30_phase16-reply-classifications.sql` in
PR #476) + any future schema-drift Mgmt-API recovery.

### Prerequisites

- Supabase account owner login

### Steps

```sh
# 1. Open the PAT manager:
open 'https://supabase.com/dashboard/account/tokens'

# 2. Revoke the existing "LDS Mgmt API" token (or whatever name is
#    visible). DO NOT skip the revoke — if the leaked value is still
#    live, it grants Mgmt-API access until revoked.

# 3. Click "Generate new token":
#    - Name: lds-mgmt-2026-05-30
#    - Scope: full (Mgmt API does not subscope yet)
#    - Copy the NEW value — Supabase will not show it again.

# 4. Two update locations:

# 4a. Local secrets file (~/.bookbed-secrets):
#     Add or replace: SUPABASE_PERSONAL_ACCESS_TOKEN=<paste-supabase-pat-here>

# 4b. (Only if any GitHub Actions workflow uses it — search first:)
git grep -l 'SUPABASE_PERSONAL_ACCESS_TOKEN' .github/workflows/
# If grep returns files: gh secret set SUPABASE_PERSONAL_ACCESS_TOKEN --body '<paste-supabase-pat-here>'
```

### Verification probe

```sh
source ~/.bookbed-secrets
curl -fsS \
  -H "Authorization: Bearer $SUPABASE_PERSONAL_ACCESS_TOKEN" \
  'https://api.supabase.com/v1/projects' \
  | jq 'length'
# Expected: 1 (just the LDS project) or however many projects you have.
```

### Phase 16 schema apply (immediate next step after rotation)

```sh
source ~/.bookbed-secrets
SQL=$(cat scripts/migrations/2026-05-30_phase16-reply-classifications.sql | jq -Rs '{query: .}')
curl -fsS -X POST \
  -H "Authorization: Bearer $SUPABASE_PERSONAL_ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d "$SQL" \
  'https://api.supabase.com/v1/projects/kbtkxpvchmunwjykbeht/database/query'

# Re-verify drift gate (should now exit 0):
DATABASE_URL="$SUPABASE_DATABASE_URL" python -m src.scripts.schema_drift_check
```

### Rollback

PATs cannot be rolled back (Supabase generates new value). If the new
PAT is lost, repeat Generate step. The Mgmt API itself is idempotent;
re-running a migration that already applied is a no-op.

---

## §4 — `ANTHROPIC_API_KEY` mint + wire

### Why

Unblocks two things:

1. PR #477 live bench (`scripts/run_reply_classifier_bench.py`) on the
   50-row synthetic dataset — produces the accuracy + p95 latency
   baseline for the Phase 16 classifier.
2. PR #478 Phase 16 classifier going live in prod (once
   `PHASE16_REPLY_CLASSIFIER=1` is also flipped + T1 schema applied
   per §3).

### Prerequisites

- Anthropic account access — https://console.anthropic.com
- Render dashboard access for backend `srv-d89bisbbc2fs73f1pjpg`

### Steps

```sh
# 1. Mint at https://console.anthropic.com/settings/keys
#    - Name: lds-phase16
#    - Workspace: default (or operator-specific if multi-tenant)
#    - Copy the NEW value (<paste-anthropic-key-here>).

# 2. Three update locations:

# 2a. Local secrets file (for running the bench locally):
#     ~/.bookbed-secrets += ANTHROPIC_API_KEY=<paste-anthropic-key-here>

# 2b. Render backend env (for the runtime classifier — flag-gated until
#     PHASE16_REPLY_CLASSIFIER=1):
curl -X PUT \
  -H "Authorization: Bearer $RENDER_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '[{"key":"ANTHROPIC_API_KEY","value":"<paste-anthropic-key-here>"}]' \
  'https://api.render.com/v1/services/srv-d89bisbbc2fs73f1pjpg/env-vars'

# 2c. (If Phase 16 ever runs in a separate cron service):
#     Repeat 2b for the dispatch + sweeper cron service IDs in
#     ~/git/LeadDataScraper/docs/runbooks/dispatch-cron.md.
```

### Verification probe

```sh
# Local: bench runner is the canonical smoke. Takes ~75s.
source ~/.bookbed-secrets
cd ~/git/lds-phase16-bench  # the PR #477 worktree
python scripts/run_reply_classifier_bench.py --limit 5
# Expected output: 5 rows classified, JSON + MD report written under
# tests/benchmarks/. Exit 0.

# Cheaper smoke (single API call):
source ~/.bookbed-secrets
python3 -c "
import anthropic
m = anthropic.Anthropic().messages.create(
    model='claude-haiku-4-5-20251001',
    max_tokens=10,
    messages=[{'role':'user','content':'ping'}],
)
print(m.content[0].text)
"
```

### Wire-up follow-up (do NOT do yet)

Flipping `PHASE16_REPLY_CLASSIFIER=1` in Render backend env is the
TRIGGER for live classification. Before doing so:

1. PR #476 schema MUST be applied (§3 covers this).
2. PR #478 must be merged.
3. `anthropic` must be in `requirements.in` + `requirements.txt`
   (currently held off per memory
   [[phase16-classifier-bench-2026-05-30]]).
4. The bench from §4 verification MUST show ≥85 % clear-case accuracy
   + ≤2.0s p95 (targets pinned in `scripts/run_reply_classifier_bench.py`).
5. Operator-grep `webhook_sweeper` logs for `phase16
   apply_state_transitions` lines after the first inbound reply event
   to confirm the state-machine is logging cleanly before the
   side-effects (campaign_messages stamp, suppressions INSERT) fire on
   live leads.

### Rollback

Revoke the key in the Anthropic console (same URL as Mint). Render env
removal: PUT the env-vars list without the `ANTHROPIC_API_KEY` entry.
Classifier service degrades to `None` returns + logs "stub" lines per
the T2 design — no exception path.

---

## §5 — Sentry auth token (project-scoped)

### Why

Backend can't query the Sentry API to surface issues / drive SLOs /
self-monitor without a `SENTRY_AUTH_TOKEN`. DSNs are already wired per
[[sentry-enabled-2026-05-29]] so capture works — this rotation enables
the *query* path + source-map upload for the frontend release pipeline.

### Prerequisites

- Operator has admin on Sentry org `leaddatascraper`
- `$RENDER_API_KEY` in shell (for the Render env-PUT below)

### Steps

```sh
# 1. Mint at https://sentry.io/settings/account/api/auth-tokens/
#    - Name: lds-mgmt-$(date +%Y-%m-%d)
#    - Scopes (minimum for issue query): project:read, event:read, org:read
#    - Add release:admin ONLY if you also use this token for frontend
#      source-map upload (Sentry CLI `releases files upload-sourcemaps`).
#    - Click Create.
#    - Copy the token (starts with sntrys_) — Sentry shows once.
#    - Save to 1Password as "LDS Sentry auth token $(date +%Y-%m-%d)".

# 2. Three update locations (one local file + two Render env-vars):
NEW_SENTRY_TOKEN='<paste-sentry-token-here>'

# 2a. Local secrets file:
sed -i.bak.$(date +%Y%m%d) "s|^SENTRY_AUTH_TOKEN=.*|SENTRY_AUTH_TOKEN=$NEW_SENTRY_TOKEN|" ~/.bookbed-secrets 2>/dev/null \
  || echo "SENTRY_AUTH_TOKEN=$NEW_SENTRY_TOKEN" >> ~/.bookbed-secrets
grep -c '^SENTRY_AUTH_TOKEN=' ~/.bookbed-secrets   # → 1
# Also add the org + project slugs (only needed once; safe to re-set):
grep -q '^SENTRY_ORG='     ~/.bookbed-secrets || echo 'SENTRY_ORG=leaddatascraper' >> ~/.bookbed-secrets
grep -q '^SENTRY_PROJECT=' ~/.bookbed-secrets || echo 'SENTRY_PROJECT=lds-backend' >> ~/.bookbed-secrets

# 2b. Render backend env — SINGLE-KEY endpoint per §1's two-endpoint
#     lesson (bulk PUT replaces the entire env-var list):
for KV in \
  "SENTRY_AUTH_TOKEN:$NEW_SENTRY_TOKEN" \
  "SENTRY_ORG:leaddatascraper" \
  "SENTRY_PROJECT:lds-backend" ; do
  K="${KV%%:*}" ; V="${KV#*:}"
  curl -fsS -X PUT \
    -H "Authorization: Bearer $RENDER_API_KEY" \
    -H 'Content-Type: application/json' \
    -d "{\"value\":\"$V\"}" \
    "https://api.render.com/v1/services/srv-d89bisbbc2fs73f1pjpg/env-vars/$K" \
    && echo "$K set"
done

# 3. Single-key env-var PUT does NOT auto-redeploy. Trigger:
curl -fsS -X POST \
  -H "Authorization: Bearer $RENDER_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"clearCache":"do_not_clear"}' \
  'https://api.render.com/v1/services/srv-d89bisbbc2fs73f1pjpg/deploys'

# 4. Wait deploy live (~3 min). Poll:
curl -fsS -H "Authorization: Bearer $RENDER_API_KEY" \
  'https://api.render.com/v1/services/srv-d89bisbbc2fs73f1pjpg/deploys?limit=1' \
  | jq -r '.[].deploy | "\(.id) \(.status)"'
```

### Verification probe

```sh
# Direct Sentry API smoke (sidesteps backend wiring uncertainty —
# proves the token + scopes + slugs are all correct):
source ~/.bookbed-secrets
curl -fsS \
  -H "Authorization: Bearer $SENTRY_AUTH_TOKEN" \
  "https://sentry.io/api/0/projects/leaddatascraper/lds-backend/issues/?limit=5&statsPeriod=24h" \
  | jq 'length'
# Expected: integer ≥ 0 (empty array is fine on a quiet prod).
# 401 → scopes wrong (re-mint with the 3 listed scopes).
# 404 → org/project slug mismatch (verify against the URL after dashboard login).
```

### Rollback

```sh
# Sentry-side: dashboard → Auth Tokens → Revoke on the NEW token.
# Render-side: re-PUT the OLD value (kept in 1Password):
curl -fsS -X PUT \
  -H "Authorization: Bearer $RENDER_API_KEY" \
  -H 'Content-Type: application/json' \
  -d "{\"value\":\"$OLD_SENTRY_TOKEN\"}" \
  'https://api.render.com/v1/services/srv-d89bisbbc2fs73f1pjpg/env-vars/SENTRY_AUTH_TOKEN'
# Re-deploy (see step 3).
```

### Memory link

- [[sentry-token-awaiting-operator-2026-05-30]] — the gate this
  rotation lifts (and the partial recipe that fed this section).
- [[sentry-enabled-2026-05-29]] — DSN side already wired; this is the
  complementary token side.

---

## §6 — `RENDER_API_KEY` rotation (dashboard-only)

### Why

`RENDER_API_KEY` is the master key for every other rotation in this
runbook + every Render Mgmt-API operation. Render has **no API
endpoint to mint its own PATs** — rotation is dashboard-only.

This section also catches the case where the value has leaked to a
transcript (the autonomous-rotation flow on 2026-05-31 echoed the
value through a `${VAR:+PRESENT}${VAR:-MISSING}` shell expansion;
see [[api-secret-key-rotation-2026-05-30]] cross-ref) — treat as
compromised and rotate.

### Prerequisites

- Render dashboard access (PAT lives at the *account* level, not per-
  workspace).
- 1Password open before clicking Create — the token is shown once.

### Steps

```sh
# 1. Open the PAT manager:
open 'https://dashboard.render.com/u/settings#api-keys'

# 2. Click "Create API Key":
#    - Name: lds-prod-$(date +%Y-%m-%d)
#    - Click Create — copy the value (starts with rnd_). Render shows it once.
#    - Save to 1Password as "LDS Render PAT $(date +%Y-%m-%d)" BEFORE
#      closing the dialog.

# 3. Two local update locations:
NEW_RENDER_PAT='<paste-render-pat-here>'

# 3a. Persistent secret file:
sed -i.bak.$(date +%Y%m%d) "s|^RENDER_API_KEY=.*|RENDER_API_KEY=$NEW_RENDER_PAT|" ~/.bookbed-secrets 2>/dev/null \
  || echo "RENDER_API_KEY=$NEW_RENDER_PAT" >> ~/.bookbed-secrets
grep -c '^RENDER_API_KEY=' ~/.bookbed-secrets   # → 1

# 3b. Shell-rc (so future shells pick it up without sourcing):
if grep -q '^export RENDER_API_KEY=' ~/.zshenv 2>/dev/null; then
  sed -i.bak.$(date +%Y%m%d) "s|^export RENDER_API_KEY=.*|export RENDER_API_KEY=$NEW_RENDER_PAT|" ~/.zshenv
else
  echo "export RENDER_API_KEY=$NEW_RENDER_PAT" >> ~/.zshenv
fi

# 4. Reload current shell so the new value is live now:
export RENDER_API_KEY="$NEW_RENDER_PAT"
# (or: source ~/.zshenv — but only if ~/.zshenv is idempotent.)

# 5. Smoke the new key:
curl -fsS -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer $RENDER_API_KEY" \
  'https://api.render.com/v1/services?limit=1'
# Expected: 200. 401 → typo in token (re-paste). 403 → workspace/role mismatch.

# 6. Revoke the OLD key in the dashboard:
open 'https://dashboard.render.com/u/settings#api-keys'
# Click "Revoke" on the old-named row (the one NOT named lds-prod-<today>).

# 7. Verify revocation — re-fire step 5 using the OLD key value
#    (paste manually one last time):
OLD_RENDER_PAT='<paste-old-rnd-value-here>'
curl -fsS -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer $OLD_RENDER_PAT" \
  'https://api.render.com/v1/services?limit=1'
# Expected: 401. Any other code (200, 403, 5xx) → revocation didn't
# stick; retry step 6.
```

### Verification probe

Covered by steps 5 + 7 above (smoke new key returns 200; verify old
key returns 401). No further probe needed — every other Mgmt-API
endpoint flows through the same auth gate.

### Rollback

Not applicable — once an old key is revoked it is dead. If you lose
the new key between mint and step 4, generate again (step 1-2) and
repeat. If you accidentally revoke the only working key (no other
PAT minted), you must log in to the dashboard fresh and mint a new
one; there is no API path back in.

### Memory link

- [[api-secret-key-rotation-2026-05-30]] — the autonomous flow that
  echoed the previous PAT to transcript via shell expansion.

---

## Post-rotation broadcast template

After completing each rotation, post in your sync channel:

```
✅ <rotation-name> rotated <YYYY-MM-DD HH:MMZ>
   New sha256[:12]: <hash>
   Updated: <list of locations from §X step 2/3/4>
   Verified: <command + result line>
   Dependents unblocked: <items from the table at top of this runbook>
```

Then in the orchestrator: `<rotation-name> rotated` (literal string —
matches the bot's listener).
