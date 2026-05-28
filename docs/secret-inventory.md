# Secret inventory

Every credential the system holds, where it lives, what it grants, and
how often it rotates. Keep this file current — when a new secret is
introduced, the PR that introduces it must also add a row here. CODEOWNERS
gates `/.github/` and `/docs/` (this file) on DanLika's review.

Last reviewed: **2026-05-22**. Next review: **2026-08-22** (quarterly).

## Threat model in one paragraph

A leaked secret's blast radius depends on three things: its **scope**
(what it can touch), its **lifetime** (how long the leak stays exploitable),
and its **detectability** (how loudly a misuse trips an alarm). The
controls below trade off these three. The default is "narrow scope, short
lifetime"; broad-scope secrets (service-role keys, root PATs) get tighter
rotation. OIDC trust relationships eliminate lifetime entirely — prefer
them where the provider supports it.

## OIDC migration status

Pre-staged OIDC trust relationships eliminate long-lived bearer tokens.
Status per provider as of 2026-05-22 (verify before changing approach):

| Provider | OIDC support | Action |
|---|---|---|
| **GitHub → GHCR** | ✅ Native — `GITHUB_TOKEN` w/ `packages: write` is short-lived (1h) and scoped to the workflow run. | **In use** (`deploy-backend.yml` push step). No long-lived PAT. |
| **GitHub → Sigstore Fulcio** | ✅ Native — `id-token: write` + `cosign sign --yes` is keyless. | **In use** (`deploy-backend.yml` provenance + verify). |
| **GitHub → Render** | ⚠️ **Verify before adopting** — Render documents some OIDC support but the surface area / IAM-binding model has been evolving. Check <https://render.com/docs/access-control> AND test in a non-prod service before flipping the deploy workflow off `RENDER_API_KEY`. | Fallback: rotate `RENDER_API_KEY` monthly (below). |
| **GitHub → Supabase Management API** | ❌ Not supported (PAT only). | Rotate `SUPABASE_ACCESS_TOKEN` monthly. |
| **GitHub → Google AI Studio (Gemini)** | ❌ Not supported for the AI Studio key. Vertex AI (via Google Cloud Workload Identity Federation) DOES support OIDC but requires migrating off `google.genai`'s API-key auth. | Rotate `GEMINI_API_KEY` quarterly. Migration to Vertex + WIF tracked as future hardening. |
| **GitHub → Slack webhook** | ❌ Webhooks are static URLs. | Treat URL itself as a secret; rotate annually or on suspicion. |

## Inventory

### Production backend (Render env)

