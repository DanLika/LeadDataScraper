"""Instantly webhook event policy — bounce_type discrimination (PR #359).

The pre-#359 handler (`backend/main.py::_instantly_handle_bounced`)
collapsed every ``email_bounced`` into a permanent ``bounce_hard``
suppression with no inspection of ``payload.bounce_type``. That cost
us recoverable addresses on transient 4xx SMTP failures and left the
``bounce_soft_3x`` taxonomy slot unused.

This module owns the policy decision only. Side effects (DB writes,
state-machine transitions, sequence cancels) stay at the handler call
site in ``backend/main.py`` so the decision logic remains pure and
snapshot-testable in ``tests/unit/test_bounce_policy.py``.

Policy table
------------

================== =================== =====================
bounce_type        prior_soft_count    BounceAction
================== =================== =====================
hard / permanent   any                 ``suppress_hard``
soft / transient   < threshold (3)     ``noop_soft``
soft / transient   >= threshold        ``suppress_soft_3x``
missing / empty    any                 ``suppress_hard``  (defensive)
unknown value      any                 ``suppress_hard``  (logged WARN)
================== =================== =====================

The "missing → suppress_hard" branch preserves pre-#359 behavior on
the prod traffic Instantly emits today (which sometimes omits
``bounce_type``). Tightening to ``noop_soft`` on missing would
silently widen the bounce-tolerance surface — we'd rather over-suppress
on absent metadata than open a recoverable-address regression.

Counter scope
-------------

``prior_soft_count`` is sourced from
``WebhookEventRepository.count_soft_bounces_for_recipient`` (30-day
window, per-recipient, ``event_type='email_bounced'`` AND
``payload->>bounce_type`` ILIKE ``'soft'``). The count INCLUDES the
current event because ``webhook_events`` INSERT precedes handler
dispatch in ``_process_instantly_event``. So ``threshold=3`` means
"the 3rd or later soft within 30 days flips us to permanent".

Future refinement (not in #359): switch to "consecutive softs since
last ``email_sent`` success" — requires a reset signal from the
sequence engine. Tracked separately; the 30-day window is the simpler
correct-direction policy that ships today.
"""
from __future__ import annotations

import logging
from typing import Literal

logger = logging.getLogger(__name__)

BounceAction = Literal["suppress_hard", "suppress_soft_3x", "noop_soft"]

SOFT_THRESHOLD = 3
"""Soft-bounce strikes (within the counter window) at which we flip
to permanent suppression. Matches the ``bounce_soft_3x`` slot in the
``suppressions_reason_allowed`` DB CHECK constraint — renaming this
constant requires a coordinated schema + handler PR."""

SOFT_COUNTER_WINDOW_DAYS = 30
"""Look-back window for the soft-bounce counter. 30 days matches
common ESP retention (Sendgrid event_log default) and is long enough
to catch genuine 3-strike patterns without dragging in stale events
from a re-engaged mailbox."""

_HARD_TYPES: frozenset[str] = frozenset({
    "hard",        # canonical
    "permanent",   # Instantly synonym in some payloads
    "blocked",     # 550-class refusal at recipient server
    "rejected",    # explicit rejection (e.g. spam-block)
})

_SOFT_TYPES: frozenset[str] = frozenset({
    "soft",        # canonical
    "transient",   # 4xx SMTP
    "temporary",   # Instantly synonym
    "deferred",    # mailbox full / greylist
})


def decide_bounce_action(
    bounce_type: str | None,
    prior_soft_count: int,
    *,
    threshold: int = SOFT_THRESHOLD,
) -> BounceAction:
    """Map ``(bounce_type, prior_soft_count)`` to a ``BounceAction``.

    Pure function — no DB, no IO. The only side effect is a single
    WARN log line on an unknown bounce_type value, so operators see
    new provider strings in time to widen the allowlist.

    Args:
        bounce_type: Raw value from ``payload['bounce_type']``. May be
            ``None``, empty, or any provider tag. Match is
            case-insensitive + whitespace-trimmed.
        prior_soft_count: Soft bounces for this recipient in the counter
            window INCLUDING the current event (the count is taken
            after ``webhook_events`` INSERT in ``_process_instantly_event``).
            When >= ``threshold``, the current event flips suppression.
        threshold: Strikes-to-suppress. Default ``SOFT_THRESHOLD=3``
            aligns with the ``bounce_soft_3x`` taxonomy.

    Returns:
        - ``"suppress_hard"``    — immediate permanent suppression
        - ``"suppress_soft_3x"`` — soft-strike threshold crossed
        - ``"noop_soft"``        — soft bounce under threshold; no
          suppression row, downstream sequence steps continue
    """
    norm = (bounce_type or "").strip().lower()
    if norm in _SOFT_TYPES:
        return "suppress_soft_3x" if prior_soft_count >= threshold else "noop_soft"
    if norm in _HARD_TYPES:
        return "suppress_hard"
    if not norm:
        return "suppress_hard"
    logger.warning(
        "unknown bounce_type %r — defaulting to suppress_hard",
        norm,
    )
    return "suppress_hard"


__all__ = [
    "BounceAction",
    "SOFT_THRESHOLD",
    "SOFT_COUNTER_WINDOW_DAYS",
    "decide_bounce_action",
]
