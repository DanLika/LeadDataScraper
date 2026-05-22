"""Repository for the `campaigns` + `campaign_messages` tables.

Pure DB access — no business logic, no HTTP awareness. Every method
takes already-validated arguments and returns raw dict / list / None
(matching supabase-py's PostgREST shapes). PGRST205 (schema cache /
table missing) is the one error class translated at this layer because
the operator-action ("run the migration SQL") is the same regardless
of which method tripped it; all other postgrest errors bubble up.

Some methods reach into the `leads` table for campaign-scoped lead
selection (e.g. `select_leads_for_channel`). When a future `LeadRepository`
migration extracts the lead domain, those methods should move there —
they live here today because they're only called from `CampaignService`
and pulling them out without a consumer would be premature.
"""
from __future__ import annotations

from typing import Any

from postgrest.exceptions import APIError

from src.services.exceptions import CampaignTableMissingError


# PostgREST schema-cache error code: "Could not find the table 'X' in
# the schema cache" — fires when the table doesn't exist. Translate
# uniformly because the operator's response is always the same.
_PGRST_TABLE_MISSING = "PGRST205"


def _translate_table_missing(exc: APIError) -> None:
    """Re-raise as CampaignTableMissingError when the underlying PostgREST
    error is PGRST205. Otherwise lets the original APIError bubble."""
    if _PGRST_TABLE_MISSING in str(exc):
        raise CampaignTableMissingError(str(exc)) from exc


class CampaignRepository:
    """DB access for the campaign domain.

    `client` is a supabase-py PostgREST client (whatever
    `SupabaseHelper.client` exposes). The repo never holds long-lived
    state — instantiate per-request from the handler.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    # ---- campaigns ---------------------------------------------------

    def insert(self, data: dict[str, Any]) -> dict[str, Any]:
        """Insert a campaign row; return the inserted row.

        Falls back to the input `data` dict if the server returns no
        rows (some PostgREST configs omit RETURNING by default — the
        original handler's `result.data[0] if result.data else campaign_data`
        accepted both shapes).
        """
        try:
            result = self._client.table("campaigns").insert(data).execute()
        except APIError as exc:
            _translate_table_missing(exc)
            raise
        return result.data[0] if result.data else data

    def list_all(self) -> list[dict[str, Any]]:
        """Return every campaign, newest first."""
        try:
            result = self._client.table("campaigns").select("*").order(
                "created_at", desc=True
            ).execute()
        except APIError as exc:
            _translate_table_missing(exc)
            raise
        return result.data or []

    def get_by_id(self, campaign_id: str) -> dict[str, Any] | None:
        """Return one campaign or None. Uses `maybe_single()` so a 0-row
        lookup yields `data=None` instead of raising APIError(PGRST116)
        — lets the caller decide 404 vs other handling without parsing
        an exception message."""
        try:
            result = self._client.table("campaigns").select("*").eq(
                "id", campaign_id
            ).maybe_single().execute()
        except APIError as exc:
            _translate_table_missing(exc)
            raise
        if not result or not result.data:
            return None
        data: dict[str, Any] = result.data
        return data

    def update(self, campaign_id: str, fields: dict[str, Any]) -> None:
        """Apply a partial update. No-op if `fields` is empty."""
        if not fields:
            return
        self._client.table("campaigns").update(fields).eq("id", campaign_id).execute()

    # ---- campaign_messages -------------------------------------------

    def insert_messages(self, messages: list[dict[str, Any]]) -> None:
        """Bulk-insert generated messages. No-op on empty list."""
        if not messages:
            return
        self._client.table("campaign_messages").insert(messages).execute()

    def list_messages(self, campaign_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """First `limit` messages for `campaign_id`. The frontend caps
        the display at 50 — keep the limit bounded server-side so the
        response stays small."""
        result = self._client.table("campaign_messages").select("*").eq(
            "campaign_id", campaign_id
        ).limit(limit).execute()
        return result.data or []

    def list_messages_for_export(self, campaign_id: str) -> list[dict[str, Any]]:
        """All messages for export. Selects only the columns the CSV
        export needs to keep the row size minimal — the operator's CSV
        downloads should not include internal fields."""
        result = self._client.table("campaign_messages").select(
            "lead_unique_key, channel, subject, body, status"
        ).eq("campaign_id", campaign_id).execute()
        return result.data or []

    def count_messages_by_status(self, campaign_id: str) -> dict[str, int]:
        """Counts per status, computed via per-status COUNT queries
        rather than fetching all rows.

        DESIGN NOTE — preserve this 5-query shape. Each
        `select('id', count='exact').limit(1)` is a COUNT(*)-equivalent
        on the server; cheap for an indexed `(campaign_id, status)` query.
        The alternative — fetching every row and counting in Python —
        was tried before and degrades to a fetch-of-hundreds-of-thousands
        for a busy campaign. A single GROUP BY would be more elegant
        but requires either an RPC or a view, neither of which the
        schema currently ships.
        """
        stats = {"pending": 0, "sent": 0, "delivered": 0, "replied": 0, "bounced": 0}
        for status in stats.keys():
            res = self._client.table("campaign_messages").select(
                "id", count="exact"
            ).eq("campaign_id", campaign_id).eq("status", status).limit(1).execute()
            stats[status] = res.count or 0
        return stats

    # ---- leads (campaign-scoped, see module docstring) ---------------

    def select_leads_for_channel(
        self,
        channel: str,
        segment_filter: str | None,
    ) -> list[dict[str, Any]]:
        """Leads that match the campaign's `segment_filter` AND have the
        contact field the channel needs.

        - `channel == "email"` → leads with non-null `email`
        - `channel == "linkedin"` → leads with non-null `linkedin`
        - `channel == "multi"` → no contact-field filter (leads may
          contribute to either or both channels in
          `CampaignService._build_messages_for_lead`)
        """
        query = self._client.table("leads").select("*")
        if segment_filter:
            query = query.eq("segment", segment_filter)
        if channel == "email":
            query = query.not_.is_("email", "null")
        elif channel == "linkedin":
            query = query.not_.is_("linkedin", "null")
        result = query.execute()
        return result.data or []

    def get_leads_by_keys(self, unique_keys: list[str]) -> list[dict[str, Any]]:
        """Subset selection of the lead identity / contact columns for
        the export-merge path. Returns empty list on empty input so the
        caller can `pd.DataFrame(...)` without a special-case."""
        if not unique_keys:
            return []
        result = self._client.table("leads").select(
            "unique_key, name, email, linkedin, company_name, first_name"
        ).in_("unique_key", unique_keys).execute()
        return result.data or []
