"""Weighted-random variant selection for A/B testing.

The dispatch tick calls :func:`select_variant` once per sequence step
to choose which copy variant to render and send. Selection is
weighted random by ``SequenceVariant.weight``; ties + zero-weight
fallback handled defensively.

**Deterministic seed safety**

Tests need reproducible variant picks (assertion order, golden
files). A naive ``deterministic_seed`` kwarg is dangerous: anyone
who sets it in production env would freeze A/B testing on a single
variant silently. The safety net:

  * ``deterministic_seed`` is a per-call kwarg
  * Honored ONLY when env ``VARIANT_SELECTOR_ALLOW_SEED=1`` is set
  * Otherwise: log a warning + fall through to random selection
  * Production env never sets ``VARIANT_SELECTOR_ALLOW_SEED``;
    pytest fixtures set it explicitly

This means a seed value smuggled into production via a request /
config bug doesn't disable A/B testing — the warning surfaces in
Sentry on first hit and the random selection continues.
"""

from __future__ import annotations

import hashlib
import logging
import os
import random
from typing import Iterable, Optional, Protocol

logger = logging.getLogger(__name__)


class _Weighted(Protocol):
    """Minimal interface — works with ``SequenceVariant`` (Phase 15.1)
    or any equivalent shape. Decouples the selector from a specific
    repo type so unit tests can pass plain namedtuples."""

    @property
    def id(self) -> str: ...

    @property
    def variant_label(self) -> str: ...

    @property
    def weight(self) -> int: ...


def select_variant(
    variants: Iterable[_Weighted],
    *,
    deterministic_seed: Optional[str] = None,
) -> Optional[_Weighted]:
    """Return one variant from ``variants``, weighted by ``.weight``.

    Empty input → None (no variants to pick from; caller decides whether
    to skip the message or alert the operator). Single-item input →
    that item regardless of weight (defensive — saves a random call).

    ``deterministic_seed`` is honored only when
    ``VARIANT_SELECTOR_ALLOW_SEED=1`` env is set. Production env must
    NOT set the gate; pytest fixtures DO set it explicitly so the
    seed produces reproducible picks for assertions.
    """
    variant_list = list(variants) if variants else []
    if not variant_list:
        return None
    if len(variant_list) == 1:
        return variant_list[0]

    # Validate weights — defensive against bad data slipping past the
    # DB CHECK constraint sequence_variants_weight_positive (would only
    # happen via direct Studio edit, but the selector shouldn't blow
    # up if it does).
    weights = [max(1, int(getattr(v, "weight", 1) or 1)) for v in variant_list]

    rng = _resolve_rng(deterministic_seed)
    chosen = rng.choices(variant_list, weights=weights, k=1)[0]
    return chosen


def _resolve_rng(deterministic_seed: Optional[str]) -> random.Random:
    """Return a Random instance, deterministic if env gate is set + seed
    provided, otherwise system-random."""
    if not deterministic_seed:
        return random.SystemRandom()
    # Literal '1' only — no whitespace tolerance, no truthy-string
    # parsing. Conservative opt-in: a leaky env value like " 1" or
    # "yes" must NOT accidentally disable A/B testing in production.
    if os.environ.get("VARIANT_SELECTOR_ALLOW_SEED") != "1":
        logger.warning(
            "deterministic_seed=%r passed but VARIANT_SELECTOR_ALLOW_SEED "
            "is not 1; ignoring and using SystemRandom",
            _redact_seed(deterministic_seed),
        )
        return random.SystemRandom()
    # Hash the seed to a stable int — direct string seed would be
    # restricted to identical-Python-version reproducibility; the
    # SHA256 hash collapses that.
    digest = hashlib.sha256(deterministic_seed.encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _redact_seed(seed: str) -> str:
    """Don't log the full seed value (might be a tracking_id, which is
    user-facing data via the unsubscribe URL). Hash for diagnostics."""
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


__all__ = ["select_variant"]
