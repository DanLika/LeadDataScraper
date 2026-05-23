# Onboarding — LeadDataScraper

> **Target: productive in under one day.** Start at the top, run every command,
> open an issue when you hit a snag the doc doesn't cover. If anything below
> blocks you for more than 30 minutes, ping the maintainer — silent struggle
> means the doc has a gap.

This guide is a single read-through. The companion docs are listed in
[§9](#9-where-to-look-when-stuck); skim those after you've finished here.

---

## 0. Prerequisites

| Tool | Version | Why |
|---|---|---|
| **Python** | **3.10+** (the prod Docker image is `mcr.microsoft.com/playwright/python:v1.40.0-jammy` — whatever python version it ships with is the prod target; match within a minor version locally) | Backend, scripts, tests |
| **Node.js** | **18+** (LTS) | Frontend, e2e tests |
| **npm** | bundled with Node | Frontend package manager |
| **Git** | recent | Source control |
| **`gh` CLI** | latest | PRs, issue triage, `gh issue list`, `gh pr create` |
| **Docker** | recent | Optional locally; required for the prod-parity backend build |
| **`pip-tools`** | latest | `pip install pip-tools` once — needed for `make lock-python` |
| **OS** | macOS / Linux | Windows users: run in **WSL2** (the Docker base is Jammy Linux) |

Browser binaries for Playwright are installed in step 1 — don't try to install
them ahead of time.

---

## 1. Clone & install

```bash
git clone https://github.com/<owner>/LeadDataScraper.git
cd LeadDataScraper
```

### 1a. Backend (Python)

```bash
python3 -m venv .venv
source .venv/bin/activate   # (or .venv\Scripts\activate on Windows)
pip install -r requirements.txt
playwright install chromium  # Discovery + Deep Hunt need this
```

`requirements.txt` is the **lockfile** with sha256 hashes; never hand-edit it.
Add direct deps to `requirements.in` and regenerate with
`make lock-python` (see [§4d](#4d-managing-python-dependencies)).

### 1b. Frontend (Next.js)

```bash
cd frontend
npm install
cd ..
```

If you'll run end-to-end tests:

```bash
cd frontend
npm run e2e:install   # installs chromium + firefox + webkit (canonical script in package.json)
cd ..
```

### 1c. Local-CI parity hooks

```bash
make install-hooks      # one-shot install of .pre-commit-config.yaml hooks
```

The pre-commit hooks (ruff lint + format, selective mypy, secret scan, semgrep,
custom workflow-pin guard) are the **same scripts CI runs**. Any drift between
local and CI is itself the alarm — fix it locally before pushing.

To temporarily skip on a WIP commit: `git commit --no-verify`. Don't push
`--no-verify` commits to a PR branch; the `ci.yml::pre-commit (local-CI
parity)` gate will catch them anyway.

---

## 2. Environment variables

The full inventory + rotation policy lives in
**[`docs/secret-inventory.md`](secret-inventory.md)** — 29 secrets, blast-radius
tiered. Read it before touching anything in production. For local dev, you
need a **subset**.

### 2a. Backend (`./.env`)

Copy the template and fill in:

```bash
cp .env.example .env
```

> ⚠️ **`.env.example` is missing `ADMIN_TOKEN`** — add the line yourself after
> copying:
>
> ```bash
> echo "" >> .env
> echo "# X-Admin-Token for DELETE /leads/clear (must match frontend/.env.local)" >> .env
> echo "ADMIN_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" >> .env
> ```
>
> Without it the Next.js proxy injects an empty header and the
> **Clear All Leads** button 403s on day 1 with no obvious cause.
> (Tracking issue: patch `.env.example` to add the placeholder line.)

Minimum to boot:

| Var | Source for dev |
|---|---|
| `SUPABASE_URL` | Your own throw-away Supabase project (free tier OK) |
| `SUPABASE_SERVICE_ROLE_KEY` | Same project → Settings → API |
| `GEMINI_API_KEY` | Google AI Studio free tier — sufficient for dev |
| `API_SECRET_KEY` | Any random 32+ char string. Run `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `ADMIN_TOKEN` | Same — `secrets.token_urlsafe(32)`. Must match the frontend's `ADMIN_TOKEN` |
| `ALLOWED_ORIGINS` | `http://localhost:3000` |

Optional (set when you need the behaviour):

| Var | When to set |
|---|---|
| `OPERATOR_NAME` | If you're testing outreach drafts and want a non-`"Your Name"` signature |
| `OPERATOR_EMAIL` | If you want the single-tenancy boot assertion (will fail if your Supabase project has > 1 auth user) |
| `QUERY_PROFILER` | `=1` to enable the N+1 profiler in dev (see `src/utils/query_profiler.py`) |

### 2b. Frontend (`./frontend/.env.local`)

**No template exists** — create the file yourself. Required keys:

| Var | Dev value |
|---|---|
| `BACKEND_URL` | `http://localhost:8000` |
| `API_SECRET_KEY` | **Same** value as backend's `API_SECRET_KEY` (proxy injects it) |
| `ADMIN_TOKEN` | **Same** value as backend's `ADMIN_TOKEN` |
| `ALLOWED_ORIGINS` | `http://localhost:3000` |
| `NEXT_PUBLIC_SUPABASE_URL` | Same as backend's `SUPABASE_URL` |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | The **anon** key from the same Supabase project (not service-role) |

> **Common mistake on day 1:** `API_SECRET_KEY` mismatched between backend
> `.env` and frontend `.env.local`. Every authed request 403s. The proxy gets
> the header from the frontend env, then the backend's `verify_api_key`
> compares against its env. They must match byte-for-byte.

### 2c. Supabase one-time setup

1. Create a project at <https://supabase.com> (free tier).
2. SQL Editor → paste the contents of `supabase_schema.sql` → run.
3. Auth → Users → **Add user** (email + password). This is your dev login.

That's it. The backend uses `service_role` so RLS is bypassed server-side;
the frontend uses `anon` + Auth cookies for the proxy gate.

---

## 3. Run it locally

Two terminals:

```bash
# terminal 1 — backend
source .venv/bin/activate
uvicorn backend.main:app --reload --port 8000
```

```bash
# terminal 2 — frontend
cd frontend
npm run dev
```

Open <http://localhost:3000>. You'll be redirected to `/login` — sign in with
the Supabase Auth user from §2c. You land on the dashboard.

**Sanity checks:**

```bash
# liveness probe (no auth required)
curl -s http://localhost:8000/

# stats (auth required — won't work without the proxy)
curl -s http://localhost:8000/stats -H "X-API-Key: <API_SECRET_KEY>"
```

> **Cold start ~1.1s** locally. If yours is slower, you've likely re-introduced
> eager imports of pandas / AgenticRouter / TaskOrchestrator. See CLAUDE.md
> "Cold-start lazy imports" — there are pinned reasons not to.

---

## 4. Run the test suite

### 4a. Offline tests (CI default, ~5s, no `GEMINI_API_KEY` needed)

```bash
pytest tests/
```

If a live-tier test trips because you didn't export the Gemini key, that test
auto-skips — the suite still passes.

### 4b. Live AI tests (run before model / prompt changes)

```bash
GEMINI_API_KEY=<your-key> pytest tests/test_outreach_golden_set.py \
  tests/test_linkedin_golden_set.py tests/test_outreach_hallucination.py \
  tests/test_ask_determinism.py tests/test_i18n_outreach.py \
  tests/test_refusal_boundaries.py tests/test_json_compliance.py \
  tests/test_ai_cost_budget.py tests/test_insights_quality.py \
  tests/test_campaign_diversity.py
```

The `test_ai_cost_budget.py` print-out is the back-of-envelope number you
should care about — see CLAUDE.md "Critical pinned findings".

### 4c. Frontend tests

```bash
cd frontend
npm test          # node --test on .mjs unit tests (cookie floor, url, …)
npm run e2e       # Playwright e2e (needs the dev stack running)
```

The e2e suite expects backend + frontend up. Set the env vars listed in
[`docs/e2e-and-frontend-contracts.md`](e2e-and-frontend-contracts.md) for any
opt-in suite (`RUN_JWT_MANIPULATION_E2E=1`, etc.).

### 4d. Managing Python dependencies

Direct deps live in `requirements.in`. Add the line there, then:

```bash
make lock-python   # regenerates requirements.txt with sha256 hashes
```

Commit `requirements.in` + `requirements.txt` together. The
`ci.yml::lockfile-sync` job re-runs `pip-compile --dry-run` and diffs; out-of-sync
turns the gate red.

### 4e. Pre-commit (replays CI's local-CI parity gate)

```bash
make pre-commit-all   # runs every hook against every file
```

Use before pushing a large PR. The `ci.yml::pre-commit (local-CI parity)` job
runs the same hooks — a green local run is a green CI step.

---

## 5. Architecture in one page

```
┌───────────────────────────────────────────────────────────────────────────┐
│  Browser  (Next.js 16 App Router, React 19)                               │
│  - dashboard / insights / campaigns / login                               │
│  - Supabase Auth session cookies (HttpOnly, Secure, SameSite=Lax)         │
└────┬──────────────────────────────────────────────────────────────────────┘
     │ same-origin fetch with auth cookie
     ▼
┌───────────────────────────────────────────────────────────────────────────┐
│  Next.js /api/proxy/[...path]   (frontend/app/api/proxy/[...path]/route)  │
│  - verifies Supabase Auth session (`auth.getUser()`)                      │
│  - injects X-API-Key (and X-Admin-Token on DELETE /leads/clear)           │
│  - re-emits trusted X-Forwarded-For (strips client-supplied)              │
│  - Origin allowlist on state-changing methods                             │
│  - stamps Cache-Control: no-store on responses                            │
└────┬──────────────────────────────────────────────────────────────────────┘
     │ HTTPS, internal-network (Render) or localhost (dev)
     ▼
┌───────────────────────────────────────────────────────────────────────────┐
│  FastAPI backend   (backend/main.py)                                      │
│  - verify_api_key on every route except GET /                             │
│  - slowapi rate limits per route (3/min – 60/min)                         │
│  - module-level lazy singletons: db, router, auditor, orchestrator        │
│                                                                           │
│  src/core/agentic_router.py   ─►  Gemini (route /ask, draft outreach,…)   │
│  src/core/task_orchestrator.py ─►  background jobs (audit/hunt/enrich)    │
│  src/core/parallel_auditor.py ─►  SEO audit (aiohttp+regex, NO Gemini)    │
│  src/scrapers/discovery_engine.py ─► Google Maps (Playwright)             │
│  src/scrapers/enrichment_engine.py ─► shared-browser pool + Gemini summary│
│  src/processors/leadhunter.py  ─►  Deep Hunt + Gemini summary             │
│  src/utils/ssrf_guard.py       ─►  outbound HTTP gate (every fetch)       │
│  src/utils/supabase_helper.py  ─►  PostgREST client (service_role)        │
└────┬──────────────────────────────────────────────────────────────────────┘
     │ PostgREST over HTTPS
     ▼
┌───────────────────────────────────────────────────────────────────────────┐
│  Supabase (managed Postgres)                                              │
│  - tables: leads, campaigns, campaign_messages, orchestration_jobs        │
│  - RLS deny-all on all four; service_role bypasses                        │
│  - statement_timeout per role (anon 3s / authenticated 8s / service 30s)  │
│  - daily checks: schema-drift, RLS, FK integrity, JSONB shape, orphans,   │
│    zombies, NULL ratios, query plans, bloat, slow queries                 │
└───────────────────────────────────────────────────────────────────────────┘

External: Google Maps (Discovery), Google Gemini (all AI), Google AI Studio
         (usage dashboard), Render (host), GHCR (image registry).
```

**Single-tenant by design.** No `owner_user_id` filter on per-resource
endpoints; `OPERATOR_EMAIL` makes this invariant trip loudly at boot if a
second Supabase Auth user appears.

For depth on any box, **CLAUDE.md** in the repo root is the canonical project
brief — every defense, every test, every contract is documented there. Reach
for it before any non-trivial change.

---

## 6. Your first task

There is **no `good first issue` label populated yet** — this is a single-
operator project, and the maintainer hasn't seeded that backlog. Starter
work, in increasing order of unknowns:

### 6a. Capture the operator-guide screenshots (~1 hour)

[`docs/runbooks/operator-guide.md`](runbooks/operator-guide.md) §10 ships a
10-image checklist with empty placeholders. Boot the dev stack, walk each
flow, save the PNGs, commit. Zero code, but you'll touch every page of the
app and pick up the UI vocabulary fast.

### 6b. Pick from auto-opened maintenance issues

Three workflows maintain one canonical GitHub issue each. Any of these is a
real, scoped task with a clear definition of done:

| Label | Workflow | Typical task |
|---|---|---|
| `flaky` | `flakiness-detector.yml` | Fix a flaky test (data in gist `flaky-tests.json`) |
| `mutation-coverage` | `mutation-test.yml` | Raise mutmut kill-rate above 80% on a security-critical module |
| `workflow-drift` | `workflow-drift.yml` | Reconcile a workflow file change with `.github/workflow-hashes.json` (`make workflow-hashes`) |

Check `gh issue list --label flaky --state open` etc.

### 6c. Pull a TODO from the code

Grep for `# TODO` / `# FIXME` in `src/` and `backend/`. Each is a known
shortcut with a note on the right answer.

```bash
git grep -nE 'TODO|FIXME|XXX' src/ backend/ frontend/app
```

### 6d. Once you've shipped one PR, ask for triage

Ping the maintainer in your first PR: *"Want me to pick up X, Y, or Z next?"*
The maintainer will label one as `good first issue` (or assign directly) and
the backlog starts populating.

---

## 7. Code review process

### 7a. Branch + commit

```bash
git checkout -b feat/<short-slug>     # or fix/, chore/, docs/, refactor/
# … work …
git commit                            # interactive editor — read 7b first
git push -u origin HEAD
```

### 7b. Commit messages

**Conventional Commits format** is required by `ci.yml::pr-hygiene` (it gates
the merge button). The `caveman-commit` skill in this repo can draft these
for you (`/caveman:caveman-commit`).

```
feat(scope): one-line subject (≤ 50 chars)

Optional body explaining the why if non-obvious. Wrap at 72.

Co-Authored-By: …
```

Allowed types: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`, `perf`,
`build`, `ci`, `revert`.

### 7c. PR

```bash
gh pr create --fill   # uses the commit message as the PR title + body
```

PR title must also be Conventional Commits (the squash-merge subject is the
PR title — the gate rejects non-conformant titles).

### 7d. Required checks (~20 from `ci.yml`)

Every PR must clear, before merge:

- `pytest` + ≥ 95% coverage
- `npm test` (frontend unit)
- `pre-commit (local-CI parity)`
- `pip-audit`, `npm audit moderate+`, `gitleaks`
- `lockfile-sync`, `license-check`, `flaky-gate`
- `semgrep`, `ruff`, `mypy --strict src/`, `eslint --max-warnings 0`
- `Playwright E2E` (chromium + firefox + webkit + iphone-14 + pixel-7)
- `schema-drift`, `referential-integrity`, `query-plans`, `concurrency-tests`
- `Lighthouse`, `container-scan` (Trivy + Grype + SBOM)
- Conventional Commits title, PR size gate

Full list + the post-merge sweeps in
[`docs/ci-architecture.md`](ci-architecture.md).

### 7e. `/ultrareview` (optional, before requesting human review)

A user-triggered multi-agent cloud review. **It's billed** — use when you
want a second pair of eyes before pinging the maintainer.

```
/ultrareview          # reviews the current branch
/ultrareview <PR#>    # reviews a specific PR
```

You cannot launch it from inside Claude Code — type the slash command
yourself in the CLI.

### 7f. Approve & merge

The maintainer reviews. Code-owner approval is required (see
`.github/CODEOWNERS`). Merge mode: **squash**. Render auto-deploys on push to
main (see §8).

---

## 8. Deploy process

### 8a. Production = Render

Two services, defined in `render.yaml`:

- **Backend** (`lead-scraper-backend`): Docker build off `./Dockerfile`,
  Playwright base image. Env vars in §2a, declared `sync: false` (values set
  in the Render dashboard, never committed).
- **Frontend**: Next.js. Env vars in §2b.

### 8b. Two deployment paths

```
              ┌──────────────────────────────────────┐
              │  push to main                        │
              │  → deploy-backend.yml                │
              │    builds image → GHCR → SLSA3       │
              │    provenance → cosign verify →      │
              │    Render API rollout on digest      │
              └──────────────────────────────────────┘

              ┌──────────────────────────────────────┐
              │  push tag v*                         │
              │  → release.yml                       │
              │    same chain, plus release-drafter  │
              │    publishes a GitHub Release        │
              └──────────────────────────────────────┘
```

**Render service must be in "Deploy from existing image" mode** for the chain
to gate rollout on the cosign-verified digest. Forged GHCR images (leaked PAT
push) fail cosign verify and never reach Render.

### 8c. Smoke + drift after deploy

- `post-deploy-smoke.yml` runs every deploy — synthetic checks against the
  live URL. See [`docs/post-deploy-smoke.md`](post-deploy-smoke.md).
- `synthetic-monitor.yml` runs hourly — see
  [`docs/synthetic-monitor.md`](synthetic-monitor.md).

### 8d. Rollback

Two options, in increasing severity:

1. **Render dashboard → Deploys → previous green → Roll Back**. Restores the
   previous image immediately.
2. `git revert <commit> && git push` — the revert PR re-triggers the chain,
   which produces a new green image. Use this when you want the change
   captured in git history.

### 8e. Secrets rotation

Cadence in [`docs/secret-inventory.md`](secret-inventory.md):

- **Monthly:** `SUPABASE_SERVICE_ROLE_KEY`, `RENDER_API_KEY`,
  `SUPABASE_DATABASE_URL`
- **Quarterly:** `API_SECRET_KEY`, `ADMIN_TOKEN`, `GEMINI_API_KEY`

OIDC is used where supported (GHCR, Sigstore Fulcio). Render/Supabase/Gemini
remain PAT-only until upstream support lands.

---

## 9. Where to look when stuck

| Doc | What's in it |
|---|---|
| **`CLAUDE.md`** (repo root) | Canonical project brief: every defense, contract, test invariant, performance guard. Reach for it first. |
| **`docs/runbooks/operator-guide.md`** | Day-to-day operations — discover/audit/hunt/draft/campaigns/export, failure recovery, Gemini cost map. |
| **`docs/runbooks/incidents.md`** | Incident response. 5 SEV-1/SEV-2 scenarios with detection → triage → mitigation → post-mortem template. Read once before the first incident; ⌘F during one. |
| **`docs/adr/`** | Architecture Decision Records — *why* the stack is shaped this way. Read before proposing a refactor that fights a pinned decision. |
| **`docs/observability.md`** | Sentry error tracking + APM. Wiring, source maps, alerts, PII scrubbing, verification procedure. |
| **`docs/alerting.md`** | Discord alert routing for the 5 operational signals beyond Sentry (synthetic monitor, storage, mutation, cold-start, cert-expiry). |
| **`docs/ci-architecture.md`** | 15 GitHub Actions workflows: what each one gates, how the security/release/drift sweeps interact. |
| **`docs/secret-inventory.md`** | Every secret, who owns it, rotation cadence, blast radius. |
| **`docs/e2e-and-frontend-contracts.md`** | E2E test surface + filter ↔ URL vocab + offline-queue contract + cross-tab behaviour. |
| **`docs/post-deploy-smoke.md`** | What runs after every deploy. |
| **`docs/synthetic-monitor.md`** | Hourly synthetic monitoring. |
| **`docs/findings/`** | Post-mortems on incidents resolved (`YYYY-MM-DD-<slug>.md`). |
| **`SECURITY.md`** | How to report a vulnerability. |
| Render dashboard | Logs, manual deploy, env-var values |
| Supabase dashboard | DB Studio, Auth users, Logs, Advisors |
| Google AI Studio | Gemini usage / billing |

Still stuck? Open an issue or DM the maintainer. **Silent struggle means the
doc has a gap** — you flagging it is the right move.

---

## 10. Glossary

- **Lead** — a row in `leads`. Sourced from CSV upload or Google Maps scrape.
  Keyed on `unique_key` (stable across runs).
- **Orchestration job** — a background task: audit / hunt / enrich / discovery
  / pipeline. Row in `orchestration_jobs`. Status: `starting / running /
  completed / failed / stopped`.
- **Audit** — SEO + tech-stack scan via aiohttp + regex. **No Gemini.**
- **Hunt** (or "Deep Hunt") — Playwright contact-extraction + Gemini
  summarisation. 3–4 Gemini calls per lead.
- **Discovery** — Google Maps scrape via Playwright. **No Gemini.**
- **Pipeline** — audit → enrich → hunt in one orchestration job.
- **Draft** — Gemini-generated outreach email or LinkedIn DM.
- **Campaign** — a saved bundle of leads + channel (email / linkedin / multi)
  + status (draft / active / paused / completed) + generated messages.
- **Operator** — the single human running this stack. There is no second user.

---

## Quick first-day checklist

- [ ] `git clone` the repo
- [ ] Backend: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && playwright install chromium`
- [ ] Frontend: `cd frontend && npm install`
- [ ] `make install-hooks`
- [ ] Create Supabase project, run `supabase_schema.sql`, add an auth user
- [ ] Fill `.env` (backend) and `frontend/.env.local` (matching `API_SECRET_KEY` + `ADMIN_TOKEN` on both sides)
- [ ] `uvicorn backend.main:app --reload --port 8000` boots cleanly
- [ ] `cd frontend && npm run dev` boots cleanly
- [ ] Sign in at <http://localhost:3000/login>, see the dashboard
- [ ] `pytest tests/` passes (offline tier)
- [ ] `cd frontend && npm test` passes
- [ ] Read `CLAUDE.md` end-to-end (skim sections that don't apply)
- [ ] Pick a §6 starter task and open a PR
- [ ] Get the PR through CI green, request review

If you finish the checklist on day 1, you're productive. Welcome.
