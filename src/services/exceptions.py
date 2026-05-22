"""Domain exceptions for service-layer error translation.

Each layer translates errors upward to its own vocabulary:

  Repository  → catches `postgrest.APIError`, translates known cases
                (e.g. PGRST205 → CampaignTableMissingError) and lets the
                rest bubble.
  Service     → catches repo-raised domain exceptions and may translate
                further, but typically just lets the handler decide the
                HTTP mapping.
  Handler     → maps domain exceptions to HTTP responses + status codes.

Operator messages on these exceptions are written for human handler
authors, NOT end users — handlers should choose the user-facing string
when mapping to an HTTP response, never echo `str(exc)` directly.
"""
from __future__ import annotations


class DomainError(Exception):
    """Base for all service-layer domain errors. Caught at the handler."""


class NotFoundError(DomainError):
    """Resource not found. Maps to HTTP 404 at the handler boundary."""


class CampaignNotFoundError(NotFoundError):
    """A campaign id does not exist."""


class CampaignTableMissingError(DomainError):
    """The `campaigns` (or `campaign_messages`) table is not provisioned.

    Translated from PostgREST `PGRST205 schema cache` errors. Maps to
    HTTP 503 at the handler with an operator-friendly hint to run the
    Supabase migration SQL.
    """


class NoMatchingLeadsError(NotFoundError):
    """No leads matched the campaign's channel / segment filter.

    Distinct from CampaignNotFoundError because the campaign DOES exist —
    it just selects an empty audience. Maps to HTTP 404 with the
    "No matching leads found" message so the operator can adjust filters.
    """


class NoCampaignMessagesError(NotFoundError):
    """Export was requested for a campaign with no generated messages."""
