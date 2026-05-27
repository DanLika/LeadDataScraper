"""WebhookEventRepository — PostgREST I/O for webhook_events.

webhook_events stores raw idempotent provider event payloads. The
``(provider, event_id)`` UNIQUE constraint gives replay-safe idempotency:
Instantly retries any non-2xx and occasionally retries 2xx, so duplicate
INSERTs are normal traffic. This repo translates the resulting 23505 to
a structured :class:`InsertResult` so the handler can distinguish
"accepted, duplicate" (200 OK) from a real DB error (500).

PostgREST does not support raw SQL — see CLAUDE.md "Connection pool /
pooler-URL contract". All writes flow through the chain API, which
keeps the call site type-checked at PR time (``WebhookProvider`` is
mirrored to the DB CHECK constraint).

Producer call site: ``backend/main.py::receive_instantly_webhook``.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from src.types.providers import WebhookProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InsertResult:
    """Outcome of one :meth:`WebhookEventRepository.insert_event` call.

    ``inserted`` is True iff a fresh row landed. ``duplicate`` is True
    iff the INSERT raced an earlier identical event (same
    ``(provider, event_id)``) — Instantly replay. Both fields are
    set on the same call so a handler can branch with a single read.
    """

    inserted: bool
    duplicate: bool


class WebhookEventRepository:
    """Thin idempotent INSERT wrapper around ``webhook_events``."""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def insert_event(
        self,
        *,
        provider: WebhookProvider,
        event_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> InsertResult:
        """Idempotent INSERT into ``webhook_events``.

        Returns:
            :class:`InsertResult` with ``inserted=True`` on a fresh
            row, or ``inserted=False`` + ``duplicate=True`` on a
            replay (PostgREST 23505 from the
            ``(provider, event_id)`` UNIQUE constraint).

        Raises:
            Any non-23505 PostgREST error propagates unchanged.
        """
        row = {
            "provider": provider,
            "event_id": event_id,
            "event_type": event_type,
            "payload": payload,
        }
        try:
            await asyncio.to_thread(
                lambda: self._db.table("webhook_events").insert(row).execute()
            )
            return InsertResult(inserted=True, duplicate=False)
        except Exception as exc:  # noqa: BLE001 — narrow inline
            if _is_unique_violation(exc):
                return InsertResult(inserted=False, duplicate=True)
            raise

    async def count_soft_bounces_for_recipient(
        self,
        recipient_email: str,
        *,
        window_days: int = 30,
        provider: WebhookProvider = "instantly",
    ) -> int:
        """Count ``email_bounced`` events with ``bounce_type='soft'`` for
        ``recipient_email`` in the last ``window_days``.

        Powers the PR #359 soft-bounce strike counter consumed by
        ``src.integrations.instantly_webhook_handler.decide_bounce_action``.
        Match is ILIKE on ``payload->>bounce_type`` so case variants
        ('Soft', 'SOFT') all count, and exact-eq on
        ``payload->>recipient_email`` (Instantly preserves casing per
        message; matching by exact string is consistent within a
        single recipient's event stream).

        Raises on DB errors so the caller can choose a fail-safe (the
        handler falls back to ``suppress_hard`` on count failure rather
        than silently shrinking the bounce strike count toward zero,
        which would never escalate to permanent suppression).
        """
        if not self._db or not recipient_email:
            return 0
        cutoff_iso = (
            datetime.now(timezone.utc) - timedelta(days=window_days)
        ).isoformat()
        res = await asyncio.to_thread(
            lambda: (
                self._db.table("webhook_events")
                .select("id", count="exact")
                .eq("provider", provider)
                .eq("event_type", "email_bounced")
                .ilike("payload->>bounce_type", "soft")
                .eq("payload->>recipient_email", recipient_email)
                .gte("received_at", cutoff_iso)
                .limit(1)
                .execute()
            )
        )
        return int(getattr(res, "count", 0) or 0)


def _is_unique_violation(exc: Exception) -> bool:
    """PostgREST surfaces 23505 either via the ``code`` attribute on
    ``APIError`` or as a substring of the response body. Both surfaces
    checked so test mocks can fake either. Mirrors
    ``src.repositories.suppression_repo._is_unique_violation`` and
    the legacy ``backend.main._looks_like_unique_violation`` (which
    this repo replaced at the webhook_events call site — function
    removed in the same PR).
    """
    code = getattr(exc, "code", None)
    if code == "23505":
        return True
    text = str(exc)
    return "23505" in text or "duplicate key" in text


__all__ = ["WebhookEventRepository", "InsertResult"]
