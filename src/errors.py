"""Canonical domain error hierarchy.

One place to define every typed exception the application raises. Every
boundary handler (FastAPI routes, CLI scripts, background tasks) maps a
domain error to a context-appropriate response — HTTP status code, exit
code, retry-or-give-up decision.

## Hierarchy

    DomainError                       (base — caught by boundary catch-alls)
    ├── NotFoundError                 → 404 at HTTP boundary
    │   ├── CampaignNotFoundError
    │   ├── NoMatchingLeadsError
    │   ├── NoCampaignMessagesError
    │   └── LeadNotFoundError
    ├── ValidationError               → 400/422 at HTTP boundary
    ├── ConfigurationError            → 503 (operator action required)
    │   └── CampaignTableMissingError
    ├── LeadError                     → 500; per-domain catch-all for leads
    │   └── LeadProcessingError
    ├── EnrichmentError               → 500; enrichment pipeline failures
    │   ├── EnrichmentTimeoutError
    │   └── EnrichmentExtractionError
    └── AuditError                    → 500; SEO audit failures
        ├── AuditTimeoutError
        └── AuditFetchError

## Rules for callers

- Raise the most specific class that fits. Producers catch
  `LeadError`/`EnrichmentError`/`AuditError` per domain; handlers catch
  `DomainError` as the last catch.
- NEVER `raise Exception(...)` — pick a class.
- Messages on these exceptions are written for human handler authors,
  NOT end users. Handlers choose the user-facing string when mapping to
  an HTTP response; do not echo `str(exc)` directly to clients (would
  leak internal context).
- Catch `except Exception` ONLY at the outermost boundary; everywhere
  else, catch the specific domain type so a real bug in `X` doesn't
  silently look like a domain-level failure in `Y`.

## Migration

This module supersedes `src/services/exceptions.py`. The previous
campaign-only exceptions are re-defined here under the canonical
hierarchy; `src/services/exceptions.py` is now a thin shim that
re-exports for backward compat.
"""
from __future__ import annotations


# ---- Base hierarchy ----------------------------------------------

class DomainError(Exception):
    """Base for every typed exception this application raises.

    Boundary handlers catch this as the last `except` clause before
    a true catch-all `except Exception`. Anything inheriting from
    `Exception` directly (rather than `DomainError`) signals a real
    bug or an unhandled third-party failure — those should surface
    as a 500 with `logger.exception(...)`.
    """


class NotFoundError(DomainError):
    """Resource not found. Maps to HTTP 404 at the handler boundary."""


class ValidationError(DomainError):
    """Input failed business validation (above and beyond Pydantic's
    type checks — e.g. "channel must match campaign's existing channel").
    Maps to HTTP 400 / 422."""


class ConfigurationError(DomainError):
    """The deployment is misconfigured — required env var unset,
    required table missing, required external service unreachable
    at boot. Maps to HTTP 503 (transient from the operator's POV;
    fix-and-retry, not retry-as-is)."""


# ---- Campaign domain (previously src/services/exceptions.py) -----

class CampaignNotFoundError(NotFoundError):
    """A campaign id does not exist."""


class CampaignTableMissingError(ConfigurationError):
    """The `campaigns` (or `campaign_messages`) table is not provisioned.

    Translated from PostgREST `PGRST205 schema cache` errors by
    `CampaignRepository._translate_table_missing`. Handler maps to
    HTTP 503 with an operator-friendly hint to run the migration SQL.
    """


class NoMatchingLeadsError(NotFoundError):
    """No leads matched the campaign's channel / segment filter.

    Distinct from `CampaignNotFoundError` because the campaign DOES exist
    — it just selects an empty audience. Maps to HTTP 404 with the
    "No matching leads found" message so the operator can adjust filters.
    """


class NoCampaignMessagesError(NotFoundError):
    """Export was requested for a campaign with no generated messages."""


# ---- Lead domain -------------------------------------------------

class LeadError(DomainError):
    """Per-domain catch-all for lead operations. Producers (the lead
    processor / segmenter / scorer) raise this when a step fails for
    domain reasons (vs. an environment failure)."""


class LeadNotFoundError(NotFoundError, LeadError):
    """A lead `unique_key` does not exist."""


class LeadProcessingError(LeadError):
    """A lead-level processing step failed (e.g. enrichment + audit +
    scoring pipeline raised mid-way). The lead's `audit_status` should
    be left in a recoverable state (`Failed` or `Pending` retry) by
    the catch site."""


# ---- Enrichment domain -------------------------------------------

class EnrichmentError(DomainError):
    """Per-domain catch-all for the enrichment pipeline (Playwright
    browser pool + Gemini extraction). Caught at the
    `EnrichmentEngine.enrich_lead` boundary; per-lead failures should
    NOT take down the whole batch."""


class EnrichmentTimeoutError(EnrichmentError):
    """Playwright page load or `asyncio.wait_for` outer timeout fired.
    Distinct from `EnrichmentExtractionError` because retry policy
    differs (a timeout often resolves on retry; an extraction failure
    usually doesn't)."""


class EnrichmentExtractionError(EnrichmentError):
    """Gemini call succeeded but the extracted payload was empty,
    malformed, or below quality threshold."""


# ---- SEO audit domain --------------------------------------------

class AuditError(DomainError):
    """Per-domain catch-all for SEO audit failures."""


class AuditTimeoutError(AuditError):
    """aiohttp HEAD/GET timed out before the page returned. Recorded
    as `last_error='Timeout'` on the lead row by
    `ParallelAuditor.audit_single_lead`."""


class AuditFetchError(AuditError):
    """aiohttp fetch returned a 4xx/5xx, the response wasn't parseable
    HTML, or a redirect chain blew past the configured limit. The lead
    row's `last_error` carries the upstream status / reason."""
