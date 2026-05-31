"""Phase 16 reply classifier service — stub scaffold.

The Resend webhook handler at ``POST /webhooks/resend`` delegates
the per-event business logic to this service. Every public method is
gated behind ``PHASE16_REPLY_CLASSIFIER=1`` so the *endpoint* can
land + verify HMAC + log against production traffic from day one,
while the *side-effects* (Anthropic call, DB writes, sequence pause)
stay dark until the operator flips the flag.

State-machine contract (Option A — per-lead pause)
--------------------------------------------------
When a classification lands in a terminal bucket
(``interested``, ``not_interested``, ``unsubscribe_request``,
``complaint``), the service:

  * Stamps every PENDING / DISPATCHING ``campaign_messages`` row for
    that ``lead_unique_key`` with ``status='paused_by_reply'``.
  * Writes the classification row to ``reply_classifications`` for
    operator review.
  * Does NOT flip ``sequences.paused_on_reply`` — that boolean is
    operator-UI-only by design (whole-sequence pause is a separate
    operator action, e.g. for a misfiring template). See PR #476 +
    schema-section ``Phase 16 — Reply classification + auto-pause
    state machine`` for the column-purpose rationale.

Non-terminal classes
--------------------
  * ``ooo``: defer-and-resume planned (push pending ``scheduled_at``
    by 7 days). T2 stub logs the intent with
    ``extra={lead_unique_key, classification, expected_resume_at}``
    so a manual operator can replay; mutation lands in T2-followup.
  * ``wrong_person``: write a suppression row for the lead's email
    so future campaigns skip them. Stub logs the intent.
  * ``bounce_soft`` / ``bounce_hard`` / ``auto_reply`` /
    ``asking_for_info`` / ``other``: log + store classification; no
    state mutation. Operator review queue handles them.

DB schema dependency
--------------------
Every PostgREST chain lives behind the ``self._enabled`` check so
that an LDS deployment with ``PHASE16_REPLY_CLASSIFIER=0`` (the
default) never references ``reply_classifications`` or
``campaign_messages.status='paused_by_reply'`` at runtime. This
means PR #476's schema (which adds both) can land out-of-order or
remain un-applied during the T2 stub window without 23514'ing the
backend.
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


#: Terminal classifications — these flip the per-lead pause. Mirrors
#: the spec'd auto-pause set in the Phase 16 task description. Adding
#: a new terminal bucket requires schema-side awareness of the
#: classification value (already covered by the 11-enum CHECK).
TERMINAL_CLASSIFICATIONS: frozenset[str] = frozenset(
    {
        "interested",
        "not_interested",
        "unsubscribe_request",
        "complaint",
    }
)

#: Defer-and-resume window for OOO replies. 7 days matches the
#: typical short-vacation horizon; longer OOOs (parental leave,
#: extended sabbatical) read as "not_interested" or "wrong_person"
#: by the classifier, so the 7-day window does not need to cover them.
OOO_RESUME_AFTER_DAYS = 7

#: Suppression source tag used when wrong_person triggers a write.
#: Mirrors the existing ``suppressions.source`` discriminator used by
#: unsubscribe + manual-suppression flows.
SUPPRESSION_SOURCE_WRONG_PERSON = "phase16_classifier_wrong_person"


def is_enabled(env: dict[str, str] | None = None) -> bool:
    """True when ``PHASE16_REPLY_CLASSIFIER=1`` in the env.

    The check is centralised so a test can substitute env without
    monkey-patching ``os.environ`` globally. Production code never
    passes the ``env`` arg.
    """
    source = env if env is not None else os.environ
    return source.get("PHASE16_REPLY_CLASSIFIER", "0") == "1"


# --- Pre-processing -------------------------------------------------------

# Heuristic quote markers — every line beginning with one of these is
# treated as a quoted earlier message and dropped before classification.
# The set covers RFC 3676 ('> '), Outlook-style (no marker but preceded
# by an "On <date> wrote:" line — handled separately below), and Gmail's
# "On <weekday>, <date> <name> wrote:" pattern.
_QUOTE_LINE_PREFIXES = ("> ", ">>", ">>>")

# A "wrote:" line generally signals the start of a quoted reply. Strip
# from the first one onward. Languages handled: en + hr ("napisao je:",
# "napisala je:"). Provider-language detection is out of scope here.
_QUOTE_LEAD_PATTERNS = (
    " wrote:",
    " wrote ::",
    " napisao je:",
    " napisala je:",
)

# Signature delimiter — RFC 3676 "-- " (dash-dash-space-CR-LF). Mail
# clients commonly strip trailing whitespace, so we match the rstrip'd
# form ("--") rather than the canonical "-- ". Anything after the
# delimiter line is treated as signature.
_SIG_DELIM_RSTRIPPED = "--"


def preprocess_reply_body(text: str) -> str:
    """Strip quoted text + signatures from an inbound reply.

    Intentionally lossy — the classifier wants only the new content
    the sender authored. We over-strip quoted text; if that leaves an
    empty body the classifier returns ``other`` and the operator
    review queue catches it.

    Args:
        text: Raw reply body. May include quoted-text + sig + footer
            (typical Gmail / Outlook shape).

    Returns:
        The cleaned body. Trailing whitespace and blank-line runs are
        collapsed.
    """
    if not text:
        return ""

    lines = text.splitlines()
    cleaned: list[str] = []
    seen_sig = False
    for raw_line in lines:
        line = raw_line.rstrip()
        if line == _SIG_DELIM_RSTRIPPED:
            seen_sig = True
            break
        if any(p in line for p in _QUOTE_LEAD_PATTERNS):
            break
        if line.startswith(_QUOTE_LINE_PREFIXES):
            continue
        cleaned.append(line)

    if seen_sig:
        # Trailing whitespace before the sig delim is also pure noise.
        while cleaned and not cleaned[-1]:
            cleaned.pop()

    return "\n".join(cleaned).strip()


def body_hash(text: str) -> str:
    """SHA256 hex digest of the preprocessed body — the idempotency key
    for ``reply_classifications.message_body_hash``.

    Same input → same hash → INSERT 23505 on replay (the
    ``reply_classifications_unique_classification`` UNIQUE constraint
    set up in PR #476 catches the duplicate).
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- Service --------------------------------------------------------------


class ReplyClassifierService:
    """Stub service. Public API stable for T2; method bodies fill in
    over later PRs:

      * ``classify_via_anthropic()`` — Anthropic Messages API call.
        Stub logs + returns ``None``. Lands when ``ANTHROPIC_API_KEY``
        + ``anthropic`` package are wired (memory
        [[phase16-classifier-bench-2026-05-30]] tracks the gating).
      * ``store_classification()`` — INSERT into reply_classifications.
        Stub no-op when disabled; schema dep documented above.
      * ``apply_state_transitions()`` — per-lead pause + suppress.
        Stub logs intended mutation under ``logger.info`` with
        structured ``extra={…}`` so an operator-side grep can rebuild
        the pause set by hand if the flag flips before the SQL lands.
    """

    def __init__(self, db: Any, *, enabled: bool | None = None) -> None:
        self.db = db
        # Cache the flag at construction so a mid-request env flip
        # cannot cause a partial side-effect cycle (e.g. classify +
        # store fire, but state-transition skips).
        self._enabled = is_enabled() if enabled is None else enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def handle_replied_event(
        self,
        *,
        reply_body: str,
        lead_unique_key: str,
        campaign_message_id: str | None,
        provider_event_id: str,
    ) -> dict[str, Any] | None:
        """Top-level entry point from the webhook handler.

        Pipeline: preprocess → classify → store → transition.

        Returns the classification dict on success, ``None`` when
        disabled or when classification fails (errors logged; no
        raise — webhook ack must succeed).
        """
        cleaned = preprocess_reply_body(reply_body)
        if not cleaned:
            logger.info(
                "phase16 classifier: empty body after preprocess; skipping",
                extra={
                    "lead_unique_key": lead_unique_key,
                    "campaign_message_id": campaign_message_id,
                    "provider_event_id": provider_event_id,
                },
            )
            return None

        if not self._enabled:
            logger.info(
                "phase16 classifier disabled; logging-only ack",
                extra={
                    "lead_unique_key": lead_unique_key,
                    "campaign_message_id": campaign_message_id,
                    "provider_event_id": provider_event_id,
                    "body_chars": len(cleaned),
                    "body_hash": body_hash(cleaned),
                },
            )
            return None

        classification = await self.classify_via_anthropic(cleaned)
        if classification is None:
            return None

        await self.store_classification(
            classification=classification,
            cleaned_body=cleaned,
            lead_unique_key=lead_unique_key,
            campaign_message_id=campaign_message_id,
        )
        await self.apply_state_transitions(
            classification=classification["category"],
            confidence=float(classification.get("confidence", 0.0)),
            lead_unique_key=lead_unique_key,
        )
        return classification

    async def classify_via_anthropic(self, cleaned_body: str) -> dict[str, Any] | None:
        """STUB. Wires to Anthropic SDK in a follow-up PR.

        When wired:
          * Imports lazy: ``import anthropic``.
          * Uses the prompt builder from
            ``src.services.reply_classifier_prompt.build_classification_messages``
            (lives on Phase 16 PR #477 branch ``feat/phase16-classifier-bench``).
          * Returns ``{"category": ..., "confidence": ..., "reasoning": ...}``
            on success; ``None`` on any SDK error so the webhook ack
            never blocks on classifier health.
        """
        logger.warning(
            "phase16 classify_via_anthropic stub hit — wire Anthropic SDK in "
            "follow-up PR before flipping PHASE16_REPLY_CLASSIFIER=1",
            extra={"body_chars": len(cleaned_body)},
        )
        return None

    async def store_classification(
        self,
        *,
        classification: dict[str, Any],
        cleaned_body: str,
        lead_unique_key: str,
        campaign_message_id: str | None,
    ) -> None:
        """STUB. INSERTs into reply_classifications via PostgREST.

        Idempotency: the schema's UNIQUE (lead_unique_key,
        message_body_hash) catches replay; the implementation will
        treat 23505 as success (already-classified).
        """
        logger.info(
            "phase16 store_classification stub — would INSERT reply_classifications",
            extra={
                "lead_unique_key": lead_unique_key,
                "campaign_message_id": campaign_message_id,
                "classification": classification.get("category"),
                "confidence": classification.get("confidence"),
                "body_hash": body_hash(cleaned_body),
            },
        )

    async def apply_state_transitions(
        self,
        *,
        classification: str,
        confidence: float,
        lead_unique_key: str,
    ) -> None:
        """Apply the per-classification side-effects.

        Logs the intended mutation always; the actual PostgREST call
        is a STUB in T2. Each transition emits a structured log line
        whose ``extra={...}`` carries enough state for an operator to
        replay manually before the SQL lands.

        Transitions:
            * terminal class → bulk UPDATE campaign_messages.status
              FROM ('pending', 'dispatching') TO 'paused_by_reply'
              WHERE lead_unique_key = X
            * 'wrong_person' → INSERT suppression row
              (source=SUPPRESSION_SOURCE_WRONG_PERSON)
            * 'ooo' → push scheduled_at by OOO_RESUME_AFTER_DAYS
              for pending rows of this lead
            * others → no-op (operator review handles them)
        """
        if classification in TERMINAL_CLASSIFICATIONS:
            logger.info(
                "phase16 apply_state_transitions stub — would mark pending "
                "campaign_messages paused_by_reply",
                extra={
                    "lead_unique_key": lead_unique_key,
                    "classification": classification,
                    "confidence": confidence,
                    "target_status": "paused_by_reply",
                    "from_statuses": ["pending", "dispatching"],
                },
            )
            if classification == "unsubscribe_request":
                logger.info(
                    "phase16 apply_state_transitions stub — would also "
                    "INSERT suppression",
                    extra={
                        "lead_unique_key": lead_unique_key,
                        "source": "phase16_classifier_unsubscribe",
                    },
                )
            return

        if classification == "wrong_person":
            logger.info(
                "phase16 apply_state_transitions stub — would INSERT "
                "suppression (wrong_person)",
                extra={
                    "lead_unique_key": lead_unique_key,
                    "source": SUPPRESSION_SOURCE_WRONG_PERSON,
                    "confidence": confidence,
                },
            )
            return

        if classification == "ooo":
            resume_at = datetime.now(timezone.utc) + timedelta(
                days=OOO_RESUME_AFTER_DAYS
            )
            logger.info(
                "phase16 apply_state_transitions stub — would defer pending "
                f"sends {OOO_RESUME_AFTER_DAYS}d",
                extra={
                    "lead_unique_key": lead_unique_key,
                    "expected_resume_at": resume_at.isoformat(),
                    "classification": classification,
                    "confidence": confidence,
                },
            )
            return

        logger.debug(
            "phase16 apply_state_transitions — no-op transition",
            extra={
                "lead_unique_key": lead_unique_key,
                "classification": classification,
                "confidence": confidence,
            },
        )
