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
| Network | Explicit `ALLOWED_ORIGINS` (no `*`) | CORS-locks the API to trusted origins |
| API auth | `X-API-Key` header on every endpoint | Validated server-side; key never enters the browser bundle |
| Destructive ops | `X-Admin-Token` second secret on `DELETE /leads/clear` | Defence-in-depth: a leaked API key cannot wipe the DB |
| AI/job abuse | `slowapi` per-IP rate limits on `/ask`, `/draft-*`, `/insights`, `/execute`, `/upload`, `/hunt-*`, `/discovery/start`, `/process-all`, `/enrich/start`, `/leads/clear` | Gemini billing + Playwright spawn protection |
| Polling abuse | Per-IP caps on `/leads`, `/stats`, `/audit-status` | Reads can still flood the backend |
| Database | RLS + revoke on data tables | Even if the anon key leaks, no rows are readable |
| Schema migration | Narrow `add_lead_column(text)` SECURITY DEFINER RPC | Replaces unsafe generic `exec_sql` |
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
- `SMTP_*` (optional, for outreach)

### Frontend `.env.local`
- `BACKEND_URL` — server-side only (used by `/api/proxy/[...path]`)
- `API_SECRET_KEY` — server-side only, **must not** be prefixed `NEXT_PUBLIC_`
- `NEXT_PUBLIC_SUPABASE_URL` — public
- `NEXT_PUBLIC_SUPABASE_ANON_KEY` — public (RLS makes it harmless)

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

The limiter honours `X-Forwarded-For` so the trusted Next.js proxy passes the
real client IP through.

## Reporting

Email security issues privately rather than opening a public issue.
