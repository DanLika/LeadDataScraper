"""Campaign business logic.

Each method takes typed primitives (not Pydantic instances) so non-HTTP
callers (CLI scripts, background tasks, future cron jobs) can construct
arguments without depending on `backend.main`'s request models.

The service is the thin layer between HTTP handlers and DB access. It
orchestrates repository calls, applies any cross-table business rules,
and raises domain exceptions (see `src.services.exceptions`) when the
business invariants are violated. It does NOT touch FastAPI request /
response shapes — the handler maps domain exceptions to HTTP.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import Any

import pandas as pd

from src.repositories.campaign_repository import CampaignRepository
from src.services.exceptions import (
    CampaignNotFoundError,
    NoCampaignMessagesError,
    NoMatchingLeadsError,
)
from src.utils.csv_helper import sanitize_dataframe_for_csv


class CampaignService:
    """Orchestrator over `CampaignRepository`. Stateless per-request."""

    def __init__(self, repo: CampaignRepository) -> None:
        self._repo = repo

    # ---- CRUD --------------------------------------------------------

    def create(self, *, name: str, channel: str, segment_filter: str | None) -> dict[str, Any]:
        """Create a draft campaign. The server-side defaults
        (id / status / counters) live here, NOT in the handler — so a
        CLI bulk-create can produce identical rows."""
        campaign_data = {
            "id": str(uuid.uuid4()),
            "name": name,
            "channel": channel,
            "segment_filter": segment_filter,
            "status": "draft",
            "total_leads": 0,
            "sent_count": 0,
            "reply_count": 0,
        }
        return self._repo.insert(campaign_data)

    def list_all(self) -> list[dict[str, Any]]:
        return self._repo.list_all()

    def get_with_stats(self, campaign_id: str) -> dict[str, Any]:
        """Return campaign + first 50 messages + per-status counts.

        Raises `CampaignNotFoundError` if the id is unknown — handler
        translates to 404. The 50-message cap matches the frontend's
        rendered window; the per-status counts come from
        `count_messages_by_status` (see repo docstring on the 5-query
        shape).
        """
        campaign = self._repo.get_by_id(campaign_id)
        if campaign is None:
            raise CampaignNotFoundError(campaign_id)
        stats = self._repo.count_messages_by_status(campaign_id)
        messages = self._repo.list_messages(campaign_id, limit=50)
        return {
            "campaign": campaign,
            "messages": messages,
            "stats": stats,
            "total_messages": sum(stats.values()),
        }

    def set_status(self, campaign_id: str, status: str) -> None:
        """Update only the status field. Used by start / pause / archive
        flows. The caller is responsible for passing a valid
        CampaignStatus value (handler-side Literal validates)."""
        self._repo.update(campaign_id, {"status": status})

    # ---- message generation ------------------------------------------

    def generate_messages(self, campaign_id: str) -> int:
        """Generate per-lead outreach messages and bulk-insert.

        Returns the count of LEADS that contributed messages (a multi
        campaign produces 1 email + 1 linkedin row per lead but the
        return value is the per-lead count, matching the previous
        handler's response shape).

        Raises:
            CampaignNotFoundError: id is unknown
            NoMatchingLeadsError: the channel + segment filter selected
                zero leads — the operator should adjust filters

        DESIGN NOTE — sync method on purpose. The body is pure
        PostgREST (sync supabase-py) + dict-building. The handler runs
        this under `asyncio.to_thread` so the event loop is not blocked
        for the duration of the generate pass. The previous implementation
        defined an `async def generate_messages()` that awaited zero
        actual coroutines — it just blocked the event loop with sync
        PostgREST calls. Moving to `to_thread` here matches the pattern
        SupabaseHelper already follows for its hot-path reads (see
        `CLAUDE.md` perf invariants).
        """
        campaign = self._repo.get_by_id(campaign_id)
        if campaign is None:
            raise CampaignNotFoundError(campaign_id)

        channel = campaign["channel"]
        segment = campaign.get("segment_filter")
        leads = self._repo.select_leads_for_channel(channel, segment)
        if not leads:
            raise NoMatchingLeadsError(campaign_id)

        messages: list[dict[str, Any]] = []
        for lead in leads:
            messages.extend(self._build_messages_for_lead(campaign_id, channel, lead))

        if messages:
            self._repo.insert_messages(messages)
            self._repo.update(campaign_id, {
                "total_leads": len(leads),
                "status": "draft",
            })
        return len(leads)

    # ---- export ------------------------------------------------------

    def export_messages_to_csv(self, campaign_id: str) -> str:
        """Materialise messages + lead joins to a CSV under `exports/`.
        Returns the absolute path the handler will serve via
        FileResponse.

        Raises:
            NoCampaignMessagesError: campaign has no generated messages
                yet — handler returns 404 with the "No messages found"
                hint that prompts the operator to hit /generate first.

        The CSV is cell-sanitised against formula injection
        (`=`, `@`, `+`, `-` prefixes) via `sanitize_dataframe_for_csv`
        before write — required for any export that includes
        attacker-controllable fields (lead names, pain_points,
        email_hook etc. come from CSV uploads + scrapes).
        """
        rows = self._repo.list_messages_for_export(campaign_id)
        if not rows:
            raise NoCampaignMessagesError(campaign_id)

        df = pd.DataFrame(rows)
        unique_keys: list[str] = df["lead_unique_key"].dropna().unique().tolist()
        lead_rows = self._repo.get_leads_by_keys(unique_keys)
        leads_df = pd.DataFrame(lead_rows) if lead_rows else pd.DataFrame()
        if not leads_df.empty:
            df = df.merge(
                leads_df,
                left_on="lead_unique_key",
                right_on="unique_key",
                how="left",
            )

        os.makedirs("exports", exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        export_path = f"exports/campaign_{campaign_id[:8]}_{ts}.csv"
        sanitize_dataframe_for_csv(df).to_csv(export_path, index=False)
        return export_path

    # ---- private: per-lead message construction ----------------------

    @staticmethod
    def _build_messages_for_lead(
        campaign_id: str,
        channel: str,
        lead: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Return 0..2 messages for one lead based on the campaign channel.

        - `channel == "email"` → 1 email message
        - `channel == "linkedin"` → 1 linkedin message
        - `channel == "multi"` → 1 email AND 1 linkedin message

        The handler's previous inline form did the channel-dispatch in
        the loop body. Lifting to a per-lead helper makes the channel
        branches independently testable.
        """
        out: list[dict[str, Any]] = []
        lead_name = lead.get("name") or lead.get("company_name") or "there"

        if channel in ("email", "multi"):
            out.append(_CampaignMessageBuilder.email(campaign_id, lead, lead_name))
        if channel in ("linkedin", "multi"):
            out.append(_CampaignMessageBuilder.linkedin(campaign_id, lead, lead_name))
        return out


class _CampaignMessageBuilder:
    """Per-channel message body templates. Static methods so the service
    can call without instantiation. Pure-data — no I/O, no Gemini calls
    (the existing handler builds these from cached `email_hook` /
    `linkedin_hook` fields that were generated during the enrichment
    pass).

    The `{{first_name}}` placeholder is preserved verbatim in the body
    so a downstream send pipeline (Instantly / Apollo / a custom SMTP
    integration) can substitute at delivery time. Keep the double-braces
    — single braces would conflict with Python f-strings used elsewhere.
    """

    @staticmethod
    def email(campaign_id: str, lead: dict[str, Any], lead_name: str) -> dict[str, Any]:
        hook: str = lead.get("email_hook") or ""
        company: str = lead.get("company_name") or lead_name
        pain: str = lead.get("pain_points") or ""
        subject = f"Quick question about {company}"
        if hook:
            body = (
                f"Hi {{{{first_name}}}},\n\n"
                f"{hook}\n\n"
                f"I'd love to share a few specific ideas that could help. "
                f"Would you be open to a quick 10-minute chat this week?\n\n"
                f"Best,"
            )
        else:
            body = (
                f"Hi {{{{first_name}}}},\n\n"
                f"I came across {company}'s website and noticed a few areas "
                f"where you might be leaving growth on the table. {pain[:200]}\n\n"
                f"Would you be open to a quick chat about it?\n\n"
                f"Best,"
            )
        return {
            "campaign_id": campaign_id,
            "lead_unique_key": lead["unique_key"],
            "channel": "email",
            "subject": subject,
            "body": body,
            "status": "pending",
        }

    @staticmethod
    def linkedin(campaign_id: str, lead: dict[str, Any], lead_name: str) -> dict[str, Any]:
        hook: str = lead.get("linkedin_hook") or ""
        company: str = lead.get("company_name") or lead_name
        body = hook or (
            f"Hi, I came across {company} and was impressed by what you're "
            f"building. I work in a similar space and would love to connect."
        )
        return {
            "campaign_id": campaign_id,
            "lead_unique_key": lead["unique_key"],
            "channel": "linkedin",
            "subject": None,
            "body": body,
            "status": "pending",
        }
