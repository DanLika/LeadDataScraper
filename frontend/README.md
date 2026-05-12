# LeadDataScraper Frontend

Next.js (App Router) dashboard for the LeadDataScraper pipeline.

## Architecture

The browser **never** holds the backend API key or queries Supabase directly.
All data flows through a server-side proxy:

```
Browser  →  /api/proxy/[...path]  →  FastAPI backend (X-API-Key injected here)
            ↑ Next.js server route
```

- `frontend/app/api/proxy/[...path]/route.ts` — proxy that forwards every
  method (GET/POST/PUT/DELETE/PATCH/OPTIONS) to `BACKEND_URL` and attaches
  `X-API-Key` from the server-side `API_SECRET_KEY` env var.
- `frontend/utils/apiConfig.ts` — `apiFetch()` wrapper. Callers use
  `apiFetch(\`${API_BASE_URL}/leads\`)`; `API_BASE_URL` is `/api/proxy`.
- Frontend pages (`app/page.tsx`, `app/insights/page.tsx`) read leads only via
  `/leads`. Supabase RLS blocks anon access to the data tables.

## Environment

Create `frontend/.env.local`:

```bash
# Server-side only — used by the proxy route. NOT NEXT_PUBLIC_*.
BACKEND_URL=http://127.0.0.1:8000
API_SECRET_KEY=<same value as backend .env>

# Platform-injected client-IP header the proxy re-emits as X-Forwarded-For.
# Vercel (default): x-vercel-forwarded-for
# Render / generic XFF hosts: x-forwarded-for
TRUSTED_CLIENT_IP_HEADER=x-vercel-forwarded-for

# Public — Supabase publishable key. RLS prevents data access.
NEXT_PUBLIC_SUPABASE_URL=https://<project>.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=<publishable anon key>
```

> ⚠️ Do **not** add `NEXT_PUBLIC_API_KEY`. It existed historically and was
> shipped to every browser. Anyone who loaded the site before the rotation
> still holds it — rotate `API_SECRET_KEY` on the backend.

## Security headers

`next.config.ts` sets a strict header bundle on every route:

- `Content-Security-Policy` — `script-src 'self'` in production (dev relaxes
  to allow Next HMR); `connect-src` whitelists the Supabase URL + its `wss:`
  realtime variant only.
- `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: strict-origin-when-cross-origin`, `Strict-Transport-Security`
  (2y + preload), `Permissions-Policy` (camera/mic/geolocation off).
- `productionBrowserSourceMaps: false` — sourcemaps stay server-side.

## Run

```bash
npm install
npm run dev   # http://localhost:3000
```

Make sure the FastAPI backend is running on `BACKEND_URL` first.

## Build & test

```bash
npm run build
npx tsc --noEmit
```
