# CI/CD architecture

Canonical map of every workflow under `.github/workflows/`, when each
fires, what each gates, and the operator setup needed to make it green.
Future contributors (Claude included) should read this before touching
anything in `.github/`.

## Design principles

1. **Block at the gate, not at the commit.** Every safety property is
   asserted as a CI job whose red turns into a merge block via required
   status checks. Local hooks (pre-commit) are advisory parity, not the
   gate.
2. **One canonical issue per concern.** Flakiness, mutation coverage,
   workflow drift — each gets a single auto-updated tracker issue, not a
   stream of new issues per run.
3. **Pin everything.** All third-party action refs are 40-char commit
   SHAs with `# vX.Y.Z` comments (Codecov-2021 pattern). Dependabot
   bumps them. The `workflow-pin-guard` local pre-commit hook fails any
   commit that re-introduces a mutable `@vN` tag.
4. **Fail-closed.** Missing secret → job red. Missing snapshot entry →
   workflow-drift opens an issue. Unverified provenance → Render deploy
   never fires.
5. **Single-tenant operator.** This repo has one human (`@DanLika`) plus
   Dependabot. CODEOWNERS gates every path; auto-merge handles patch
   bumps after every check passes.

## Workflow inventory

| Workflow | Trigger | Role | Blocks merge? |
|---|---|---|---|
| `ci.yml` | `pull_request` | Primary PR gate — 15+ jobs | ✅ |
| `security.yml` | push to main + daily cron | Post-merge audit + DB invariant sweep | n/a (informational) |
| `main-matrix.yml` | push to main + daily cron | Python 3.11/3.12/3.13 + Node 20/22 compat | n/a |
| `deploy-backend.yml` | push to main (paths: backend/) | GHCR push → SLSA3 → cosign → Render rollout | n/a (deploy gate) |
| `release.yml` | push tag `v*` | Tagged release: build, sign, changelog, GH release, deploy | n/a |
| `pr-hygiene.yml` | `pull_request` | Conventional Commits title + size gate | ✅ |
| `dependabot-auto-merge.yml` | `pull_request_target` | `gh pr merge --auto` for patch PRs | n/a |
| `flakiness-detector.yml` | nightly cron | 3× parallel pytest → gist `flaky-tests.json` | n/a (feeds `flaky-gate` in ci.yml) |
| `mutation-test.yml` | weekly Sunday cron | mutmut on security-critical modules, 80% kill rate | n/a (issue tracker) |
| `workflow-drift.yml` | daily cron | Hash drift + untracked-commit audit on workflows | n/a (issue tracker) |
| `post-deploy-smoke.yml` | `repository_dispatch` (Render webhook) | Playwright smoke + auto-rollback | n/a (production gate) |
| `synthetic-monitor.yml` | scheduled (every 5 min) | Liveness probe → gist + Slack | n/a |
| `e2e.yml` | `pull_request` | Playwright E2E (chromium PR, full browsers on main) | ✅ |
| `migration-safety.yml` | `workflow_dispatch` only | Supabase migration dry-run + lock check | n/a (manual) |
| `backup-verify-deep.yml` | `workflow_dispatch` only | PITR backup verification (Supabase Pro+) | n/a (manual) |

## Per-workflow detail

### ci.yml — PR gate

Jobs (all must be green for merge, configured as required checks):

- **pytest (cov >= 95%)** — `python-tests`: `pytest --cov=src --cov-fail-under=95`.
- **npm test** — `frontend-tests`: `node --test utils/...`.
- **pre-commit (local-CI parity)** — runs `pre-commit run --all-files`; same hooks as `make install-hooks` locally.
- **pip-audit --strict** — fail on any fixable CVE in `requirements.txt`. Fork-guarded.
- **npm audit (moderate+)** — `--audit-level=moderate` on prod deps.
- **gitleaks (full git history)** — secret scan; `--exit-code 1`.
- **lockfile-sync** — re-runs `pip-compile --dry-run` on `requirements.in`, diffs against committed `requirements.txt`.
- **license-check** — `pip-licenses --fail-on "GPL;LGPL;AGPL..."` + `npx license-checker --failOn`.
- **flaky-gate** — fails if PR diff touches a file flagged flaky in the last 7 days.
- **semgrep** — `--config=auto --error`. Fork-guarded.
- **python-lint** — `ruff check src/ tests/` + `mypy --strict src/`.
- **eslint** — `npm run lint -- --max-warnings 0`.
- **playwright-e2e** — Boots backend + Next.js, runs Playwright against Supabase ephemeral branch. Fork-guarded.
- **schema-drift / referential-integrity / query-plans** — Supabase DB invariants. Fork-guarded.
- **Lighthouse CI** — reads `.lighthouserc.json`. Fork-guarded.
- **container-scan** — Build image → Trivy (CRITICAL + fixable HIGH) → Grype (`--fail-on high --only-fixed`) → Syft SBOM upload.

Concurrency: cancel-on-force-push (`cancel-in-progress: ${{ github.event_name == 'pull_request' }}`).

