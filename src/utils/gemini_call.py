"""Guarded wrappers around ``client.models.generate_content`` (sync)
and ``client.aio.models.generate_content`` (async).

Every Gemini call in ``src/`` MUST go through one of these helpers
so the daily token budget (see ``src/utils/gemini_budget.py``)
sees both the pre-call estimate AND the post-call real usage.

A single direct ``client.models.generate_content`` left somewhere
in the codebase is enough to defeat the breaker — the
``grep -rn "client\\.models\\.generate_content"`` rule in
``CLAUDE.md`` is the regression guard against that.

Contract
--------
- ``estimate_input``  — conservative pre-call estimate, in tokens.
                       Callers typically derive it as
                       ``len(prompt.encode("utf-8")) // 4`` which
                       is the documented Gemini approximation.
- ``estimate_output`` — config.max_output_tokens (or 2048 default).

The helper:
1. Calls ``check_budget(estimate_input, estimate_output)``.  On
   ``BudgetExceededError`` the underlying Gemini call is NEVER
   made — the exception propagates and the FastAPI exception
   handler maps it to HTTP 503.
2. Invokes the SDK ``generate_content`` call.
3. Reads ``response.usage_metadata`` (Gemini SDK >= 0.2).
   ``prompt_token_count`` + ``candidates_token_count`` are the
   real numbers.  If usage_metadata is missing (older SDK / mock
   / unusual response), we fall back to the same estimate the
   pre-debit used — net delta zero, no double-charge.
4. Calls ``record_usage(actual_in, actual_out, estimate_in, estimate_out)``
   so the budget table reflects real consumption.

The wrapper deliberately does NOT swallow Gemini SDK exceptions —
the existing call-site ``try/except`` blocks in each domain module
still catch and translate domain errors (e.g. ``"AI query failed"``).
"""

from __future__ import annotations

import logging
from typing import Any

from src.errors import AIQuotaExceededError
from src.utils.gemini_budget import check_budget, record_usage

logger = logging.getLogger(__name__)


def _is_quota_error(exc: BaseException) -> bool:
    """Return True iff `exc` is a Gemini SDK 429.

    The new `google-genai` SDK raises `google.genai.errors.ClientError`
    (subclass of `APIError`) carrying `.code = 429` for upstream quota.
    Match by both module path and `.code` so a future SDK refactor that
    swaps the class hierarchy still fires this branch instead of leaking
    the raw envelope to the client.
    """
    if getattr(exc, "code", None) != 429:
        return False
    mod = type(exc).__module__ or ""
    return mod.startswith("google.genai") or mod.startswith("google.api_core")


def _extract_usage(
    response: Any, fallback_in: int, fallback_out: int
) -> tuple[int, int]:
    """Extract ``(prompt_tokens, candidates_tokens)`` from a Gemini
    response.  Defensive: any missing field or non-int value falls
    back to the supplied estimate so the post-call delta is zero
    instead of accidentally double-charging or reverting the pre-debit.
    """
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return fallback_in, fallback_out
    try:
        prompt_tokens = int(getattr(meta, "prompt_token_count", None) or fallback_in)
    except (TypeError, ValueError):
        prompt_tokens = fallback_in
    try:
        candidates_tokens = int(
            getattr(meta, "candidates_token_count", None) or fallback_out
        )
    except (TypeError, ValueError):
        candidates_tokens = fallback_out
    return prompt_tokens, candidates_tokens


def guarded_generate_content(
    client: Any,
    *,
    model: str,
    contents: Any,
    config: Any,
    estimate_input: int,
    estimate_output: int,
) -> Any:
    """Sync helper.  Wraps ``client.models.generate_content``.

    All call sites in ``src/core/agentic_router.py`` (5 sites)
    and ``src/processors/ai_mapper.py`` (1 site) route through here.
    """
    est_in = max(0, int(estimate_input))
    est_out = max(0, int(estimate_output))
    # BudgetExceededError propagates here — caller MUST NOT have
    # already issued the Gemini call.
    check_budget(est_in, est_out)
    try:
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )
    except Exception as exc:
        if _is_quota_error(exc):
            logger.warning("Gemini upstream 429 — raising AIQuotaExceededError")
            raise AIQuotaExceededError("upstream Gemini quota exhausted") from exc
        raise
    actual_in, actual_out = _extract_usage(response, est_in, est_out)
    record_usage(actual_in, actual_out, est_in, est_out)
    return response


async def guarded_generate_content_async(
    client: Any,
    *,
    model: str,
    contents: Any,
    config: Any,
    estimate_input: int,
    estimate_output: int,
) -> Any:
    """Async helper.  Wraps ``client.aio.models.generate_content``.

    All call sites in ``src/processors/leadhunter.py`` (3 sites)
    and ``src/scrapers/enrichment_engine.py`` (1 site) route through
    here.  The budget I/O is sync (SQLite) — the helper does NOT hop
    to ``asyncio.to_thread`` because the SQLite write is sub-millisecond
    and the lock contention is dominated by the multi-second Gemini
    call, not the budget bookkeeping.  If profiling ever flags this,
    wrap ``check_budget`` / ``record_usage`` in ``to_thread``.
    """
    est_in = max(0, int(estimate_input))
    est_out = max(0, int(estimate_output))
    check_budget(est_in, est_out)
    try:
        response = await client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )
    except Exception as exc:
        if _is_quota_error(exc):
            logger.warning("Gemini upstream 429 (async) — raising AIQuotaExceededError")
            raise AIQuotaExceededError("upstream Gemini quota exhausted") from exc
        raise
    actual_in, actual_out = _extract_usage(response, est_in, est_out)
    record_usage(actual_in, actual_out, est_in, est_out)
    return response


def estimate_tokens_from_text(text: str) -> int:
    """Rough Gemini token estimate.  Gemini's tokenizer is ~4 bytes
    per token on prose.  Use ``utf-8`` byte length / 4 as the
    conservative ceiling.  Multilingual + dense content will be
    under-estimated; that's acceptable for the pre-call gate because
    the post-call ``record_usage`` reconciles to real numbers.

    Conservative variant: integer division floors, so callers should
    not panic if the estimate undershoots by a few tokens.
    """
    if not text:
        return 0
    return len(text.encode("utf-8", errors="ignore")) // 4
