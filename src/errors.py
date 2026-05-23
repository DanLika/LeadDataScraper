"""Canonical domain error hierarchy.

One place to define every typed exception the application raises. Every
boundary handler (FastAPI routes, CLI scripts, background tasks) maps a
domain error to a context-appropriate response — HTTP status code, exit
code, retry-or-give-up decision.

## Hierarchy

    DomainError                       (base — caught by boundary catch-alls)
    └── AuditError                    → 500; SEO audit failures
        └── AuditFetchError

## Rules for callers

- Raise the most specific class that fits. Producers catch the per-domain
  parent (`AuditError`, etc.); boundary handlers catch `DomainError` as
  the last clause before a true catch-all `except Exception`.
- NEVER `raise Exception(...)` — pick a class.
- Messages on these exceptions are written for human handler authors,
  NOT end users. Handlers choose the user-facing string when mapping to
  an HTTP response; do not echo `str(exc)` directly to clients (would
  leak internal context).
- Catch `except Exception` ONLY at the outermost boundary; everywhere
  else, catch the specific domain type so a real bug in `X` doesn't
  silently look like a domain-level failure in `Y`.

## Forward compatibility

PR #195 (open at time of writing) introduces the full hierarchy
documented in CLAUDE.md (NotFoundError / ValidationError /
ConfigurationError / LeadError / EnrichmentError + per-domain children).
This file ships only the slice needed by the seo_audit body-cap fix —
the parent classes match #195's shape exactly so the two changes merge
cleanly regardless of order. Once #195 lands, the additional siblings
land alongside without renaming or re-parenting anything here.
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


# ---- SEO audit domain --------------------------------------------

class AuditError(DomainError):
    """Per-domain catch-all for SEO audit failures."""


class AuditFetchError(AuditError):
    """aiohttp fetch returned a 4xx/5xx, the response wasn't parseable
    HTML, the response body exceeded the configured size cap, or a
    redirect chain blew past the configured limit. The lead row's
    `last_error` carries the upstream status / reason."""