| Secret | What it grants | Scope | Rotation | Leak detection |
|---|---|---|---|---|
| `API_SECRET_KEY` | `X-API-Key` for every backend endpoint except `/`. | Per-deployment shared key. ALL endpoints. | **Quarterly** (next: 2026-08-22). On leak: immediate rotate, redeploy backend + frontend (frontend proxy injects it). | Audit logs: any 4xx from `verify_api_key` failures, especially from unknown source IPs after rotation. |
| `ADMIN_TOKEN` | `X-Admin-Token` for `DELETE /leads/clear`. | Single destructive endpoint. | **Quarterly** (paired with `API_SECRET_KEY`). | Backend log: every `/leads/clear` 403 is suspicious in single-tenant. |
| `SUPABASE_URL` | Project hostname. | Not secret in the cryptographic sense, but treat as inventory. | Changes only on project replatform. | N/A |
| `SUPABASE_SERVICE_ROLE_KEY` | Bypasses RLS for all 4 tables. **Highest-blast-radius secret in the system.** | Full DB read/write. | **Monthly** (next: 2026-06-22). Hot-rotate by issuing a new key in Supabase dashboard, updating Render env + GH secret in lockstep, then revoking old. | RLS-bypass query patterns from unexpected IPs. Set up Supabase log alerts on `service_role` usage from non-Render egress. |
| `GEMINI_API_KEY` | Google AI Studio billing-quota access. | All Gemini models the project uses. | **Quarterly** (next: 2026-08-22). Watch for unusual spend → indicates leak. | Google AI Studio usage dashboard; budget alert at 2× normal monthly spend. |
| `ALLOWED_ORIGINS` | CORS + Origin allowlist. | Not secret; misconfig is the risk (defaults to `localhost:3000` if unset → fail-closed Origin gate). | Update on domain change. | Origin-mismatch 403s are normal during rotation; sustained spike = misconfig. |
| `OPERATOR_EMAIL` | Single-tenant assertion target. | Boot-time invariant only. | Update on operator change. | Backend boot log: `RuntimeError` on second user provisioned. |
| `OPERATOR_NAME` | Non-secret — appears in outreach draft signatures. | Render env (cosmetic). | N/A | N/A |
| `RESEND_API_KEY` | Resend HTTP API access for outreach email send + `Idempotency-Key` dedup (24h provider-side window). | Send-only scope (no domain admin, no team admin). | **Quarterly** (next: 2026-08-22). On leak: rotate in Resend dashboard, redeploy backend. Compromised key → spam from `mail.leaddatascraper.com` → DMARC reputation damage. | Resend dashboard usage chart + bounce rate; daily spend >2× normal. |
| `RESEND_FROM_EMAIL` / `RESEND_FROM_NAME` | From-header config — `"Display Name <addr@mail.leaddatascraper.com>"`. | Not secret; misconfig is the risk (mismatch with verified domain → Resend 422). | On domain change. | Auth-Results header in seed-test inbox. |
| `EMAIL_PROVIDER` | Factory switch in `src/integrations/email_sender.py::get_email_sender` — `smtp` (default) or `resend_api`. | Boot-time selection. Unknown value raises at startup. | Flip to `resend_api` only after `docs/email-deliverability.md` checklist passes 10/10. | Backend boot log: `ValueError: Unknown EMAIL_PROVIDER`. |
| `EMAIL_REPLY_TO` | `Reply-To` header value — operator's real inbox (`mail.leaddatascraper.com` has no MX). | Not secret. | On operator-inbox change. | Replies arriving at wrong inbox. |
| `EMAIL_LIST_UNSUBSCRIBE` | `List-Unsubscribe` header (`<https://...>` or `<mailto:...>`). Pair auto-emits `List-Unsubscribe-Post: List-Unsubscribe=One-Click`. | Operator config; required by Gmail at >5k/day. | On unsubscribe-endpoint URL change. | Gmail Postmaster Tools spam rate; missing header → Gmail Spam tab. |

### Production frontend (Render env)

| Secret | What it grants | Scope | Rotation | Leak detection |
|---|---|---|---|---|
| `BACKEND_URL` | URL of backend service. | Routing only. | On infra change. | N/A |
| `API_SECRET_KEY` | Server-side mirror of backend value. **MUST match backend.** | Frontend proxy → backend. | Paired with backend rotation. | Drift → all proxy fetches 403. |
| `ADMIN_TOKEN` | Mirror of backend value. | "Clear All Leads" path only. | Paired with backend rotation. | Drift → button 403s. |
| `ALLOWED_ORIGINS` | Origin gate on `/api/proxy` + `/api/auth/signout`. | Same allowlist as backend. | Paired. | Origin 403s during rotation. |
| `NEXT_PUBLIC_SUPABASE_URL` | Project URL, **public by design** (`NEXT_PUBLIC_*`). | Browser-readable. | On project replatform. | N/A |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Anon JWT — RLS gates everything. **Public by design.** | Browser-readable. Compromised only if RLS itself is compromised. | On RLS-policy rotation. | RLS denial spikes in Supabase logs. |
| `TRUSTED_CLIENT_IP_HEADER` | Name of the platform-injected real-IP header. | Config, not secret. | On platform change (Render vs Vercel). | Rate-limit-key collisions if misnamed. |

