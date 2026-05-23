# ADR-004: Playwright for Discovery, aiohttp for Audit

- **Status:** Accepted
- **Date:** 2026-05-22
- **Deciders:** Operator

## Context

The pipeline scrapes the open web in two distinct shapes:

1. **Discovery** (Google Maps lead-finding). Google Maps is a JavaScript
   single-page app with infinite-scroll side panel, lazy DOM mutations on
   result-card open, and anti-bot fingerprinting. Plain HTTP requests
   return the loading shell, not lead data.
2. **SEO audit** (every customer website, often thousands of distinct
   domains). The job is to fetch HTML, extract tech-stack markers
   (WordPress, Shopify, React, jQuery version, GA / GTM, Cloudflare, etc.)
   via regex, and compute a heuristic `seo_score`. Most sites are
   static-enough that the HTML response body contains all signals.

Using Playwright everywhere would burn 10× more CPU, 100× more memory, and
make a 1000-lead audit a multi-hour ordeal. Using aiohttp everywhere fails
on Google Maps. The shapes are different enough to deserve different
tools.

## Decision

- **`src/scrapers/discovery_engine.py`** uses Playwright (headless
  Chromium) **for the Discovery path only**. Single Chromium process per
  `EnrichmentEngine` instance; per-lead `new_context()`. `aclose()`
  unconditionally invoked in `_process_in_chunks` `finally` to prevent
  Chromium leakage per job.
- **`src/scrapers/seo_audit.py`** uses `aiohttp` + regex for the SEO audit
  path. No browser. Async-native, fast, low memory.
- **`src/scrapers/enrichment_engine.py`** (used by Deep Hunt) uses
  Playwright for the contact-extraction phase — modal/JS-driven contact
  forms are common on small-business sites. Same shared-browser-pool
  pattern as discovery.

SSRF protection is wired at **both** layers so the choice of transport
doesn't bypass it:

- `aiohttp` calls in `seo_audit.py` route through `assert_safe_url()`
  pre-flight (DNS resolve → block private / loopback / link-local /
  reserved / multicast IPs and known cloud + Kubernetes metadata
  hostnames).
- Playwright contexts install `_install_ssrf_route_guard(context)`, a
  `context.route("**/*", ...)` handler that re-runs `assert_safe_url` on
  every initial navigation, every 30x redirect, every subresource —
  closing the TOCTOU window between pre-flight DNS check and `page.goto()`
  and blocking redirect chains that hop to an internal host.

## Consequences

**Positive:**
- Audit is fast and cheap: aiohttp does 50 concurrent fetches per chunk,
  zero browser overhead. A 200-lead audit finishes in ~3 minutes; the same
  via Playwright would be 30+ minutes and risk OOM.
- Discovery and Hunt get full JS execution where it's actually required.
- Shared-browser pool means we pay the Chromium boot cost **once per
  EnrichmentEngine instance**, not per lead.
- SSRF defense is uniform — no "this transport is safe, that one isn't"
  edge case.
- Cost: zero Gemini calls in either Discovery or Audit (see ADR-006). Both
  are pure scrape + regex / DOM parsing.

**Negative / trade-offs:**
- **Two scraping codepaths** to maintain (`discovery_engine.py` plus
  `enrichment_engine.py` are Playwright; `seo_audit.py` is aiohttp). New
  scraping features need to decide which side they belong on.
- Sites that gate behind JS will fail SEO audit with `Failed` /
  `403 Forbidden` / `Timeout`. The operator handles this via the failure-
  recovery flow (operator-guide §7c) — manually re-enriching via Deep Hunt
  is the escape valve.
- Playwright contexts must be torn down explicitly. `aclose()` must be
  called in every `finally`, every direct caller, every test. Forgetting
  this leaks a Chromium subprocess per job (was a real bug — locked in by
  the explicit `finally` blocks in `task_orchestrator._process_in_chunks`
  and `_execute_deep_enrichment`).
- Render `starter` plan minimum for browser stability; smaller plans OOM
  under Playwright load.
- Playwright base image (`mcr.microsoft.com/playwright/python:v1.40.0-jammy`)
  pins the browser version. Updating Playwright is a deploy event, not a
  pip-install — the version is encoded in the Dockerfile.

## Alternatives considered

- **Pure Playwright** everywhere: rejected on cost (CPU + memory +
  wallclock).
- **Pure aiohttp** everywhere: rejected on Discovery — Google Maps
  fundamentally requires JS execution.
- **Headless browsers via Splash / Selenium-Grid / Browserless**: rejected
  as additional infra. Playwright in-process is simpler.
- **Crawlbase** (the `CRAWLBASE_NORMAL_TOKEN` / `CRAWLBASE_JS_TOKEN` env
  vars remain from a legacy attempt): rejected on cost and on giving a
  third party the scraping payload. Vars are unused; kept in
  `.env.example` to avoid breaking older `.env` files but slated for
  removal next major.

## References

- `src/scrapers/discovery_engine.py`
- `src/scrapers/seo_audit.py`
- `src/scrapers/enrichment_engine.py`
- `src/utils/ssrf_guard.py`
- CLAUDE.md → "Discovery engine invariants"
- CLAUDE.md → "API Security" → "Playwright browser contexts in
  enrichment_engine.py additionally install `_install_ssrf_route_guard`"
