"""Instantly v2 cold-outreach dispatcher.

Scope — **cold outreach only.** Resend AUP forbids cold sends from
owned domains, so cold mail goes through Instantly's rotating
cold-sender pool (per ``docs/email-dispatch-architecture.md`` §0).
The dispatcher writes one row per attempted send to
``email_send_ledger`` with ``provider='instantly'`` so the orchestrator
can throttle per-provider AND keep cost accounting separate from the
warm-path Resend reputation.

Phase 14.1 — bulk-push endpoint only. Webhook handler ships in Phase
14.2; suppression-driven retries in Phase 14.3.

API reference: https://developer.instantly.ai/api/v2
"""
from __future__ import annotations

import logging
import os
from typing import Any, ClassVar, Optional, TYPE_CHECKING

import aiohttp  # type: ignore[import-not-found]

from src.integrations.email_sender import EmailDispatcher
from src.integrations.instantly_models import (
    InstantlyError,
    InstantlyLeadPayload,
    InstantlyPushResult,
    LdsLeadRow,
)

if TYPE_CHECKING:
    from supabase import Client as SupabaseClient


logger = logging.getLogger(__name__)


INSTANTLY_BASE_URL = "https://api.instantly.ai/api/v2"
INSTANTLY_LEADS_ADD_PATH = "/leads/add"
# Instantly v2 hard cap. Documented at https://developer.instantly.ai/api/v2.
# We batch below this for safer error recovery (one 1000-lead failure
# loses 10x the work of one 100-lead failure).
INSTANTLY_BULK_HARD_LIMIT = 1000
DEFAULT_BATCH_SIZE = 100
DEFAULT_TIMEOUT_SECONDS = 30


