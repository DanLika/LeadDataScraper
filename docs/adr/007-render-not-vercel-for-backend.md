# ADR-007: Render, not Vercel, for the backend

- **Status:** Accepted
- **Date:** 2026-05-22
- **Deciders:** Operator

## Context

The Next.js frontend is naturally Vercel-shaped. The FastAPI backend is not.

The backend has shapes Vercel's (and AWS Lambda's, and Cloud Functions')
serverless model does not absorb well:

- **Playwright** spawns a headless Chromium subprocess. Cold-starting a
  500 MB browser binary on every invocation is untenable. Serverless
  platforms don't ship a Chromium binary in their base runtime; you bring
  your own layer / extension, and the cold-start budget is exceeded.
- **Long-running orchestration jobs** — audit + enrich + hunt on a chunk of
  50 leads regularly runs 60–180 seconds per chunk; multi-chunk jobs run
  for minutes. Vercel's serverless function timeouts (10–60 s on
  Hobby/Pro tiers, higher only on Enterprise/Fluid) are below the
  chunk-processing budget. AWS Lambda caps at 15 min hard. Cloud
  Functions Gen 2 reaches 60 min, but still carries the Chromium-cold-start
  problem on every invocation.
- **Shared-browser pool** — one Chromium per `EnrichmentEngine` instance,
  per-lead `new_context()`, `aclose()` on job teardown. This pattern
  requires *persistent* process state across requests within a job. The
  serverless function model is request-scoped; there is no place for a
  pool to live.
- **`asyncio.create_task` background tasks** in the lifespan
  (`recover_interrupted_jobs()`, scheduled task scans) need a stable
  process. Serverless cold-start every N minutes resets the state.

A long-running container is the obvious fit. The candidates are Render,
Fly.io, Railway, AWS ECS/Fargate, GCP Cloud Run.

## Decision

**Backend on Render** as a Docker service (`env: docker` in `render.yaml`).
The `Dockerfile` uses `mcr.microsoft.com/playwright/python:v1.40.0-jammy`
as the base image — Playwright + Chromium pre-installed, eliminating the
browser-install pain entirely. The image is then hardened in the same
`RUN` layer:

- `build-essential` is installed AND purged in the same layer (gcc/make
  etc. don't ship to the runtime image — no post-RCE local-privesc
  toolkit).
- `pip install --require-hashes` against the hash-pinned
  `requirements.txt` — PyPI tampering between resolve and install fails
  the build.
- `HEALTHCHECK` polls `/` (the unauthenticated liveness probe) so `docker
  run` and local orchestrators can detect a wedged uvicorn worker.
- `uvicorn ... --no-server-header` so `Server: uvicorn` never leaves the
  box.

`render.yaml` declares the service in **"Deploy from existing image"**
mode (rather than building from the repo on every push). The deploy chain
is:

```
push main
  → deploy-backend.yml
    → docker build + push to GHCR
    → cosign-sign + SLSA3 provenance attestation
    → cosign verify-attestation
    → Render API rollout, pinned on the verified digest
```

This makes the GHCR digest, not the git SHA, the deploy unit. A forged
GHCR image (leaked PAT push) fails cosign verify and never reaches Render.

**Plan:** `starter` minimum. The Render free tier OOMs under Playwright;
this is a non-negotiable cost.

(The frontend service is also on Render today via the same `render.yaml`
for env-var parity — `ALLOWED_ORIGINS` + `ADMIN_TOKEN` must declare on the
frontend service or the production state-change endpoints fail-closed
403. Migrating the frontend to Vercel is mechanically possible and
intentionally out-of-scope for this ADR. The backend choice is the locked-in
one.)

## Consequences

**Positive:**
- **No per-request time cap.** Orchestrator jobs run as long as they need
  (the longest single chunk observed was ~3 minutes).
- **Playwright "just works."** The Microsoft base image gives us
  Chromium + dependencies + the right glibc version. Zero install pain.
- **Shared-browser pool fits naturally** — uvicorn process persists,
  `EnrichmentEngine` holds the Chromium handle for the job's lifetime.
- **Cosign-verified rollouts.** "Deploy from existing image" + the
  GHCR + cosign chain means the rollout gate is on the image digest, not
  the repo commit. A maintainer cannot bypass CI by pushing a manual
  image.
- **Docker build = reproducibility.** The same `Dockerfile` runs locally
  (`docker build -t lead-scraper-backend .`) and in Render.
- **Render env-var management** — `sync: false` declares the schema in
  `render.yaml`; actual values stay in the dashboard. Schema + value are
  decoupled.

**Negative / trade-offs:**
- **`starter` plan minimum** (Render free-tier auto-spin-down after 15 min
  idle is *not* what we want for a service that holds a Chromium pool;
  paying the starter tier is the way to keep the dyno hot). The free tier
  is also infeasible under Playwright load (OOM), so the cost is forced
  either way. The synthetic monitor (`synthetic-monitor.yml`, hourly)
  exists for cache-warmth on the in-process `_StatsCache` after long-idle
  periods, not for keeping the dyno itself alive.
- **Build time is slower than Vercel.** A typical Docker build is ~3–5
  minutes vs. Vercel's ~1 minute for the Next.js frontend.
- **Vendor lock-in to Render's deploy chain.** Migrating to Fly.io / GCP /
  Docker-on-EC2 means reproducing the SLSA3 + cosign verify + image-pinned
  rollout. The `Dockerfile` itself is portable; the deploy workflow
  (`deploy-backend.yml`) is the Render-shaped part.
- **No edge / CDN co-location.** The backend lives in one Render region;
  global users see one region's latency. Acceptable at the single-operator
  scale.

## Alternatives considered

- **Vercel Functions:** rejected for the time cap + Chromium cold-start
  story.
- **Fly.io:** strong alternative; deferred. Fly's Machines model fits the
  long-running-process shape and its anycast gives latency wins.
  Re-evaluate at the next major.
- **AWS ECS Fargate:** rejected for operational complexity (VPC,
  ALB, IAM) at the single-operator scale.
- **GCP Cloud Run:** the 60-min request cap helps; the Chromium cold-start
  still hurts. Reconsider if the chunk size shrinks below 60 min and the
  cold-start can be amortized.
- **Self-hosted on a VPS:** rejected for security operational load (TLS
  cert renewal, OS patching, log shipping). Render absorbs all of this.

## When to revisit

- If Render's pricing changes meaningfully (the starter plan is the price
  anchor).
- If the chunk-size budget shifts under 60 minutes per chunk (Cloud Run
  becomes viable).
- If a second region is needed (Fly.io / Cloud Run becomes more
  attractive).
- If the orchestrator moves to a job queue + worker model (the long-running
  process constraint dissolves; serverless re-enters consideration for the
  API layer only).

## References

- `Dockerfile`
- `render.yaml`
- `.github/workflows/deploy-backend.yml`
- `.github/workflows/release.yml`
- `.github/workflows/post-deploy-smoke.yml`
- `.github/workflows/synthetic-monitor.yml`
- CLAUDE.md → "Dockerfile hardening" + "CI/CD architecture"
- [ADR-004](004-playwright-for-discovery-aiohttp-for-audit.md) — the
  Playwright dependency that drives this choice
