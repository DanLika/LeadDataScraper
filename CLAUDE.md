# LeadDataScraper

## Project Overview
Lead data scraping and enrichment pipeline with Supabase backend and Next.js dashboard frontend.

## Tech Stack
- **Backend**: Python, FastAPI, Supabase (database), Playwright, Google GenAI
- **Frontend**: Next.js (App Router), React 19, TypeScript, Recharts, Lucide icons

## Backend Architecture
- `backend/main.py` — FastAPI app with all API endpoints (leads, campaigns, orchestrator, AI chat, exports)
- `src/utils/supabase_helper.py` — Supabase client wrapper (uses `SUPABASE_SERVICE_ROLE_KEY` for backend ops)
- `src/scrapers/seo_audit.py` — Async SEO auditor with tech stack detection
- `src/scrapers/discovery_engine.py` — Google Maps lead discovery via Playwright
- `src/core/task_orchestrator.py` — Background job orchestration for audits, hunts, enrichment
- `src/core/agentic_router.py` — AI instruction routing (natural language → task execution)

## API Security
- **Frontend access requires a Supabase Auth session.** Root `frontend/middleware.ts`
  (wraps `utils/supabase/middleware.ts`) redirects anonymous traffic to `/login`.
  The `/api/proxy/[...path]` handler re-runs `auth.getUser()` and 401s on
  unauthenticated fetch/XHR. State-changing methods also reject foreign `Origin`.
  Provision users in the Supabase Auth dashboard (no public signup).
- Auth middleware public-path allowlist (`/login`, `/auth`, `/api/auth`) uses
  exact match or trailing-slash subpath — not raw `startsWith`. Prevents a
  future `/login-internal` or `/authentication-guide` route from being silently
  unauthenticated by string-prefix overlap.
