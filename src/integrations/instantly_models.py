"""Pydantic models for the Instantly v2 API integration.

Mirrors the request/response surface of ``POST /api/v2/leads/add`` and
the per-lead error rows it returns. Bounded everywhere (``max_length``,
``extra='forbid'``) so attacker-controlled lead fields can't smuggle
unbounded payloads to the upstream API.

See ``src/integrations/instantly_sender.py`` for the dispatcher that
consumes these.
"""
from __future__ import annotations

import re
from typing import Any, ClassVar, Optional

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


# Same `\Z`-anchored shape as ResendEmailSender — `\s` excludes
# CR/LF/VT/FF so attacker-controlled lead emails can't smuggle
# CRLF into downstream MIME / log lines. Matches LDS's pinned regex
# (tests/test_crlf_injection.py).
_EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+\Z")


# Mirrors the columns the dispatcher reads off a Supabase `leads` row.
# Kept as a TypedDict-shaped dict so we don't introduce a hard runtime
# dep on a Lead ORM (LDS reads via supabase-py PostgREST, untyped dicts).
LdsLeadRow = dict[str, Any]


class InstantlyLeadPayload(BaseModel):
    """One lead in the Instantly ``/leads/add`` request body.

    Field names match the v2 API. ``custom_variables`` is a free-form
    dict (str/int/float/bool/None) that Instantly merges into its
    template-variable resolver — LDS uses it to thread the source
    `unique_key` through to the suppression webhook.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    email: str = Field(max_length=320)
    first_name: Optional[str] = Field(default=None, max_length=128)

    @field_validator("email")
    @classmethod
    def _validate_email_shape(cls, v: str) -> str:
        if not _EMAIL_REGEX.match(v):
            raise ValueError("Invalid email format")
        return v
    last_name: Optional[str] = Field(default=None, max_length=128)
    company_name: Optional[str] = Field(default=None, max_length=256)
    website: Optional[HttpUrl] = None
    personalization: Optional[str] = Field(default=None, max_length=4000)
    custom_variables: dict[str, Optional[str | int | float | bool]] = Field(
        default_factory=dict
    )

    # LDS custom-variable keys — pinned here so the dispatcher + webhook
    # handler agree on names. Adding a new key requires updating both
    # sides AND the Phase 14.1 doc (`docs/integrations/instantly.md`).
    # ``list_unsubscribe`` (Phase 14.2 PR β) carries the angle-bracketed
    # ``<https://...>, <mailto:...>`` value Instantly sets as the
    # List-Unsubscribe header. Setting the header server-side is
    # Instantly's job; we ship the value via this var per their
    # custom-vars-to-header bridge convention.
    LDS_KEYS: ClassVar[frozenset[str]] = frozenset(
        {
            "lds_lead_id",
            "lds_audit_score",
            "lds_discovery_source",
            "lds_dispatched_at",
            "list_unsubscribe",
            "list_unsubscribe_post",
        }
    )

    @classmethod
    def from_lds_lead(
        cls,
        lead: LdsLeadRow,
        personalization: Optional[str] = None,
        dispatched_at: Optional[str] = None,
        list_unsubscribe: Optional[str] = None,
    ) -> "InstantlyLeadPayload":
        """Map a Supabase `leads` row to an Instantly request payload.

        Required source columns: ``email`` (string). All others are
        best-effort — Instantly accepts NULL for every non-email field.
        Custom variables thread ``lds_lead_id`` / ``lds_audit_score`` /
        ``lds_discovery_source`` / ``lds_dispatched_at`` so the webhook
        handler can reconcile bounces back to the originating row.

        ``list_unsubscribe`` (Phase 14.2 PR β) is the angle-bracketed
        ``<https://...>, <mailto:...>`` header value Instantly maps to
        the SMTP List-Unsubscribe header. Pair with
        ``list_unsubscribe_post = "List-Unsubscribe=One-Click"`` to
        satisfy Gmail/Yahoo/Microsoft 2024+ enforcement. Caller passes
        ``None`` to omit (e.g. for a non-bulk send).
        """
        custom_vars: dict[str, Optional[str | int | float | bool]] = {
            "lds_lead_id": lead.get("unique_key"),
            "lds_audit_score": lead.get("outreach_score"),
            "lds_discovery_source": lead.get("lead_source"),
            "lds_dispatched_at": dispatched_at,
        }
        if list_unsubscribe:
            custom_vars["list_unsubscribe"] = list_unsubscribe
            custom_vars["list_unsubscribe_post"] = "List-Unsubscribe=One-Click"
        return cls(
            email=lead["email"],
            first_name=lead.get("first_name") or None,
            last_name=lead.get("last_name") or None,
            company_name=lead.get("company_name") or None,
            website=lead.get("website") or None,
            personalization=personalization,
            custom_variables=custom_vars,
        )


class InstantlyError(BaseModel):
    """One per-lead error row from the Instantly bulk-add response."""

    model_config = ConfigDict(extra="ignore")

    email: str = Field(max_length=320)
    error_code: str = Field(max_length=64)
    error_message: str = Field(max_length=512)


class InstantlyPushResult(BaseModel):
    """Per-batch summary returned by ``InstantlyDispatcher.push_leads``."""

    model_config = ConfigDict(extra="forbid")

    success_count: int = Field(ge=0)
    skipped_suppressed: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    errors: list[InstantlyError] = Field(default_factory=list)
    # `raw_response` is the API body Instantly returned; capped via
    # max_length on the wrapper class — the dispatcher trims long bodies
    # before construction. Type stays `dict` to leave room for the
    # v2-versus-future-v3 envelope shape.
    raw_response: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False

    @property
    def total_attempted(self) -> int:
        return self.success_count + self.skipped_suppressed + self.failed_count
