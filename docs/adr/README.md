# Architecture Decision Records

This folder records the major architectural choices that shape the
LeadDataScraper codebase. Each ADR is short and pointed: **why this and not
that, what we gain, what we accept losing.** New devs read these before
proposing refactors that fight a pinned decision.

## Format

Lightweight [Nygard](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions)
style. Every record has four sections:

- **Context** — the forces in play when the decision was made
- **Decision** — what we chose, in one paragraph
- **Consequences** — both wins and trade-offs
- **Status** — `Proposed` / `Accepted` / `Superseded by ADR-XXX`

Dates are when the decision was ratified, not when the doc was written.

## Index

| # | Decision | Status |
|---|---|---|
| [001](001-single-tenant-by-design.md) | Single-tenant by design (the `OPERATOR_EMAIL` invariant) | Accepted |
| [002](002-fastapi-not-django.md) | FastAPI, not Django, for the backend | Accepted |
| [003](003-supabase-postgrest-not-direct-pg.md) | Supabase PostgREST, not a direct Postgres connection | Accepted |
| [004](004-playwright-for-discovery-aiohttp-for-audit.md) | Playwright for Discovery, aiohttp for Audit | Accepted |
| [005](005-no-soft-delete.md) | No soft delete (Faza 5.18) | Accepted |
| [006](006-gemini-not-openai.md) | Google Gemini, not OpenAI / Anthropic, for AI | Accepted |
| [007](007-render-not-vercel-for-backend.md) | Render, not Vercel, for the backend | Accepted |

## Writing a new ADR

1. `cp 001-single-tenant-by-design.md NNN-short-slug.md`
2. Fill in the four sections.
3. Mark **Status: Proposed**.
4. Open a PR. Reviewers debate the call before it lands as **Accepted**.
5. Append a row to the index above. Keep IDs monotonically increasing — never
   renumber existing files.
6. Supersession: a new ADR's **Status** points back at the old one
   (`Supersedes ADR-XXX`), and the old ADR is edited once — its status flips
   to `Superseded by ADR-NNN`. No content rewrite. The history is the point.
