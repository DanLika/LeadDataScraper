"""Build the dispatcher payload + thread-continuation handling.

Once a tick has claimed a message, batch-fetched the lead/step/variant
joins, and selected a variant, this module assembles the final payload
the dispatcher receives. Two concerns intertwine:

1. **Rendering** — :mod:`template_renderer` turns the variant templates
   into concrete subject + body strings using lead-derived + operator
   + system context.
2. **Thread continuation** — Steps with ``thread_with_prior=True`` need
   the prior step's ``provider_message_id`` to set the SMTP
   In-Reply-To header. Instantly + Resend interpret an empty subject
   on a threaded send as ``Re: <prior_subject>``. Race condition:
   step N+1 may be scheduled before step N's ``email_sent`` webhook
   arrives, so the prior row's ``provider_message_id`` is still NULL.
   :class:`PriorMessageNotReadyError` signals this; the worker
   catches + reschedules.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from src.services.template_renderer import (
    TemplateError,
    render,
)

logger = logging.getLogger(__name__)


# ----- Errors ---------------------------------------------------------------


class ThreadBuildError(Exception):
    """Base — any failure assembling a send payload."""


class PriorMessageNotReadyError(ThreadBuildError):
    """``thread_with_prior=True`` step needs prior's
    ``provider_message_id`` but it hasn't been stamped yet (webhook
    delay race).

    Worker semantics: catch + bump ``scheduled_at`` by 1 hour, leave
    status='pending'. Don't release as 'failed' — the prior send is
    legitimately in flight; we just need to wait.
    """

    def __init__(self, lds_message_id: str, prior_message_id: Optional[str] = None) -> None:
        super().__init__(
            f"prior message {prior_message_id or '<unknown>'} not ready "
            f"for thread continuation on {lds_message_id}"
        )
        self.lds_message_id = lds_message_id
        self.prior_message_id = prior_message_id


# ----- Payload shape --------------------------------------------------------


@dataclass(frozen=True)
class DispatchPayload:
    """Wire-format-agnostic payload the dispatcher consumes.

    Concrete dispatchers (Instantly via push_leads, future Resend
    single-shot, etc.) translate this into their own per-provider
    request shape; the build_send_payload layer is dispatcher-neutral.
    """

    lds_message_id: str
    lead_unique_key: str
    email: str
    subject: str
    body: str
    in_reply_to_message_id: Optional[str]
    list_unsubscribe_url: str

    def as_lead_dict(self) -> dict[str, Any]:
        """Project to the dict shape ``InstantlyDispatcher.push_leads``
        consumes (matches ``LdsLeadRow`` Phase 14.1)."""
        return {
            "unique_key": self.lead_unique_key,
            "email": self.email,
            "subject": self.subject,
            "body": self.body,
            "in_reply_to_message_id": self.in_reply_to_message_id,
            "list_unsubscribe_url": self.list_unsubscribe_url,
        }


# ----- Public API -----------------------------------------------------------


def build_send_payload(
    *,
    lds_message_id: str,
    lead: dict[str, Any],
    step: Any,  # SequenceStep (15.1 dataclass) — typed Any to keep
                # this module decoupled from the repo layer.
    variant: Any,  # SequenceVariant (15.1 dataclass)
    prior_message: Optional[dict[str, Any]] = None,
    operator_name: str = "",
    operator_signature: str = "",
    unsubscribe_url: str = "",
) -> DispatchPayload:
    """Render the variant + assemble the send payload.

    ``lead`` is a flat dict with at least ``unique_key``, ``email``.
    Template rendering picks fields from :data:`ALLOWED_VARS`; missing
    fields render as empty strings (Jinja2 StrictUndefined on
    ``{{ undefined_var }}`` raises MissingVariableError → caller
    decides whether to skip the message OR fail the variant).

    Raises:
        :class:`PriorMessageNotReadyError` when ``step.thread_with_prior``
        is True but ``prior_message`` is None / has no
        ``provider_message_id``.
        :class:`TemplateError` (or subclass) on render failure.
    """
    if not lds_message_id or not lead:
        raise ThreadBuildError("missing identifiers")

    # Thread continuation gate — short-circuit before doing render work.
    threading = bool(getattr(step, "thread_with_prior", False))
    in_reply_to: Optional[str] = None
    if threading:
        prior_id = (prior_message or {}).get("provider_message_id")
        if not prior_id:
            raise PriorMessageNotReadyError(
                lds_message_id=lds_message_id,
                prior_message_id=(prior_message or {}).get("id"),
            )
        in_reply_to = prior_id

    # Build render context — pulls + normalizes lead fields into the
    # ALLOWED_VARS shape. Use .get with defaults so a sparse lead row
    # doesn't raise StrictUndefined for fields the template doesn't
    # actually reference.
    context = {
        "first_name": (lead.get("first_name") or "").strip(),
        "last_name": (lead.get("last_name") or "").strip(),
        "company": (lead.get("company_name") or lead.get("company") or "").strip(),
        "website": (lead.get("website") or "").strip(),
        "city": (lead.get("city") or "").strip(),
        "industry": (lead.get("industry") or lead.get("segment") or "").strip(),
        "audit_score": str(lead.get("outreach_score") or lead.get("audit_score") or ""),
        "pain_point": (lead.get("pain_points") or lead.get("pain_point") or "").strip(),
        "operator_name": operator_name,
        "operator_signature": operator_signature,
        "unsubscribe_url": unsubscribe_url,
    }

    # Subject template is optional (e.g. thread-continuation steps
    # often blank it so the mail client renders "Re: <prior>"). When
    # threading is on AND subject template is empty, that's the
    # intentional pattern.
    subject_template = getattr(variant, "subject_template", None) or ""
    body_template = getattr(variant, "body_template", "") or ""
    # Variant-driven render mode. 'html' enables Jinja2 autoescape so
    # attacker-controlled lead fields (pain_point, first_name, company,
    # industry, city — sourced from CSV ingest + Gemini enrichment of
    # scraped sites) can't break out of HTML context in the recipient
    # mail client. Subject stays text-mode (RFC 5322 line, no HTML).
    content_type = getattr(variant, "content_type", "text")
    if content_type not in ("text", "html"):
        content_type = "text"

    try:
        if threading and not subject_template:
            # Blank subject — Instantly + Resend honor this as "Re: prior".
            subject = ""
        else:
            subject = render(subject_template, context)
        body = render(body_template, context, content_type=content_type)
    except TemplateError:
        # Surface as-is — caller (worker) decides whether to release
        # the claim as 'failed' or retry.
        raise

    return DispatchPayload(
        lds_message_id=lds_message_id,
        lead_unique_key=str(lead.get("unique_key") or ""),
        email=str(lead.get("email") or "").strip().lower(),
        subject=subject,
        body=body,
        in_reply_to_message_id=in_reply_to,
        list_unsubscribe_url=unsubscribe_url,
    )


__all__ = [
    "DispatchPayload",
    "ThreadBuildError",
    "PriorMessageNotReadyError",
    "build_send_payload",
]