### security.yml — post-merge audit

Same security checks as ci.yml plus DB invariant sweeps (`schema-drift`, `referential-integrity`, `query-plans`, `jsonb-shapes`, `null-audit`, `orphans-zombies`). Push + daily cron. Daily run catches:
- Newly-disclosed CVEs in already-pinned deps
- Supabase Studio edits that bypassed migrations
- Direct-push secret commits the PR scan missed

### deploy-backend.yml — supply-chain locked deploy

Triggered on push to `main` if `backend/`, `src/`, `requirements.txt`, `Dockerfile`, or this workflow changes. Pipeline:

```
build (GHCR push) → provenance (SLSA3 reusable workflow) → verify-and-deploy
                                                              ↓
                                                  cosign verify-attestation
                                                              ↓
                                                  Render API rollout
```

Provenance verification pins issuer to `https://token.actions.githubusercontent.com` and certificate identity to `slsa-github-generator/.github/workflows/generator_container_slsa3.yml@refs/tags/v2.x.y`. Forged images at GHCR fail verify — Render never sees them.

### release.yml — tag-driven release

Triggers on tags matching `v*`. Six jobs: `guard-tag` (pusher must be DanLika), `build-and-sign` (semver tags on GHCR), `provenance` (same SLSA3 chain), `release` (release-drafter changelog + Syft SBOM + GH release), `deploy` (re-verify + Render API). Concurrency `cancel-in-progress: false` — never cancel a release mid-flight.

### dependabot-auto-merge.yml

`pull_request_target` event (needed for the bot to get a write token). Only patch updates auto-merge via `gh pr merge --auto --squash`. GitHub waits for every required check before actually merging. Minor + major Dependabot PRs stay manual.

### flakiness-detector.yml + ci.yml::flaky-gate

Nightly: 3× parallel pytest runs, mixed-outcome aggregator writes `flaky-tests.json` to gist. PR-time `flaky-gate` reads gist, intersects PR files with active flake entries (`last_seen >= today - 7d`), fails on overlap. Aggregator script: `.github/scripts/aggregate-flakes.py`.

### mutation-test.yml

Weekly. Three matrix entries (one per target file: `ssrf_guard.py`, `prompt_safety.py`, `leadhunter.py`) run `mutmut run` for up to 350 minutes each. Kill rate = `(killed + timeout) / (killed + timeout + survived + suspicious)`. Threshold: 80%. Issue label: `mutation-coverage`. Auto-closes when threshold restored.

### workflow-drift.yml

Daily. Two independent checks:
1. **Hash drift** — recompute sha256 of every workflow file, diff against `.github/workflow-hashes.json`. Snapshot regenerated via `make workflow-hashes` (operator action when intentionally editing workflows).
2. **Untracked commits** — `git log --since=25h -- .github/workflows/`, for each commit query `gh api repos/.../commits/<sha>/pulls` and flag commits with no merged PR. Catches admin-bypass direct pushes.

Issue label: `workflow-drift`. Slack ping if `SLACK_WEBHOOK_URL` set.

## Concurrency pattern

Every workflow uses:

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
```

The ternary collapses to `false` on push/schedule/dispatch (serialize-only) and `true` on PR events (cancel-stale-on-force-push). Exceptions: `post-deploy-smoke` (per-service rollback queue) and `synthetic-monitor` (gist PATCH race) keep their purpose-specific blocks.

## SHA-pinning convention

Every action ref under `.github/workflows/` is a 40-char commit SHA with a trailing `# vX.Y.Z` comment that Dependabot reads to bump both atomically:

```yaml
- uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5  # v4.3.1
```

Reasoning: Codecov 2021 incident — a mutable `@v1` tag was re-pointed to a malicious commit. The same risk applies to any unpinned action. The local `workflow-pin-guard` pre-commit hook + the `pre-commit` CI job both reject `uses: org/action@vN` patterns at commit time.

## Required status checks (branch protection)

Configure on `main` via Settings → Branches → Branch protection rules. Required checks:

```
pytest (cov >= 95%)
npm test
pre-commit (local-CI parity)
pip-audit --strict
npm audit (moderate+)
gitleaks (full git history)
requirements.txt ↔ requirements.in sync
License compliance (no copyleft)
Flaky-test gate (last 7 days)
semgrep (--config=auto)
ruff + mypy --strict
ESLint (no warnings)
Playwright E2E (Supabase ephemeral branch)
Schema drift + RLS posture (Supabase)
Referential integrity (CASCADE + FK enforcement)
Query plans (no Seq Scan on hot paths)
Lighthouse CI
Container scan (Trivy + Grype + SBOM)
Conventional Commits title
PR size gate
```

Also enable on the same rule:
- Require a pull request before merging
- Require approvals: 1
- Dismiss stale pull request approvals when new commits are pushed
- Require review from Code Owners
- Require conversation resolution before merging
- Require signed commits
- Require linear history
- Do not allow bypassing the above settings
- Allow force pushes: OFF
- Allow deletions: OFF

