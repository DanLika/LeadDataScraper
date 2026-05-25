"""Variant CRUD orchestration — validation hooks + repo call.

Sits between handlers / Phase 18 UI / AI personalization layer (Phase
18) and ``SequenceVariantRepository``. Centralises the
template-validation pipeline so any caller (operator UI, AI generator,
seed script) gets the same enforcement:

  1. Syntax check — Jinja2 AST parse, reject bad templates
  2. ALLOWED_VARS check — every var in body / subject must be in the
     allowlist. Reject early with DisallowedVariableError listing the
     offending vars.
  3. Cold-AUP check (channel-conditional) — when ``step.channel='email'``,
     the body MUST reference ``{{ unsubscribe_url }}``.
  4. Insert via repo — UNIQUE collision on (step_id, variant_label)
     returns None per the repo contract.

Decoupled from FastAPI so Phase 18's AI generator (which won't go
through an HTTP handler) lands the same enforcement path.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from src.repositories.sequence_variant_repo import (
    SequenceVariant,
    SequenceVariantRepository,
)
from src.services.template_renderer import (
    DisallowedVariableError,
    MissingUnsubscribeUrlError,
    TemplateError,
    assert_cold_email_unsubscribe,
    validate_template_vars,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CreateVariantResult:
    """Service-level outcome envelope. ``ok=False`` + error_code lets
    the handler map to the right HTTP status without inspecting the
    exception type."""

    ok: bool
    variant: Optional[SequenceVariant] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    disallowed_vars: tuple[str, ...] = ()


# Error codes — pinned so HTTP handlers + tests use the same vocabulary.
class ErrorCodes:
    SYNTAX = "template_syntax"
    DISALLOWED_VARS = "disallowed_vars"
    MISSING_UNSUBSCRIBE = "missing_unsubscribe_url"
    DUPLICATE = "duplicate_label"
    UNKNOWN = "unknown"


class VariantService:
    """Wraps :class:`SequenceVariantRepository` with the validation
    pipeline. Stateless; constructed per request / per worker tick.
    """

    def __init__(
        self,
        repo: SequenceVariantRepository,
    ) -> None:
        self._repo = repo

    async def create_variant(
        self,
        *,
        step_id: str,
        step_channel: str,
        variant_label: str,
        body_template: str,
        subject_template: Optional[str] = None,
        weight: int = 50,
        ai_model_used: Optional[str] = None,
        ai_prompt_version: Optional[str] = None,
    ) -> CreateVariantResult:
        """Validate templates → insert via repo. Returns a structured
        result; the handler / caller maps to HTTP status by inspecting
        ``error_code``.
        """
        # 1+2. Subject + body syntax + ALLOWED_VARS check. Subject is
        # optional (may be empty for thread-continuation steps); only
        # validate it when non-empty.
        for label, tpl in (
            ("subject_template", subject_template or ""),
            ("body_template", body_template),
        ):
            if not tpl and label == "subject_template":
                continue
            try:
                disallowed = validate_template_vars(tpl)
            except TemplateError as exc:
                return CreateVariantResult(
                    ok=False,
                    error_code=ErrorCodes.SYNTAX,
                    error_message=f"{label}: {exc}",
                )
            if disallowed:
                return CreateVariantResult(
                    ok=False,
                    error_code=ErrorCodes.DISALLOWED_VARS,
                    error_message=(
                        f"{label} uses disallowed variables: "
                        f"{', '.join(disallowed)}"
                    ),
                    disallowed_vars=tuple(disallowed),
                )

        # 3. Cold-AUP gate — email channel only. LinkedIn variants
        # skip (HeyReach connection requests don't carry an
        # unsubscribe URL; opt-out is the LinkedIn-side reject button).
        if step_channel == "email":
            try:
                assert_cold_email_unsubscribe(body_template)
            except MissingUnsubscribeUrlError as exc:
                return CreateVariantResult(
                    ok=False,
                    error_code=ErrorCodes.MISSING_UNSUBSCRIBE,
                    error_message=str(exc),
                )
            except TemplateError as exc:
                return CreateVariantResult(
                    ok=False,
                    error_code=ErrorCodes.SYNTAX,
                    error_message=str(exc),
                )

        # 4. Insert via repo — None on UNIQUE collision OR bad input.
        # Distinguish duplicate (specific error code) from other
        # repo failures.
        variant = await self._repo.create(
            step_id=step_id,
            variant_label=variant_label,
            body_template=body_template,
            subject_template=subject_template,
            weight=weight,
            ai_model_used=ai_model_used,
            ai_prompt_version=ai_prompt_version,
        )
        if variant is None:
            # The repo's create() returns None on duplicate AND on
            # other validation failures; without a typed return we
            # fall back to the duplicate-label code, which is the
            # most likely cause given prior validation passed.
            return CreateVariantResult(
                ok=False,
                error_code=ErrorCodes.DUPLICATE,
                error_message=(
                    f"variant with label {variant_label!r} already exists "
                    f"for step {step_id}"
                ),
            )
        return CreateVariantResult(ok=True, variant=variant)


__all__ = ["VariantService", "CreateVariantResult", "ErrorCodes"]
