# Security Model

## Trust boundaries

```
Browser  ─/api/proxy/*─►  Next.js server  ─X-API-Key─►  FastAPI  ─service_role─►  Supabase (RLS on)
```

- The **browser** holds no secrets. Only `NEXT_PUBLIC_SUPABASE_URL` and the
  publishable anon key, both of which are useless because Supabase RLS blocks
  anon access on every data table.
- The **Next.js server** (Node runtime) is the only place that knows the
  backend `API_SECRET_KEY`. Every browser request flows through
  `frontend/app/api/proxy/[...path]/route.ts`, which forwards to
  `BACKEND_URL` and injects `X-API-Key`.
- The **FastAPI backend** validates `X-API-Key` and uses Supabase's
  `service_role` key to perform all reads/writes. Service role bypasses RLS
  by design.
- **Supabase** has Row-Level Security enabled on `leads`, `campaigns`,
  `campaign_messages`, `orchestration_jobs`. `anon` and `authenticated`
  roles are revoked from those tables.

## Layered controls

| Layer | Control | Why |
|------|---------|-----|
| Browser | CSP + `X-Frame-Options: DENY` + `Referrer-Policy: strict-origin-when-cross-origin` + HSTS + `Permissions-Policy` (camera/mic/geo off) | Clickjacking, XSS, mixed-content, info-leak defence — set in `frontend/next.config.ts` |
| Network | Explicit `ALLOWED_ORIGINS` (no `*`) | CORS-locks the API to trusted origins |
| API auth | `X-API-Key` header on every endpoint, validated with `secrets.compare_digest` | Constant-time compare; key never enters the browser bundle |
| Destructive ops | `X-Admin-Token` second secret on `DELETE /leads/clear`, constant-time compare | Defence-in-depth: a leaked API key cannot wipe the DB |
| API surface | `/docs`, `/redoc`, `/openapi.json` disabled unless `ENABLE_DOCS=true` | Hide endpoint enumeration in prod |
| Client IP | Proxy strips client-sent `X-Forwarded-For`/`X-Real-IP`/`Forwarded`; re-emits the platform-trusted header (`TRUSTED_CLIENT_IP_HEADER` env). Backend honours XFF **only** when the request also carries a valid `X-API-Key` (proven via `secrets.compare_digest`) — i.e. it came through the proxy. Forged XFF without the key falls back to the TCP peer IP. | Anti-spoof for rate limiter buckets even if FastAPI is reached directly |
| Outbound fetch | `src/utils/ssrf_guard.py` blocks loopback, RFC1918, link-local, CGNAT (100.64/10), multicast, reserved, 0.0.0.0, IPv4-mapped-v6, octal/decimal/hex literal IPs, `metadata.google.internal`, and non-`http(s)` schemes. Wired via `SSRFGuardResolver` (aiohttp TCPConnector) in `seo_audit.py` so every redirect re-resolves; `enrichment_engine.py` pre-checks before `page.goto`. | Stops cloud-metadata / internal-network SSRF via user-supplied lead URLs |
| AI/job abuse | `slowapi` per-IP rate limits on `/ask`, `/draft-*`, `/insights`, `/execute`, `/upload`, `/hunt-*`, `/discovery/start`, `/process-all`, `/enrich/start`, `/leads/clear` | Gemini billing + Playwright spawn protection |
| Polling abuse | Per-IP caps on `/leads`, `/stats`, `/audit-status` | Reads can still flood the backend |
| Database | RLS + revoke on data tables | Even if the anon key leaks, no rows are readable |
| Schema migration | Narrow `add_lead_column(text)` SECURITY DEFINER RPC + Python regex pre-check | Replaces unsafe generic `exec_sql`; defence-in-depth on column name |
| File uploads | UUID names under `tempfile.gettempdir()`, 50 MB cap, content-type allowlist, `try/finally` cleanup | Path traversal + disk leak protection |
| Errors | Global `Exception` handler returns JSON `{ "error": ... }` | Prevents stack-trace leakage; the proxy can always `.json()` |

## Required environment variables

### Backend `.env`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY` — server-side only, **never** in the frontend
- `GEMINI_API_KEY`
- `API_SECRET_KEY` — same value as the frontend's server-side `API_SECRET_KEY`
- `ADMIN_TOKEN` — separate from `API_SECRET_KEY`, never shipped to browsers
- `ALLOWED_ORIGINS` — comma-separated list of trusted origins
- `ENABLE_DOCS` — set to `true` only in dev to expose `/docs`, `/redoc`, `/openapi.json`. Default is closed.
- `SMTP_*` (optional, for outreach)

### Frontend `.env.local`
- `BACKEND_URL` — server-side only (used by `/api/proxy/[...path]`)
- `API_SECRET_KEY` — server-side only, **must not** be prefixed `NEXT_PUBLIC_`
- `TRUSTED_CLIENT_IP_HEADER` — platform-injected client-IP header the proxy
  re-emits as `X-Forwarded-For`. Defaults to `x-vercel-forwarded-for`. On
  Render or other XFF-using hosts set to `x-forwarded-for`.
- `NEXT_PUBLIC_SUPABASE_URL` — public
- `NEXT_PUBLIC_SUPABASE_ANON_KEY` — public (RLS makes it harmless)

> Never prefix `API_SECRET_KEY` or `ADMIN_TOKEN` with `NEXT_PUBLIC_`. Any
> historical `NEXT_PUBLIC_API_KEY` line in `frontend/.env.local` must be
> deleted and the value rotated in prod — it was once baked into browser
> bundles.

## Rate limits (per IP)

| Endpoint | Limit |
|----------|-------|
| `/leads`, `/stats` | 30 / min |
| `/audit-status` | 60 / min |
| `/ask`, `/insights`, `/execute`, `/enrich/start` | 10 / min |
| `/draft-outreach`, `/draft-linkedin`, `/hunt-lead` | 20 / min |
| `/upload`, `/discovery/start` | 5 / min |
| `/hunt-all`, `/process-all` | 3 / min |
| `DELETE /leads/clear` | 3 / hour (also requires `X-Admin-Token`) |

The limiter honours `X-Forwarded-For` **only when the request also carries a
valid `X-API-Key`** (constant-time compared). The Next.js proxy is the only
legitimate holder of that key, so a matching key proves the XFF was set by
the proxy (which strips client-supplied XFF). Requests without — or with an
invalid — key are bucketed by their TCP peer IP, so forged XFF cannot spread
load across rate-limit buckets even if the FastAPI port is exposed directly.

## Reporting

Email security issues privately rather than opening a public issue.
