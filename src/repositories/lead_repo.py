"""LeadRepository — minimal PostgREST adapter for ``public.leads``.

Phase 15.3 adds this thin repo specifically for the dispatch tick's
batch-fetch pattern. The existing ``SupabaseHelper`` methods (per
``src/utils/supabase_helper.py``) are pipeline-stage-specific
(``list_leads_recent``, ``get_stats_rows``) and don't include a
batch ``WHERE unique_key IN (...)`` shape — the dispatch worker
needs that one shape to avoid N+1 lookups across the 100-message
claim batch.

Keeps the projection minimal — only the fields the variant template
renderer + AUP-injection layer actually use. Adding a field requires
extending ``ALLOWED_VARS`` in :mod:`src.services.template_renderer`
in the same PR.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# Projection — keep in sync with template_renderer.ALLOWED_VARS.
# Stored as a tuple so PostgREST `.select(",".join(...))` is stable.
_LEAD_FIELDS: tuple[str, ...] = (
    "unique_key",
    "email",
    "first_name",
    "last_name",
    "company_name",
    "website",
    "address",
    "segment",
    "outreach_score",
    "pain_points",
)


class LeadRepository:
    """Batch-fetch surface for ``public.leads``.

    Constructor takes the same supabase-py client as the rest of the
    repo layer (``SupabaseHelper().client``). Stateless.
    """

    TABLE_NAME = "leads"

    def __init__(self, db: Any) -> None:
        self._db = db

    async def fetch_many(self, unique_keys: Iterable[str]) -> dict[str, dict[str, Any]]:
        """Return a ``unique_key → row-dict`` mapping for the given keys.

        Single PostgREST round trip (``WHERE unique_key IN (...)``).
        Missing keys are silently absent from the returned dict —
        caller's responsibility to detect.

        Empty input returns ``{}`` without a round trip.
        """
        if not self._db:
            return {}
        keys = [k for k in (unique_keys or []) if k]
        if not keys:
            return {}
        # Dedupe for the IN clause (PostgREST tolerates duplicates but
        # generates a longer URL).
        unique_inputs = list(dict.fromkeys(keys))
        try:
            rows = await asyncio.to_thread(
                lambda: (
                    self._db.table(self.TABLE_NAME)
                    .select(",".join(_LEAD_FIELDS))
                    .in_("unique_key", unique_inputs)
                    .execute()
                )
            )
        except Exception:
            logger.exception(
                "LeadRepository.fetch_many failed for %d keys",
                len(unique_inputs),
            )
            return {}
        data = getattr(rows, "data", None) or []
        return {r["unique_key"]: r for r in data if r.get("unique_key")}


__all__ = ["LeadRepository"]