## Required secrets (GitHub Actions repo secrets)

See `docs/secret-inventory.md` for the full table with rotation cadence. Quick reference:

```
RENDER_API_KEY                — Render API; monthly rotation
RENDER_BACKEND_SERVICE_ID     — srv-<id> of backend service
SUPABASE_DATABASE_URL         — Postgres URL w/ password
SUPABASE_ACCESS_TOKEN         — Supabase Management API PAT
SUPABASE_PROJECT_REF          — project ref for management calls
SUPABASE_E2E_URL              — ephemeral Supabase branch URL
SUPABASE_E2E_ANON_KEY         — anon key for E2E branch
SUPABASE_E2E_SERVICE_KEY      — service-role key for E2E branch
E2E_USER_EMAIL                — test user on E2E branch
E2E_USER_PASSWORD             — test user password
E2E_API_SECRET_KEY            — X-API-Key for E2E backend
E2E_ADMIN_TOKEN               — X-Admin-Token for E2E backend
GEMINI_API_KEY                — Google AI Studio key (smoke probe)
SUPABASE_URL                  — prod Supabase URL (smoke probe)
SUPABASE_ANON_KEY             — prod anon key (smoke probe)
SUPABASE_SERVICE_ROLE_KEY     — prod service-role key (smoke probe)
GIST_TOKEN                    — gist-scoped GH PAT
MONITOR_GIST_ID               — synthetic-monitor history gist
FLAKY_TESTS_GIST_ID           — flakiness-detector tracker gist
SLACK_WEBHOOK_URL             — alerts channel
PROD_BACKEND_URL              — prod backend URL (synthetic)
PROD_FRONTEND_URL             — prod frontend URL (synthetic)
PROD_API_SECRET_KEY           — prod X-API-Key (synthetic)
LHCI_GITHUB_APP_TOKEN         — Lighthouse CI app token
```

`GITHUB_TOKEN` is auto-provisioned (~1h lifetime); no manual setup.

## Required labels

Create via Settings → Labels:

```
flaky                — flakiness-detector tracker
mutation-coverage    — mutation-test tracker
workflow-drift       — workflow-drift tracker
size/large           — pr-hygiene 400-800 line PR
size/xl              — pr-hygiene >800 line PR
security             — release-drafter category
feat / fix / perf / refactor / docs / test / chore
breaking             — major version trigger for release-drafter
```

## Operator one-time setup

1. **Repo settings**
   - Settings → General → "Allow auto-merge" ON
   - Settings → General → "Allow squash merging" ON, others OFF (linear history)
   - Settings → Code security → "Secret scanning" + "Push protection" ON
   - Settings → Code security → "Dependabot alerts" + "Security updates" ON
   - Settings → Actions → "Workflow permissions" → "Read repository contents and packages permissions"
   - Settings → Actions → "Approval for outside collaborators" → "Require approval for all outside collaborators"
2. **Tags**
   - Settings → Tags → "Protected tags" → pattern `v*`, restrict push to `@DanLika`
3. **Branch protection** — see "Required status checks" section above
4. **Render**
   - Backend service → switch to "Deploy from existing image" mode pointing at `ghcr.io/DanLika/lds-backend`
5. **Local clone**
   - `make install-hooks` once per clone
   - `make lock-python` to generate hashed `requirements.txt` (REQUIRED before next merge — `lockfile-sync` job + Docker `--require-hashes` will fail without it)
6. **Gists**
   - Create one gist with file `flaky-tests.json` containing `{}`; copy ID → `FLAKY_TESTS_GIST_ID`
7. **Render OIDC** (future hardening) — verify support at render.com/docs/access-control; if available, replace `RENDER_API_KEY` with OIDC trust binding

## Day-one red expected

Until prerequisites are backfilled, these checks fail:

- `pytest (cov >= 95%)` — coverage baseline TBD
- `ruff + mypy --strict` — codebase mostly untyped
- `Playwright E2E` — no Playwright tests committed yet (`e2e.yml` may have stubs)
- `Schema drift + RLS posture` — `src/scripts/schema_drift_check.py` may be incomplete
- `Lighthouse CI` — `.lighthouserc.json` missing
- `License compliance` — first run will reveal copyleft transients
- `Container scan` — Trivy may flag CVEs in Playwright base image
- `requirements.txt ↔ requirements.in sync` — until `make lock-python` runs
- `Container scan` Docker build — fails under `--require-hashes` until lockfile regenerated

Land branch protection AFTER these stabilize, not before, or every PR will be red.

## Future hardening

- **Render OIDC** — eliminate the highest-blast-radius long-lived bearer token
- **Vertex AI + Workload Identity Federation** — replace `GEMINI_API_KEY`
- **Per-service Render API keys** — reduce blast radius if Render exposes scoping
- **Consolidate `e2e.yml` ↔ `ci.yml::playwright-e2e`** — likely a duplicate from linter-add cycles
- **Pin third-party `install.sh` scripts** — grype/syft install scripts fetched from `main`; vendor them into the repo or use SHA-pinned actions
