"""SuppressionRepository — pure PostgREST I/O for the suppressions table.

Generic multi-channel suppression list (Phase 14.2 — renamed from
email_suppression). One row per (identifier_type, identifier_value,
channel) tuple. Email dispatcher consults this before every send via a
single batch SELECT (see ``InstantlyDispatcher._fetch_suppressed_emails``).
Webhook handlers (PR γ) insert here on bounce / unsubscribe events.

Reason taxonomy mirrors the DB CHECK constraint
``suppressions_reason_allowed``; expanding it requires a schema + drift
gate update in the same PR (see ``supabase_schema.sql`` Phase 14.2
section). The repo intentionally exposes the raw reason string instead
of a typed enum — adding a new reason to the DB and the repo signature
in lockstep is the desired friction.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Optional

logger = logging.getLogger(__name__)

# Mirrors suppressions_identifier_type_allowed CHECK in supabase_schema.sql.
IdentifierType = Literal["email", "domain", "linkedin_url", "phone"]
# Mirrors suppressions_channel_allowed CHECK.
Channel = Literal["email", "linkedin", "sms", "all"]
# Mirrors suppressions_reason_allowed CHECK. NEW values land here AND in
# the DB CHECK in the same PR.
Reason = Literal[
    "bounce",
    "bounce_hard",
    "bounce_soft_3x",
    "complaint",
    "manual",
    "unsubscribe",
    "gdpr_request",
    "spam_trap",
]
# Mirrors suppressions_provider_allowed CHECK.
SourceProvider = Literal["resend", "instantly", "smtp", "heyreach", "manual"]


@dataclass(frozen=True)
class SuppressionAdd:
    """Single-row insert payload for :meth:`SuppressionRepository.bulk_import`.

    All fields except ``identifier_type`` / ``identifier_value`` / ``reason``
    are optional — defaults match the DB column defaults.
    """

    identifier_type: IdentifierType
    identifier_value: str
    reason: Reason
    channel: Channel = "all"
    source_provider: Optional[SourceProvider] = None
    source_campaign_id: Optional[str] = None
    created_by: Optional[str] = None
    notes: Optional[str] = None


@dataclass(frozen=True)
class BulkImportResult:
    """Outcome of :meth:`SuppressionRepository.bulk_import`."""

    inserted: int
    skipped_duplicate: int
    failed: int


class SuppressionRepository:
    """PostgREST adapter for ``public.suppressions``.

    Construct with the same supabase-py client the rest of the stack uses
    (``SupabaseHelper().client``). The repo is stateless; the caller owns
    connection lifecycle.
    """

    TABLE_NAME = "suppressions"

    def __init__(self, db: Any) -> None:
        self._db = db

    async def is_suppressed(self, identifier: str, channel: Channel = "all") -> bool:
        """Return True if ``identifier`` is suppressed on ``channel`` (or 'all').

        Lookup honours the natural ``(identifier_value, channel)`` partial
        index. 'all' on the caller side widens to "match if suppressed on
        target channel OR globally"; the dispatcher always passes the
        concrete channel ('email' / 'linkedin'), not 'all'.
        """
        if not self._db or not identifier:
            return False
        wanted = self._channel_predicate(channel)
        rows = await asyncio.to_thread(
            lambda: (
                self._db.table(self.TABLE_NAME)
                .select("id", count=None)
                .eq("identifier_value", identifier)
                .in_("channel", wanted)
                .limit(1)
                .execute()
            )
        )
        return bool(getattr(rows, "data", None))

    async def filter_suppressed(
        self,
        identifiers: list[str],
        channel: Channel = "all",
    ) -> tuple[list[str], list[str]]:
        """Split ``identifiers`` into (allowed, suppressed) in ONE round trip.

        Single SELECT with ``.in_("identifier_value", identifiers)`` regardless
        of batch size. Order of ``allowed`` matches input order minus the
        suppressed entries; ``suppressed`` is the set intersection.
        """
        if not self._db or not identifiers:
            return list(identifiers), []
        wanted = self._channel_predicate(channel)
        # Deduplicate inputs for the IN clause; preserve original order in
        # the returned `allowed` list.
        unique_inputs = list(dict.fromkeys(identifiers))
        rows = await asyncio.to_thread(
            lambda: (
                self._db.table(self.TABLE_NAME)
                .select("identifier_value")
                .in_("channel", wanted)
                .in_("identifier_value", unique_inputs)
                .execute()
            )
        )
        suppressed_set = {
            (r.get("identifier_value") or "")
            for r in (getattr(rows, "data", None) or [])
        }
        allowed = [v for v in identifiers if v not in suppressed_set]
        suppressed = [v for v in identifiers if v in suppressed_set]
        return allowed, suppressed

    async def add(
        self,
        identifier_type: IdentifierType,
        identifier_value: str,
        reason: Reason,
        *,
        channel: Channel = "all",
        source_provider: Optional[SourceProvider] = None,
        source_campaign_id: Optional[str] = None,
        created_by: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Optional[int]:
        """Insert one suppression row; return the new row id or None on duplicate.

        Duplicate detection relies on the DB-level UNIQUE
        ``(identifier_type, identifier_value, channel)`` constraint
        (``suppressions_unique``). PostgREST surfaces a duplicate as
        ``code=23505``; we translate that into a ``None`` return rather
        than raising — webhook handlers replay the same event on retry
        and shouldn't see a 500 on the second hit.
        """
        if not self._db or not identifier_value:
            return None
        payload = {
            "identifier_type": identifier_type,
            "identifier_value": identifier_value,
            "reason": reason,
            "channel": channel,
            "source_provider": source_provider,
            "source_campaign_id": source_campaign_id,
            "created_by": created_by,
            "notes": notes,
        }
        # Strip None values so the DB defaults (created_at = now()) apply.
        payload = {k: v for k, v in payload.items() if v is not None}
        try:
            res = await asyncio.to_thread(
                lambda: self._db.table(self.TABLE_NAME).insert(payload).execute()
            )
        except Exception as exc:  # noqa: BLE001 — narrow check below
            if _is_unique_violation(exc):
                return None
            logger.exception(
                "SuppressionRepository.add failed for %s/%s",
                identifier_type,
                _scrub_for_log(identifier_value),
            )
            raise
        data = getattr(res, "data", None) or []
        return int(data[0]["id"]) if data and "id" in data[0] else None

    async def bulk_import(self, items: Iterable[SuppressionAdd]) -> BulkImportResult:
        """Operator-facing path for "paste 500 emails into a textarea".

        Inserts the full batch in ONE PostgREST call and counts duplicates
        via the unique-violation reply. Failures other than duplicates
        (e.g. CHECK constraint rejection on an invalid reason) abort the
        whole batch and re-raise — bulk imports should be atomic.
        """
        rows = [
            {k: v for k, v in {
                "identifier_type": it.identifier_type,
                "identifier_value": it.identifier_value,
                "reason": it.reason,
                "channel": it.channel,
                "source_provider": it.source_provider,
                "source_campaign_id": it.source_campaign_id,
                "created_by": it.created_by,
                "notes": it.notes,
            }.items() if v is not None}
            for it in items
        ]
        if not rows:
            return BulkImportResult(inserted=0, skipped_duplicate=0, failed=0)
        # PostgREST honours ?on_conflict=...&prefer=resolution=ignore-duplicates
        # via supabase-py's .upsert(..., ignore_duplicates=True). One round
        # trip; rows that collide on suppressions_unique are silently skipped.
        try:
            res = await asyncio.to_thread(
                lambda: (
                    self._db.table(self.TABLE_NAME)
                    .upsert(
                        rows,
                        on_conflict="identifier_type,identifier_value,channel",
                        ignore_duplicates=True,
                    )
                    .execute()
                )
            )
        except Exception:
            logger.exception("SuppressionRepository.bulk_import failed")
            raise
        inserted_data = getattr(res, "data", None) or []
        inserted = len(inserted_data)
        skipped = len(rows) - inserted
        return BulkImportResult(
            inserted=inserted, skipped_duplicate=max(skipped, 0), failed=0
        )

    @staticmethod
    def _channel_predicate(channel: Channel) -> list[str]:
        """A caller asking about ``email`` should also match global ('all') rows.

        Inverse is not symmetric: a caller asking about 'all' wants only
        globally-applicable rows, not channel-specific ones (otherwise a
        webhook checking "is this address blocked anywhere" would over-match).
        """
        if channel == "all":
            return ["all"]
        return [channel, "all"]


def _is_unique_violation(exc: Exception) -> bool:
    """PostgREST surfaces unique violations as ``APIError(code='23505')``.

    Supabase-py wraps PostgrestAPIError around the response; the underlying
    code is reachable via ``exc.code`` OR by checking the message body.
    Both surfaces are checked so test mocks can fake either shape.
    """
    code = getattr(exc, "code", None)
    if code == "23505":
        return True
    message = str(exc).lower()
    return "23505" in message or "duplicate key" in message


def _scrub_for_log(value: str) -> str:
    """Hash long identifiers for log output to avoid full PII in stdout."""
    if not value or len(value) <= 24:
        return value
    return value[:6] + "…" + value[-3:]