### GitHub Actions secrets (repo-level)

| Secret | Used by | What it grants | Rotation |
|---|---|---|---|
| `GITHUB_TOKEN` | Every workflow | Auto-provisioned, ~1h lifetime, scoped to workflow. | **No rotation needed** — short-lived by design. |
| `RENDER_API_KEY` | `post-deploy-smoke.yml`, `deploy-backend.yml`, `synthetic-monitor.yml` | Render API: deploy + read for ALL services on the account. **Long-lived bearer.** | **Monthly** (next: 2026-06-22). On leak: rotate immediately, audit Render deploy history for unauthorized rollouts. If Render exposes per-service or scoped tokens (verify in dashboard), use the narrower form. |
| `RENDER_BACKEND_SERVICE_ID` | `deploy-backend.yml`, `post-deploy-smoke.yml` | Identifies which Render service to deploy/rollback. | Static — changes only on service recreation. Not a secret per se, but treated as one to avoid info leakage. |
| `SUPABASE_DATABASE_URL` | `security.yml` (schema-drift, referential-integrity, query-plans, jsonb-shapes, null-audit), `ci.yml` (same jobs) | Postgres connection string with **password embedded**. Treat password as DB credential. | **Monthly**. Scope DB role to read-only on `information_schema` / `pg_catalog` / `pg_policies` + minimal write on `campaigns` / `campaign_messages` for the referential-integrity check (mutations are rolled back). |
| `SUPABASE_E2E_URL` / `SUPABASE_E2E_ANON_KEY` / `SUPABASE_E2E_SERVICE_KEY` | `ci.yml` Playwright job | Ephemeral Supabase branch — separate from production. | Per-branch lifetime (auto-rotates with branch). Manual rotate if branch is reused beyond a release cycle. |
| `E2E_USER_EMAIL` / `E2E_USER_PASSWORD` | `ci.yml` Playwright job | Test user provisioned in the E2E Supabase branch. | Per-branch. Password should be high-entropy, never reused across branches. |
| `E2E_API_SECRET_KEY` / `E2E_ADMIN_TOKEN` | `ci.yml` Playwright job | Backend X-API-Key + admin token for the ephemeral E2E backend instance. | Per-PR-run effectively; static configured value in repo secrets, value distinct from prod. |
| `GEMINI_API_KEY` | `post-deploy-smoke.yml` (dependency-health probe) | Same as backend value. | Paired with backend rotation. |
| `SUPABASE_URL` / `SUPABASE_ANON_KEY` / `SUPABASE_SERVICE_ROLE_KEY` | `post-deploy-smoke.yml` | Production values — used by smoke probe to verify the deployed backend can reach Supabase. | Paired with backend rotation. |
| `LHCI_GITHUB_APP_TOKEN` | `ci.yml` Lighthouse job | LHCI's GitHub-app token for posting check results. | Annual; revoke if Lighthouse CI is decommissioned. |
| `GIST_TOKEN` | `synthetic-monitor.yml` | GitHub PAT with `gist` scope — writes to `MONITOR_GIST_ID`. | **Quarterly**. Should be a fine-grained PAT scoped to a single gist. |
| `MONITOR_GIST_ID` | `synthetic-monitor.yml` | Gist ID for synthetic-monitor history. | Static; rotate gist if leaked. |
| `SLACK_WEBHOOK_URL` | `synthetic-monitor.yml`, `post-deploy-smoke.yml` | Posts to a Slack channel. | **Annual** or on suspicion. Webhook URLs cannot be scoped further than the channel they were created for. |
| `PROD_BACKEND_URL` / `PROD_FRONTEND_URL` / `PROD_API_SECRET_KEY` | `post-deploy-smoke.yml`, `synthetic-monitor.yml` | URLs + API key for probing prod from CI. `PROD_API_SECRET_KEY` mirrors `API_SECRET_KEY`. | URLs static; API key paired with `API_SECRET_KEY` rotation. |

