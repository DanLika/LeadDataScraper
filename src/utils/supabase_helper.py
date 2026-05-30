import asyncio
import os
import re
from supabase import create_client, Client
from dotenv import load_dotenv
from src.utils.logging_config import get_logger

load_dotenv()

logger = get_logger(__name__)


class SupabaseHelper:
    def __init__(self):
        url: str = os.environ.get("SUPABASE_URL")
        # Backend ops must use the service-role key (intentionally bypasses RLS
        # for server-side reads/writes). Never fall back to the anon key: if
        # service-role goes missing in prod and we silently downgrade, a future
        # RLS relaxation would suddenly let the anon-key client write data.
        # Fail fast at startup instead.
        key: str = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        if not url:
            logger.warning("SUPABASE_URL not found in environment.")
            self.client = None
        elif not key:
            logger.error(
                "SUPABASE_SERVICE_ROLE_KEY missing — backend cannot start. "
                "Anon-key fallback was removed to prevent accidental privilege downgrade."
            )
            self.client = None
        else:
            self.client: Client = create_client(url, key)

    def upsert_leads(self, leads: list):
        """
        Upserts a list of lead dictionaries into the 'leads' table.
        Expects 'unique_key' to be the primary key or unique constraint.
        """
        if not self.client:
            return None

        try:
            result = self.client.table("leads").upsert(leads).execute()
            # Log the count Postgres actually returned, not the input count —
            # callers that report success based on len(leads) instead of
            # len(result.data) silently mask partial rejections (PGRST204,
            # constraint violations, RLS rejects under a different role).
            actual = len(getattr(result, "data", None) or [])
            logger.info("Upserted %d/%d leads to Supabase.", actual, len(leads))
            return result
        except Exception as e:
            if "column" in str(e) and "does not exist" in str(e):
                logger.error("DATABASE SCHEMA MISMATCH: %s", e)
                logger.warning(
                    "Please run the SQL migration script provided in the implementation plan."
                )
            else:
                logger.error("Error upserting leads: %s", e, exc_info=True)
            # Returns None on failure; callers must check the return value and
            # surface the failure rather than assuming success based on input
            # count. See backend.main._upsert_leads_to_db.
            return None

    def update_lead_info(self, unique_key: str, data: dict):
        """
        Updates arbitrary information for a specific lead.
        """
        if not self.client:
            return None

        try:
            return (
                self.client.table("leads")
                .update(data)
                .eq("unique_key", unique_key)
                .execute()
            )
        except Exception as e:
            logger.error(
                "Error updating lead info for %s: %s", unique_key, e, exc_info=True
            )
            return None

    def update_audit(self, unique_key: str, audit_data: dict):
        """
        Updates the audit results for a specific lead.
        """
        if not self.client:
            return None

        update_data = {"audit_status": "Completed", "audit_results": audit_data}

        # Extract intelligence fields if present
        if "emails" in audit_data and audit_data["emails"]:
            update_data["email"] = audit_data["emails"][0]

        if "score" in audit_data:
            try:
                update_data["seo_score"] = float(audit_data["score"])
            except (ValueError, TypeError):
                update_data["seo_score"] = 0

        if "high_risk_flag" in audit_data:
            update_data["high_risk_flag"] = bool(audit_data["high_risk_flag"])

        try:
            return (
                self.client.table("leads")
                .update(update_data)
                .eq("unique_key", unique_key)
                .execute()
            )
        except Exception as e:
            logger.error(
                "Error updating audit for %s: %s", unique_key, e, exc_info=True
            )
            return None

    # ---- async wrappers for hot read paths -----------------------------
    # supabase-py 2.x ships sync PostgREST calls (httpx-sync underneath).
    # Calling them directly from FastAPI async handlers blocks the uvicorn
    # event loop, so a single worker can only serve as many concurrent
    # requests as the wait time / 1-thread-cpu budget allows — effective
    # ceiling = worker count, not connection pool size. asyncio.to_thread
    # hands the call to the default executor (capped ~32 threads); the
    # loop stays free to accept and dispatch other requests. Locust
    # Scenario A throughput grew ~3-5x on the same `uvicorn --workers N`
    # after this change; p95 dropped from queue-bound to DB-bound.
    #
    # Only the hot READ paths get wrappers here. Background-task code
    # (TaskOrchestrator._process_in_chunks loop) keeps the direct sync
    # calls — it already runs off the request loop and the to_thread
    # hop would just add scheduling overhead.

    async def list_leads_recent(
        self,
        limit: int = 50,
        cursor: "dict | None" = None,
        include_demo: bool = False,
    ):
        """Async wrapper for the /leads handler.

        Keyset (cursor) pagination on (created_at DESC, unique_key DESC).
        When `cursor` is None, returns the first page. Otherwise, returns
        rows strictly after the cursor boundary.

        Cursor shape is `{"c": "<iso created_at>", "k": "<unique_key>"}` —
        the request handler is responsible for safely decoding the opaque
        client token into this shape before calling.

        Tie-break filter: `created_at < cursor.c` OR (`created_at == c`
        AND `unique_key < k`). Expressed in PostgREST `or_` syntax with
        the inner conjunction nested via `and(...)`. Both string values
        are interpolated directly — they're either pristine ISO + the
        unique_key the API just emitted, or already-validated cursor
        contents. constraint regex in the cursor decoder bounds length.

        `include_demo` defaults False — Phase 13.3 demo seed rows
        (`is_demo=true`) are hidden from the dashboard unless the
        operator's "Show demo data" toggle is on. Real producers (CSV
        upload, Google-Maps scrape) write `is_demo=false` so the filter
        is a no-op for them.
        """

        def _query():
            q = self.client.table("leads").select("*")
            if not include_demo:
                q = q.eq("is_demo", False)
            if (
                cursor
                and isinstance(cursor.get("c"), str)
                and isinstance(cursor.get("k"), str)
            ):
                c = cursor["c"]
                k = cursor["k"]
                # PostgREST `or` syntax: f1,f2 — top level is OR. Nested
                # `and(...)` lets us express the tie-break atomically.
                q = q.or_(f"created_at.lt.{c},and(created_at.eq.{c},unique_key.lt.{k})")
            q = (
                q.order("created_at", desc=True)
                .order("unique_key", desc=True)
                .limit(limit)
            )
            return q.execute()

        response = await asyncio.to_thread(_query)
        return response.data

    async def get_stats_rows(self, include_demo: bool = False):
        """Async wrapper for the /stats handler. Returns the narrow column
        set the chart aggregation needs — full rows would inflate the
        pandas DataFrame and dominate the thread-hop savings.

        `include_demo` defaults False — Phase 13.3 demo seed rows are
        excluded from the dashboard's stats by default so demo data
        doesn't inflate the operator's real-lead counts.
        """

        def _query():
            q = self.client.table("leads").select(
                "audit_status", "audit_results", "seo_score", "lead_source"
            )
            if not include_demo:
                q = q.eq("is_demo", False)
            return q.execute()

        response = await asyncio.to_thread(_query)
        return response.data

    def delete_demo_leads(self) -> dict:
        """Hard-delete every `is_demo=true` lead and any campaign_messages
        that reference them. Returns row counts for the operator-facing
        toast.

        Order matters: `campaign_messages.lead_unique_key` is a FK with
        no ON DELETE clause (defaults to NO ACTION), so a row tied to a
        demo lead would block the parent DELETE. Wipe messages first,
        then leads.

        The operator may have generated outreach drafts against a demo
        lead during a screenshot session; deleting those messages with
        the lead is the expected behaviour.
        """
        if not self.client:
            return {"leads_deleted": 0, "messages_deleted": 0}

        demo_keys_resp = (
            self.client.table("leads")
            .select("unique_key")
            .eq("is_demo", True)
            .execute()
        )
        demo_keys = [
            row["unique_key"]
            for row in (demo_keys_resp.data or [])
            if row.get("unique_key")
        ]

        messages_deleted = 0
        if demo_keys:
            msg_resp = (
                self.client.table("campaign_messages")
                .delete()
                .in_("lead_unique_key", demo_keys)
                .execute()
            )
            messages_deleted = len(msg_resp.data or [])

        leads_resp = self.client.table("leads").delete().eq("is_demo", True).execute()
        leads_deleted = len(leads_resp.data or [])
        return {"leads_deleted": leads_deleted, "messages_deleted": messages_deleted}

    async def find_running_job(self):
        """Async wrapper for orchestrator's resume-check on /process-lead /
        /process-all path. Returns the response.data list (0 or 1 row)."""

        def _query():
            return (
                self.client.table("orchestration_jobs")
                .select("*")
                .eq("status", "running")
                .limit(1)
                .execute()
            )

        response = await asyncio.to_thread(_query)
        return response.data

    async def insert_orchestration_job(self, job_data: dict):
        """Async wrapper used by run_massive_pipeline before dispatching the
        background task. Returns the inserted-row response.data."""

        def _query():
            return self.client.table("orchestration_jobs").insert(job_data).execute()

        response = await asyncio.to_thread(_query)
        return response.data

    def get_pending_leads(self):
        """
        Retrieves leads that haven't been audited yet.
        """
        if not self.client:
            return []

        return (
            self.client.table("leads")
            .select("*")
            .eq("audit_status", "Pending")
            .execute()
        )

    def delete_all_leads(self):
        """
        Deletes all rows from the 'leads' table.
        PostgREST requires a WHERE clause for safety. We use a tautology
        on created_at (any non-epoch row matches) which works for any column
        type — earlier `.neq("unique_key", "null")` accidentally worked
        because unique_key is text. Kept generic to mirror jobs deletion.
        """
        if not self.client:
            return None
        return (
            self.client.table("leads")
            .delete()
            .gte("created_at", "1970-01-01")
            .execute()
        )

    def delete_all_jobs(self):
        """
        Deletes all rows from the 'orchestration_jobs' table.
        Earlier `.neq("id", "null")` threw `invalid input syntax for type
        uuid: "null"` because id is UUID, not text. Match on created_at
        instead — works regardless of column types.
        """
        if not self.client:
            return None
        return (
            self.client.table("orchestration_jobs")
            .delete()
            .gte("created_at", "1970-01-01")
            .execute()
        )

    def check_schema(self):
        """
        Polls the database to check if all necessary columns exist in the 'leads' table.
        Returns a list of missing columns.
        """
        if not self.client:
            return []

        # Includes both core columns (written by /upload, /process-lead) and
        # enrichment columns (written by /hunt-lead, /process-all). The bug
        # this catches: if any of these columns are missing in the live
        # database, the boot-time auto-migration runs before the first user
        # request hits a broken codepath. Previously this list only checked
        # enrichment fields, so a missing `address` / `updated_at` / etc. was
        # invisible at boot and only surfaced on the next user upload.
        required_cols = [
            # Core ingest columns — referenced by /upload + the CSV pipeline.
            "address",
            "name",
            "email",
            "website",
            "lead_source",
            "updated_at",
            "audit_status",
            "audit_results",
            # Enrichment columns — referenced by /hunt-lead + orchestrator.
            "enrichment_status",
            "high_risk_flag",
            "seo_score",
            "company_size",
            "leadership_team",
            "key_offerings",
            "contact_details",
            "business_details",
            "target_clients",
            "pain_points",
            "facebook",
            "instagram",
            "linkedin",
            "outreach_score",
            "phone",
            "segment",
            "linkedin_hook",
            "email_hook",
            "tiktok",
            "pinterest",
            "first_name",
            "company_name",
            "priority_link",
            "needs_manual_review",
        ]

        cols_to_check = list(required_cols)
        missing = []

        try:
            while cols_to_check:
                try:
                    self.client.table("leads").select(",".join(cols_to_check)).limit(1).execute()
                    break # All remaining columns exist
                except Exception as e:
                    error_str = str(e)
                    if "column" in error_str and "does not exist" in error_str:
                        # Optimization: Extract the missing column from the error message to avoid N+1 queries
                        match = re.search(r'column \\?"?([a-zA-Z0-9_]+)\\?"? does not exist', error_str)
                        if match:
                            missing_col = match.group(1)
                            if missing_col in cols_to_check:
                                missing.append(missing_col)
                                cols_to_check.remove(missing_col)
                                continue # Retry with the missing column removed

                        # Fallback to individual checks if parsing fails
                        for col in cols_to_check:
                            try:
                                self.client.table("leads").select(col).limit(1).execute()
                            except Exception as inner_e:
                                if "column" in str(inner_e) and "does not exist" in str(inner_e):
                                    missing.append(col)
                        break # We've checked all remaining individually
                    else:
                        logger.error("Error during bulk schema check: %s", e)
                        break

            return missing
        except Exception as e:
            logger.error("Error checking schema: %s", e, exc_info=True)
            return []

    def auto_migrate(self, missing_columns: list) -> bool:
        """
        Attempts to add missing columns via Supabase RPC (requires a migration function)
        or falls back to inserting a dummy row with the columns to trigger schema creation.
        """
        if not self.client or not missing_columns:
            return False

        # Validate column names to prevent SQL injection
        valid_columns = []
        for col in missing_columns:
            col_str = str(col)
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\Z", col_str):
                valid_columns.append(col_str)
            else:
                logger.warning(
                    "Auto-migration: Skipping invalid column name '%s' to prevent SQL injection",
                    col_str,
                )

        if not valid_columns:
            logger.warning("Auto-migration: No valid columns to migrate.")
            return False

        # Call the narrow `add_lead_column(text)` RPC once per column. This RPC
        # validates the column name and only touches `public.leads`, replacing
        # the unsafe generic `exec_sql` function.
        success_any = False
        for col in valid_columns:
            try:
                self.client.rpc("add_lead_column", {"col": col}).execute()
                logger.info("Auto-migration: Added column %s", col)
                success_any = True
            except Exception as e:
                logger.warning(
                    "Auto-migration: add_lead_column(%s) failed (the RPC may not "
                    "exist yet — run the latest supabase_schema.sql): %s",
                    col,
                    e,
                )
        return success_any
