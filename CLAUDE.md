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
- All endpoints (except `/` health check) require `X-API-Key` header — validated by `verify_api_key` dependency
- API key is set via `API_SECRET_KEY` env var in backend `.env`
- Frontend sends the key automatically via `apiFetch()` wrapper in `frontend/utils/apiConfig.ts`
- Frontend key is stored in `NEXT_PUBLIC_API_KEY` in `frontend/.env.local`
- CORS restricted to specific methods (`GET/POST/PUT/DELETE/OPTIONS`) and headers (`Content-Type/Authorization/X-API-Key`)
- All POST endpoints use Pydantic models for input validation (no raw `dict` payloads)
- Error responses never leak internal exception details

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
- Semantic surface tokens: `--surface-muted`, `--surface-subtle`, `--surface-elevated`, `--border-muted`, `--border-subtle`
- Color tint tokens: `--primary-tint-5/10/15/20`, `--success-tint`, `--warning-tint`, `--error-tint`, `--linkedin-tint`
- All interactive elements must meet 44px minimum touch target (`--touch-target-min`)
- Z-index scale: sidebar=100, mobile-backdrop=199, mobile-sidebar=200, chat=400, modals=500
- Modals require: `role="dialog"`, `aria-modal="true"`, `aria-labelledby`, ESC key handler
- All buttons need `aria-label` when icon-only
- No `any` types in TypeScript — define proper interfaces
- Font: Inter (not Outfit or other AI-trendy fonts)
- No gradient text, no excessive glassmorphism — keep design clean and functional

## Available Design Skills (Impeccable)
Installed via `npx skills add pbakaus/impeccable`. Use as slash commands:
/polish, /audit, /animate, /bolder, /quieter, /distill, /critique, /colorize,
/harden, /delight, /clarify, /adapt, /onboard, /normalize, /extract,
/teach-impeccable, /optimize, /overdrive, /arrange, /typeset, /frontend-design
