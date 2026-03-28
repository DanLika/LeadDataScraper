# LeadDataScraper

## Project Overview
Lead data scraping and enrichment pipeline with Supabase backend and Next.js dashboard frontend.

## Tech Stack
- **Backend**: Python, FastAPI, Supabase (database), Playwright, Google GenAI
- **Frontend**: Next.js (App Router), React 19, TypeScript, Recharts, Lucide icons

## Frontend Architecture
- `frontend/app/page.tsx` — Main dashboard (lead inventory, health chart, filters, modals)
- `frontend/app/insights/page.tsx` — Analytics & AI strategic analysis
- `frontend/app/campaigns/page.tsx` — Outreach campaign management
- `frontend/app/components/AIChat.tsx` — Floating AI chat assistant
- `frontend/app/components/Sidebar.tsx` — Navigation sidebar with insights widget
- `frontend/app/globals.css` — Design tokens and global styles
- `frontend/utils/apiConfig.ts` — API base URL config

## Frontend Conventions
- Use CSS custom properties (design tokens) from `globals.css` — never hardcode colors
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
