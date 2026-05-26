"""SequenceVariantRepository — PostgREST I/O for ``public.sequence_variants``.

Phase 15.1 — A/B copy split per step. The variant selector (Phase 15.3)
draws weighted-random across the per-step variant set at dispatch
time. ``variant_label`` is A..Z (DB CHECK ``sequence_variants_label_format``);
``weight`` is a positive integer that the selector normalizes.

Tracking ``ai_model_used`` + ``ai_prompt_version`` lets the future
analytics view answer "which model generates the highest-reply-rate
variant" without re-deriving from logs. Both are operator-set strings;
no FK to a model registry (would over-couple the repo to the AI
infrastructure).
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

_VARIANT_LABEL_RE = re.compile(r"^[A-Z]$")


@dataclass(frozen=True)
class SequenceVariant:
    """Read-only view of a sequence_variants row."""

    id: str
    step_id: str
    variant_label: str  # single uppercase letter
    subject_template: Optional[str]
    body_template: str
    weight: int
    ai_model_used: Optional[str]
    ai_prompt_version: Optional[str]
    created_at: str


def _row_to_variant(row: dict[str, Any]) -> SequenceVariant:
    return SequenceVariant(
        id=row["id"],
        step_id=row["step_id"],
        variant_label=row["variant_label"],
        subject_template=row.get("subject_template"),
        body_template=row.get("body_template") or "",
        weight=int(row.get("weight") or 50),
        ai_model_used=row.get("ai_model_used"),
        ai_prompt_version=row.get("ai_prompt_version"),
        created_at=row.get("created_at") or "",
    )


class SequenceVariantRepository:
    """PostgREST adapter for ``public.sequence_variants``."""

    TABLE_NAME = "sequence_variants"

    def __init__(self, db: Any) -> None:
        self._db = db

    async def list_for_step(self, step_id: str) -> list[SequenceVariant]:
        """All variants for one step. Ordered by variant_label so the
        selector sees a stable A,B,C,... order — important for
        deterministic-seed tests."""
        if not self._db or not step_id:
            return []
        rows = await asyncio.to_thread(
            lambda: (
                self._db.table(self.TABLE_NAME)
                .select("*")
                .eq("step_id", step_id)
                .order("variant_label", desc=False)
                .execute()
            )
        )
        return [_row_to_variant(r) for r in (getattr(rows, "data", None) or [])]

    async def create(
        self,
        step_id: str,
        variant_label: str,
        body_template: str,
        *,
        subject_template: Optional[str] = None,
        weight: int = 50,
        ai_model_used: Optional[str] = None,
        ai_prompt_version: Optional[str] = None,
    ) -> Optional[SequenceVariant]:
        """Insert a variant. Client-side label format + weight validation
        before the round-trip — the DB CHECK is the authoritative gate
        but pre-checking keeps the error path uniform with the rest of
        the repo layer (returns None on bad input + on UNIQUE collision)."""
        if not self._db or not step_id or not body_template:
            return None
        if not _VARIANT_LABEL_RE.match(variant_label or ""):
            logger.info(
                "SequenceVariantRepository.create rejected bad label %r",
                variant_label,
            )
            return None
        if weight <= 0:
            logger.info(
                "SequenceVariantRepository.create rejected non-positive weight %d",
                weight,
            )
            return None
        try:
            res = await asyncio.to_thread(
                lambda: (
                    self._db.table(self.TABLE_NAME)
                    .insert({
                        "step_id": step_id,
                        "variant_label": variant_label,
                        "subject_template": subject_template,
                        "body_template": body_template,
                        "weight": weight,
                        "ai_model_used": ai_model_used,
                        "ai_prompt_version": ai_prompt_version,
                    })
                    .execute()
                )
            )
        except Exception as exc:  # noqa: BLE001 — narrow inline
            if _is_unique_violation(exc):
                logger.info(
                    "SequenceVariantRepository.create UNIQUE collision (%s, %s)",
                    step_id, variant_label,
                )
                return None
            logger.exception("SequenceVariantRepository.create failed")
            return None
        data = getattr(res, "data", None) or []
        return _row_to_variant(data[0]) if data else None


def _is_unique_violation(exc: Exception) -> bool:
    code = getattr(exc, "code", None)
    if code == "23505":
        return True
    msg = str(exc).lower()
    return "23505" in msg or "duplicate key" in msg


__all__ = ["SequenceVariant", "SequenceVariantRepository"]