- `/login?next=<path>` is sanitised by `sanitizeNext()` in
  `frontend/app/login/page.tsx`. Only same-origin relative paths are accepted
  (must start with `/`, must NOT start with `//` or `/\`). Closes open-redirect
  → phishing-assist on the auth flow.
- Supabase session cookies set via `setAll()` in
  `frontend/utils/supabase/middleware.ts` are floored to `SameSite=Lax`,
  `HttpOnly=true`, `Secure=true` (prod) — Supabase's own options win via spread
  order, but the floor protects against a future SDK change that loosens
  defaults.
- All endpoints (except `/` health check) require `X-API-Key` header — validated by `verify_api_key` dependency (constant-time compare via `secrets.compare_digest`)
- API key is set via `API_SECRET_KEY` env var in backend `.env`
- Interactive docs (`/docs`, `/openapi.json`, `/redoc`) are **disabled by default**.
  Enable in dev via `ENABLE_DOCS=true`. Never set in production.
- **Frontend does NOT hold the API key.** The browser calls a same-origin Next.js
  proxy at `/api/proxy/[...path]` (see `frontend/app/api/proxy/[...path]/route.ts`)
  which injects `X-API-Key` from the server-side `API_SECRET_KEY` env var.
- Destructive endpoint `DELETE /leads/clear` additionally requires
  `X-Admin-Token` matching `ADMIN_TOKEN` env (defense-in-depth even if API key leaks).
- Required env vars (see `.env.example`):
  - Backend `.env`: `API_SECRET_KEY`, `ADMIN_TOKEN`, `SUPABASE_URL`,
    `SUPABASE_SERVICE_ROLE_KEY`, `GEMINI_API_KEY`, `ALLOWED_ORIGINS`
  - Frontend `.env.local`: `BACKEND_URL` (server-side, points at FastAPI),
    `API_SECRET_KEY` (server-side, NOT `NEXT_PUBLIC_*`),
    `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- Rate limiting: AI and destructive endpoints capped via `slowapi`. See
  `backend/main.py` decorators. `headers_enabled=False` — `X-RateLimit-*` not
  emitted (slowapi requires `response: Response` param to inject; we don't
  declare it on every endpoint).
- Rate-limit key derives from `X-Forwarded-For` set by the Next.js proxy.
  The proxy strips client-controlled XFF / X-Real-IP / Forwarded headers and
  re-emits XFF from the platform-injected header named in
  `TRUSTED_CLIENT_IP_HEADER` (default `x-vercel-forwarded-for`; set to
  `x-forwarded-for` on Render). Additionally, `_rate_limit_key` in
  `backend/main.py` only honours XFF when the request carries a valid
  `X-API-Key` (constant-time compared). Forged XFF without the key falls
  back to the TCP peer IP — so even if the FastAPI port is ever exposed
  directly, attackers cannot spoof XFF to spread load across rate-limit
  buckets.
- Browser security headers set in `frontend/next.config.ts`: CSP
  (`script-src 'self'` in prod; `connect-src` whitelists Supabase URL + wss),
  HSTS (2y + preload), `X-Frame-Options: DENY`, `X-Content-Type-Options`,
  `Referrer-Policy`, `Permissions-Policy` (camera/mic/geo off).
  `productionBrowserSourceMaps: false`.
- `/upload` streams the request body and aborts at 50 MB (`MAX_UPLOAD_BYTES`)
  with a 413 — no full-buffer DoS.
- Outbound HTTP from `seo_audit.py` and `enrichment_engine.py` runs through
  `src/utils/ssrf_guard.py` (`SSRFGuardResolver` + `assert_safe_url`) which
  rejects private / loopback / link-local / reserved / multicast IPs and known
  cloud metadata hostnames at DNS-resolve time. Hardens against SSRF and
  DNS-rebinding.
- Supabase RLS is enabled on `leads`, `campaigns`, `campaign_messages`,
  `orchestration_jobs`. Anon + authenticated roles are revoked. All reads/writes
  go through the backend, which uses `service_role` to bypass RLS server-side.
- Schema migrations use `add_lead_column(text)` RPC (allowlisted column-name
  regex). The generic `exec_sql` RPC has been removed.
- CORS restricted to specific methods (`GET/POST/PUT/DELETE/OPTIONS`) and headers (`Content-Type/Authorization/X-API-Key`)
- All POST endpoints use Pydantic models for input validation (no raw `dict` payloads)
- Error responses never leak internal exception details
- Global FastAPI exception handler converts any uncaught exception to JSON
  (`{"error": "Internal server error"}`, 500) so the Next.js proxy can always
  `.json()` the body without SyntaxError.

## Frontend Architecture
- `frontend/app/page.tsx` — Main dashboard (lead inventory, modals, orchestration)
- `frontend/app/insights/page.tsx` — Analytics & AI strategic analysis
- `frontend/app/campaigns/page.tsx` — Outreach campaign management (with sidebar + AI chat)
- `frontend/app/components/AIChat.tsx` — Floating AI chat assistant
- `frontend/app/components/Sidebar.tsx` — Navigation sidebar with insights widget
- `frontend/app/components/HealthChart.tsx` — PieChart health breakdown + stats grid
- `frontend/app/components/StatsCards.tsx` — 4 summary stat cards (Total, Pending, Risk, Healthy)
- `frontend/app/components/FilterBar.tsx` — Search, segment, status, and score filters
- `frontend/app/globals.css` — Design tokens and global styles
- `frontend/utils/apiConfig.ts` — API base URL, API key, and `apiFetch()` authenticated fetch wrapper

## Frontend Conventions
- Use CSS custom properties (design tokens) from `globals.css` — never hardcode colors
- Surface scale (solid, not glass): `--surface-base` < `--surface-subtle` < `--surface-elevated` < `--surface-muted` < `--surface-hover`
- Card surfaces use `--card-bg` + `--border-subtle` + `--card-shadow` (no backdrop-filter)
- Border scale: `--border-subtle`, `--border`, `--border-muted`
- Color tint tokens: `--primary-tint-5/10/15/20`, `--success-tint`, `--warning-tint`, `--error-tint`, `--linkedin-tint`
- Single brand hue: indigo `hsl(234, 89%, 64%)` via `--primary-hsl`. Secondary/accent reserved for charts only.
- Theming: dark default, light theme auto-applied via `@media (prefers-color-scheme: light)` and overridable with `[data-theme="light"]` on `:root`. Never hardcode rgba — all tokens flip between themes.
- Modal backdrop: use `.modal-backdrop` class (driven by `--modal-backdrop-bg`), never inline rgba
- Glass tokens (`--glass-bg`, `--glass-border`, `--glass-hover`) are legacy aliases mapped to solid surfaces — prefer the solid-surface names in new code
- All interactive elements must meet 44px minimum touch target (`--touch-target-min`)
- Z-index scale: sidebar=100, mobile-backdrop=199, mobile-sidebar=200, chat=400, modals=500
- Modals require: `role="dialog"`, `aria-modal="true"`, `aria-labelledby`, ESC key handler
- All buttons need `aria-label` when icon-only
- No `any` types in TypeScript — define proper interfaces
- Font: Inter (not Outfit or other AI-trendy fonts)
- No gradient text, no `linear-gradient` on UI chrome, no `backdrop-filter` blur (kept only on mobile drawer overlay)
- Mobile sidebar slides via `transform: translateX()`, never `left:` (avoid layout-triggering transitions)
- `prefers-reduced-motion: reduce` honored globally — disables all animations/transitions

## Available Design Skills (Impeccable)
Installed via `npx skills add pbakaus/impeccable`. Use as slash commands:
/polish, /audit, /animate, /bolder, /quieter, /distill, /critique, /colorize,
/harden, /delight, /clarify, /adapt, /onboard, /normalize, /extract,
/teach-impeccable, /optimize, /overdrive, /arrange, /typeset, /frontend-design

# context-mode — MANDATORY routing rules

You have context-mode MCP tools available. These rules are NOT optional — they protect your context window from flooding. A single unrouted command can dump 56 KB into context and waste the entire session.

## BLOCKED commands — do NOT attempt these

### curl / wget — BLOCKED
Any Bash command containing `curl` or `wget` is intercepted and replaced with an error message. Do NOT retry.
Instead use:
- `ctx_fetch_and_index(url, source)` to fetch and index web pages
- `ctx_execute(language: "javascript", code: "const r = await fetch(...)")` to run HTTP calls in sandbox

### Inline HTTP — BLOCKED
Any Bash command containing `fetch('http`, `requests.get(`, `requests.post(`, `http.get(`, or `http.request(` is intercepted and replaced with an error message. Do NOT retry with Bash.
Instead use:
- `ctx_execute(language, code)` to run HTTP calls in sandbox — only stdout enters context

### WebFetch — BLOCKED
WebFetch calls are denied entirely. The URL is extracted and you are told to use `ctx_fetch_and_index` instead.
Instead use:
- `ctx_fetch_and_index(url, source)` then `ctx_search(queries)` to query the indexed content

## REDIRECTED tools — use sandbox equivalents

### Bash (>20 lines output)
Bash is ONLY for: `git`, `mkdir`, `rm`, `mv`, `cd`, `ls`, `npm install`, `pip install`, and other short-output commands.
For everything else, use:
- `ctx_batch_execute(commands, queries)` — run multiple commands + search in ONE call
- `ctx_execute(language: "shell", code: "...")` — run in sandbox, only stdout enters context

### Read (for analysis)
If you are reading a file to **Edit** it → Read is correct (Edit needs content in context).
If you are reading to **analyze, explore, or summarize** → use `ctx_execute_file(path, language, code)` instead. Only your printed summary enters context. The raw file content stays in the sandbox.

### Grep (large results)
Grep results can flood context. Use `ctx_execute(language: "shell", code: "grep ...")` to run searches in sandbox. Only your printed summary enters context.

## Tool selection hierarchy

1. **GATHER**: `ctx_batch_execute(commands, queries)` — Primary tool. Runs all commands, auto-indexes output, returns search results. ONE call replaces 30+ individual calls.
2. **FOLLOW-UP**: `ctx_search(queries: ["q1", "q2", ...])` — Query indexed content. Pass ALL questions as array in ONE call.
3. **PROCESSING**: `ctx_execute(language, code)` | `ctx_execute_file(path, language, code)` — Sandbox execution. Only stdout enters context.
4. **WEB**: `ctx_fetch_and_index(url, source)` then `ctx_search(queries)` — Fetch, chunk, index, query. Raw HTML never enters context.
5. **INDEX**: `ctx_index(content, source)` — Store content in FTS5 knowledge base for later search.

## Subagent routing

When spawning subagents (Agent/Task tool), the routing block is automatically injected into their prompt. Bash-type subagents are upgraded to general-purpose so they have access to MCP tools. You do NOT need to manually instruct subagents about context-mode.

## Output constraints

- Keep responses under 500 words.
- Write artifacts (code, configs, PRDs) to FILES — never return them as inline text. Return only: file path + 1-line description.
- When indexing content, use descriptive source labels so others can `ctx_search(source: "label")` later.

## ctx commands

| Command | Action |
|---------|--------|
| `ctx stats` | Call the `ctx_stats` MCP tool and display the full output verbatim |
| `ctx doctor` | Call the `ctx_doctor` MCP tool, run the returned shell command, display as checklist |
| `ctx upgrade` | Call the `ctx_upgrade` MCP tool, run the returned shell command, display as checklist |
