"""SequenceRepository — PostgREST I/O for ``public.sequences``.

Phase 15.1 — top-level container for multi-step outreach. A sequence
holds an ordered list of ``sequence_steps`` and lives under a single
``campaigns.id``. The dispatcher (Phase 15.2) only picks up steps from
sequences whose ``status='active'``.

State machine::

    draft  ──(operator activates)──► active
       │                                │
       │                                ├──(operator pauses)──► paused
       │                                │                         │
       │                                │      ◄──(resume)────────┘
       │                                │
       └──(operator archives)──► archived ◄──(any state)

All transitions are operator-driven; this PR ships just the CRUD
plumbing. The Phase 18 UI will provide the buttons.

PostgREST chain API only (no raw SQL — CLAUDE.md "no psycopg in
backend" gate). Idempotent updates: ``update_status`` no-ops when the
row is already in the target state (predicate-driven).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Literal, Optional

logger = logging.getLogger(__name__)

SequenceStatus = Literal["draft", "active", "paused", "archived"]


@dataclass(frozen=True)
class Sequence:
    """Read-only view of a sequences row.

    Fields mirror the schema 1:1 — operator-side renames force a coordinated
    repo + caller update so silent drift doesn't accumulate.
    """

    id: str
    campaign_id: str
    name: str
    status: SequenceStatus
    created_at: str
    updated_at: str


def _row_to_sequence(row: dict[str, Any]) -> Sequence:
    return Sequence(
        id=row["id"],
        campaign_id=row["campaign_id"],
        name=row["name"],
        status=row["status"],
        created_at=row.get("created_at") or "",
        updated_at=row.get("updated_at") or "",
    )


class SequenceRepository:
    """PostgREST adapter for ``public.sequences``."""

    TABLE_NAME = "sequences"

    def __init__(self, db: Any) -> None:
        self._db = db

    async def list_active_for_campaign(self, campaign_id: str) -> list[Sequence]:
        """Active sequences for one campaign. Backed by partial index
        ``idx_sequences_campaign_active`` so empty / archived campaigns
        are cheap to query."""
        if not self._db or not campaign_id:
            return []
        rows = await asyncio.to_thread(
            lambda: (
                self._db.table(self.TABLE_NAME)
                .select("*")
                .eq("campaign_id", campaign_id)
                .eq("status", "active")
                .order("created_at", desc=False)
                .execute()
            )
        )
        return [_row_to_sequence(r) for r in (getattr(rows, "data", None) or [])]

    async def get_by_id(self, sequence_id: str) -> Optional[Sequence]:
        if not self._db or not sequence_id:
            return None
        rows = await asyncio.to_thread(
            lambda: (
                self._db.table(self.TABLE_NAME)
                .select("*")
                .eq("id", sequence_id)
                .limit(1)
                .execute()
            )
        )
        data = getattr(rows, "data", None) or []
        return _row_to_sequence(data[0]) if data else None

    async def create(
        self,
        campaign_id: str,
        name: str,
        *,
        status: SequenceStatus = "draft",
    ) -> Optional[Sequence]:
        """Insert one sequences row and return the inserted record.

        Returns None on insert failure (e.g. FK violation on a campaign_id
        that doesn't exist). The caller distinguishes None from an
        exception path — repo never raises unless the connection itself
        broke.
        """
        if not self._db or not campaign_id or not name:
            return None
        try:
            res = await asyncio.to_thread(
                lambda: (
                    self._db.table(self.TABLE_NAME)
                    .insert(
                        {
                            "campaign_id": campaign_id,
                            "name": name,
                            "status": status,
                        }
                    )
                    .execute()
                )
            )
        except Exception:
            logger.exception("SequenceRepository.create failed")
            return None
        data = getattr(res, "data", None) or []
        return _row_to_sequence(data[0]) if data else None

    async def update_status(
        self,
        sequence_id: str,
        new_status: SequenceStatus,
    ) -> bool:
        """Idempotent UPDATE. Predicate excludes ``status = new_status``
        so a no-op re-application doesn't touch ``updated_at``."""
        if not self._db or not sequence_id:
            return False
        try:
            res = await asyncio.to_thread(
                lambda: (
                    self._db.table(self.TABLE_NAME)
                    .update(
                        {
                            "status": new_status,
                            "updated_at": _now_iso(),
                        }
                    )
                    .eq("id", sequence_id)
                    .neq("status", new_status)
                    .execute()
                )
            )
        except Exception:
            logger.exception(
                "SequenceRepository.update_status failed for %s",
                sequence_id,
            )
            return False
        return bool(getattr(res, "data", None))


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


__all__ = ["Sequence", "SequenceStatus", "SequenceRepository"]