class InstantlyDispatcher(EmailDispatcher):
    """Cold-outreach dispatcher backed by Instantly's v2 bulk API.

    Constructor is the canonical injection point — pass ``api_key`` and
    ``default_campaign_id`` explicitly, or rely on env defaults
    (``INSTANTLY_API_KEY``, ``INSTANTLY_DEFAULT_CAMPAIGN_ID``). At least
    one of the two campaign sources MUST resolve to a non-empty string
    OR every ``push_leads`` call must pass ``campaign_id=`` explicitly;
    otherwise the dispatcher raises ``ValueError`` at push time.

    ``dry_run=True`` builds + validates payloads, logs the would-have-
    sent batch summary, and returns a ``dry_run=True`` ``InstantlyPushResult``
    WITHOUT (a) touching the Instantly API and (b) writing to
    ``email_send_ledger``. Use this for Phase 18 review-before-send
    flows where the operator wants to inspect the resolved payload
    without burning sandbox quota OR creating a misleading ledger row.

    AUP invariant: ``DISPATCH_TYPE = 'cold'`` is asserted in
    ``__init__``. The DispatcherRouter (Phase 14.4) reads this attribute
    when routing per-message dispatch_type → dispatcher.
    """

    PROVIDER_NAME: ClassVar[str] = "instantly"
    SUPPORTS_WEBHOOKS: ClassVar[bool] = True
    SUPPORTS_IDEMPOTENCY: ClassVar[bool] = True
    DISPATCH_TYPE: ClassVar[str] = "cold"

    def __init__(
        self,
        api_key: Optional[str] = None,
        default_campaign_id: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        batch_size: int = DEFAULT_BATCH_SIZE,
        dry_run: bool = False,
        session: Optional[aiohttp.ClientSession] = None,
        db: Optional["SupabaseClient"] = None,
    ) -> None:
        assert self.DISPATCH_TYPE == "cold", "Instantly is cold-only (Resend AUP)"

        self.api_key = api_key or os.environ.get("INSTANTLY_API_KEY", "")
        self.default_campaign_id = (
            default_campaign_id
            or os.environ.get("INSTANTLY_DEFAULT_CAMPAIGN_ID", "")
            or None
        )
        self.timeout = timeout
        if not (1 <= batch_size <= INSTANTLY_BULK_HARD_LIMIT):
            raise ValueError(
                f"batch_size must be 1..{INSTANTLY_BULK_HARD_LIMIT}, got {batch_size}"
            )
        self.batch_size = batch_size
        self.dry_run = dry_run
        self._injected_session = session
        self._db = db

    async def aclose(self) -> None:
        """Close the injected aiohttp session if we own it.

        The default ``push_leads`` path constructs a per-call session
        via ``async with aiohttp.ClientSession(...)`` and tears it down
        automatically, so this method is a no-op for the default case.
        Callers that pass ``session=`` explicitly own the close.
        """
        if self._injected_session is not None and not self._injected_session.closed:
            await self._injected_session.close()

    async def send(
        self,
        to: str,
        subject: str,
        body: str,
        from_name: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> dict[str, Any]:
        """Not supported on Instantly — use ``push_leads`` instead.

        Instantly's model is campaign-centric (push leads, the
        campaign owns subject + body templates). The
        ``EmailDispatcher.send`` per-message API doesn't fit.
        Implementing it would force a synthetic single-lead campaign
        per send, which is wasteful and confusing.

        Raises ``NotImplementedError`` so the DispatcherRouter (Phase
        14.4) can fail fast if a caller mis-routes a single-shot
        ``send()`` to the cold dispatcher.
        """
        raise NotImplementedError(
            "InstantlyDispatcher.send() is not supported; use push_leads() "
            "instead. Cold outreach is campaign-driven on Instantly's side."
        )

    async def push_leads(
        self,
        leads: list[LdsLeadRow],
        campaign_id: Optional[str] = None,
        personalizations: Optional[dict[str, str]] = None,
        message_ids: Optional[dict[str, str]] = None,
    ) -> InstantlyPushResult:
        """Push 1..N LDS leads to an Instantly campaign in batches.

        ``personalizations`` is keyed on ``leads[i]['unique_key']`` — the
        AI-personalization layer (Phase 15) writes its output here; for
        the bare bulk-add path it can be omitted, in which case
        Instantly will fall back to its campaign-level template.

        ``message_ids`` (Phase 14.3) maps ``unique_key → campaign_messages
        .id`` (stringified UUID). When provided, every lead's payload
        carries ``custom_variables.lds_message_id`` so the email_sent
        webhook can round-trip ``provider_message_id`` back to the exact
        originating row. Without this arg the webhook handler's
        ``mark_sent`` path is a no-op (matches the lds_message_id-absent
        contract). Callers in the dispatch loop (Phase 15) must
        pre-create the campaign_messages row with status='pending'
        BEFORE calling push_leads so the lookup ID exists.

        Suppression precheck is a single batch SELECT against
        ``suppressions`` filtered to (identifier_type='email', channel ∈
        {email, all}) — one DB round-trip regardless of batch size.
        Suppressed addresses are skipped silently — they do NOT count
        against ``failed_count``.

        Ledger writes happen ONLY on confirmed-success rows from
        Instantly (i.e. ``success_count``), and ONLY when
        ``dry_run=False``.
        """
        target_campaign = campaign_id or self.default_campaign_id
        if target_campaign is None:
            raise ValueError(
                "campaign_id required: pass explicitly OR set "
                "INSTANTLY_DEFAULT_CAMPAIGN_ID env var OR construct with "
                "default_campaign_id="
            )
        if not self.api_key:
            raise ValueError(
                "INSTANTLY_API_KEY not configured (env or constructor)"
            )
        if not leads:
            return InstantlyPushResult(
                success_count=0, skipped_suppressed=0, failed_count=0,
                dry_run=self.dry_run,
            )

        suppressed = await self._fetch_suppressed_emails(
            [lead.get("email") for lead in leads if lead.get("email")]
        )

        # Build payloads, skipping suppressed emails. We surface the
        # skipped count in the result so the orchestrator can decide
        # whether to log + re-queue or warn the operator.
        payloads: list[InstantlyLeadPayload] = []
        skipped_count = 0
        dispatched_at = _utc_iso_now()
        for lead in leads:
            email = lead.get("email")
            if not email:
                continue
            if email.lower() in suppressed:
                skipped_count += 1
                continue
            uk = lead.get("unique_key") or ""
            payloads.append(
                InstantlyLeadPayload.from_lds_lead(
                    lead,
                    personalization=(personalizations or {}).get(uk),
                    dispatched_at=dispatched_at,
                    lds_message_id=(message_ids or {}).get(uk),
                )
            )

        if self.dry_run:
            logger.info(
                "InstantlyDispatcher.dry_run: would push %d leads to campaign %s (skipped %d suppressed)",
                len(payloads), target_campaign, skipped_count,
                extra={"campaign_id": target_campaign, "skipped": skipped_count},
            )
            return InstantlyPushResult(
                success_count=len(payloads),
                skipped_suppressed=skipped_count,
                failed_count=0,
                dry_run=True,
            )

        success = 0
        failures: list[InstantlyError] = []
        last_raw: dict[str, Any] = {}

        async with self._session_context() as session:
            for batch_start in range(0, len(payloads), self.batch_size):
                batch = payloads[batch_start : batch_start + self.batch_size]
                batch_success, batch_errors, raw = await self._post_batch(
                    session, target_campaign, batch
                )
                success += batch_success
                failures.extend(batch_errors)
                last_raw = raw or last_raw
                if batch_success:
                    await self._record_ledger_writes(
                        [p.email for p in batch[:batch_success]]
                    )

        return InstantlyPushResult(
            success_count=success,
            skipped_suppressed=skipped_count,
            failed_count=len(failures),
            errors=failures,
            raw_response=last_raw,
            dry_run=False,
        )

    # --- internals ---------------------------------------------------------

    async def _fetch_suppressed_emails(self, emails: list[str]) -> set[str]:
        """Single-query batch precheck against the generic suppressions table.

        Phase 14.2 renamed email_suppression → suppressions and extended
        identifier_type to {email, domain, linkedin_url, phone}. The email
        dispatcher only filters on email-typed rows whose channel allows
        email sends (i.e. channel ∈ {email, all}). The partial index
        idx_suppressions_lookup matches this predicate exactly.
        """
        if not self._db or not emails:
            return set()
        try:
            rows = (
                self._db.table("suppressions")
                .select("identifier_value")
                .eq("identifier_type", "email")
                .in_("channel", ["email", "all"])
                .in_("identifier_value", emails)
                .execute()
            )
            return {(r.get("identifier_value") or "").lower() for r in (rows.data or [])}
        except Exception:
            logger.exception("InstantlyDispatcher: suppression precheck failed")
            # Fail-OPEN intentionally: a transient PostgREST blip should
            # not block dispatch. Sentry surfaces the failure; the worst
            # case is one extra send to a recipient that should have
            # been suppressed (Resend webhook will re-suppress on next
            # bounce).
            return set()

    async def _record_ledger_writes(self, emails: list[str]) -> None:
        if not self._db or not emails:
            return
        rows = [
            {
                "recipient_domain": email.rsplit("@", 1)[-1].lower() if "@" in email else None,
                "provider": self.PROVIDER_NAME,
            }
            for email in emails
        ]
        try:
            self._db.table("email_send_ledger").insert(rows).execute()
        except Exception:
            logger.exception(
                "InstantlyDispatcher: ledger insert failed (rows=%d)", len(rows)
            )

    def _session_context(self) -> Any:
        if self._injected_session is not None:
            return _AsyncSessionPassthrough(self._injected_session)
        return aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.timeout)
        )

    async def _post_batch(
        self,
        session: aiohttp.ClientSession,
        campaign_id: str,
        batch: list[InstantlyLeadPayload],
    ) -> tuple[int, list[InstantlyError], dict[str, Any]]:
        payload = {
            "campaign_id": campaign_id,
            "leads": [p.model_dump(mode="json", exclude_none=True) for p in batch],
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "LeadDataScraper/1.0",
        }
        url = f"{INSTANTLY_BASE_URL}{INSTANTLY_LEADS_ADD_PATH}"
        try:
            async with session.post(url, json=payload, headers=headers) as resp:
                body = await _safe_json(resp)
                if resp.status == 401:
                    return 0, [
                        InstantlyError(
                            email=p.email,
                            error_code="auth",
                            error_message="Instantly auth failed (401)",
                        ) for p in batch
                    ], body
                if resp.status == 429:
                    return 0, [
                        InstantlyError(
                            email=p.email,
                            error_code="rate_limit",
                            error_message="Instantly rate-limited (429)",
                        ) for p in batch
                    ], body
                if resp.status >= 400:
                    return 0, [
                        InstantlyError(
                            email=p.email,
                            error_code=f"http_{resp.status}",
                            error_message=str(body.get("message") or body)[:512],
                        ) for p in batch
                    ], body

                # 2xx — Instantly returns
                #   {"success": <int>, "errors": [{email, code, message}, ...]}
                # (per v2 docs). Treat missing keys as 0/[].
                success_count = int(body.get("success", len(batch)) or 0)
                errors = [
                    InstantlyError(
                        email=str(e.get("email", "")),
                        error_code=str(e.get("code", "unknown")),
                        error_message=str(e.get("message", ""))[:512],
                    )
                    for e in (body.get("errors") or [])
                ]
                return success_count, errors, body
        except aiohttp.ClientError as exc:
            logger.exception("InstantlyDispatcher: HTTP transport error")
            return 0, [
                InstantlyError(
                    email=p.email,
                    error_code="transport",
                    error_message=str(exc)[:512],
                ) for p in batch
            ], {}


# --- helpers --------------------------------------------------------------


def _utc_iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


async def _safe_json(resp: aiohttp.ClientResponse) -> dict[str, Any]:
    try:
        return await resp.json(content_type=None)  # type: ignore[no-any-return]
    except (aiohttp.ContentTypeError, ValueError):
        return {"message": (await resp.text())[:512]}


class _AsyncSessionPassthrough:
    """Bridge an injected aiohttp.ClientSession into `async with`.

    The default code path uses ``async with aiohttp.ClientSession(...)``
    which builds + tears down the session. Injected sessions (tests,
    operator-supplied pool) must NOT be torn down; this wrapper makes
    ``async with`` a no-op enter/exit while still yielding the session.
    """

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def __aenter__(self) -> aiohttp.ClientSession:
        return self._session

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None