## Rotation runbook

For any rotation:

1. **Generate the new value** at the provider (Supabase / Render / Google
   AI Studio / GitHub Settings → Developer settings → PATs).
2. **Stage in parallel**: set the new value as a NEW secret name
   (`API_SECRET_KEY_NEXT`) in GitHub + Render. Deploy a build that reads
   either name with a precedence rule. (For most of these secrets we don't
   bother with this — the rotation window is short enough that a
   coordinated swap works.)
3. **Coordinated swap**: simultaneously update the canonical secret name
   in GitHub Actions repo secrets AND the production env (Render
   dashboard for backend + frontend). Trigger a redeploy of any service
   that reads the secret at boot.
4. **Verify**: hit a representative endpoint that uses the secret to
   confirm the new value works. For `SUPABASE_SERVICE_ROLE_KEY`, run the
   `/orchestrator/status` endpoint or the `schema-drift` job manually
   via `workflow_dispatch`.
5. **Revoke**: at the provider, delete/invalidate the OLD value. Do not
   skip this — the rotation isn't complete until the old key cannot
   authenticate.
6. **Log**: append to this file's revision history (bottom of doc) with
   date + which secret + reason (scheduled / leak-suspected / personnel
   change).

## Leak response runbook

If any secret leaks (gitleaks finding, log exposure, accidental Slack
paste, suspected compromise):

1. **Rotate the secret immediately** — even if you haven't confirmed
   exploitation. Lifetime is the lever you control; cut it to zero.
2. **If the secret leaked in git history**: gitleaks ci-step + manual
   audit. Use BFG Repo-Cleaner (`bfg --replace-text passwords.txt`) and
   coordinated force-push as documented in
   `.github/workflows/ci.yml::secret-scan`. Every contributor must
   re-clone.
3. **Investigate**: pull provider audit logs for the rotation window —
   any usage from unexpected IPs / user agents indicates active
   exploitation. Supabase service-role usage from non-Render egress
   should match nothing.
4. **Notify** if customer-impacting (e.g., a Supabase service-role leak
   could have exfiltrated lead data). Single-operator project, so the
   notification target is mostly internal; if PII is involved consult
   relevant regulator timelines (GDPR: 72h).
5. **Update this file** with the incident: secret, leak vector, mitigation
   applied. Use the revision history at the bottom.

## Future hardening

- **Render OIDC**: verify current support. If usable, replace
  `RENDER_API_KEY` in `deploy-backend.yml` + `post-deploy-smoke.yml` +
  `synthetic-monitor.yml` with `permissions: id-token: write` + a Render
  trust binding to `repo:DanLika/LeadDataScraper`. Eliminates the single
  highest-blast-radius long-lived secret in the inventory.
- **Vertex AI + Workload Identity Federation**: migrate Gemini calls off
  `google.genai` (API-key) to `google.cloud.aiplatform` (service account
  + WIF). Drops `GEMINI_API_KEY` from CI secrets entirely.
- **Supabase fine-grained PAT**: when Supabase ships project-scoped
  tokens (currently the management PAT is account-wide), narrow
  `SUPABASE_ACCESS_TOKEN` accordingly. Until then, ensure the PAT is
  owned by a service-only GitHub account, not a human's personal one.
- **Per-service Render API keys**: if Render dashboard exposes scoping,
  reissue `RENDER_API_KEY` as one key per service (backend, frontend,
  worker). Limits blast radius from "all services" to a single one.
- **Secret-scanning push protection**: enable in Settings → Code security
  → Push protection. Blocks pushes containing detected secrets at the
  git protocol layer — defense in depth on top of gitleaks CI.

## Revision history

| Date | Change | By |
|---|---|---|
| 2026-05-22 | Initial inventory. Renders OIDC noted as "verify before adopting"; all long-lived bearer tokens documented with rotation cadence. | DanLika |
