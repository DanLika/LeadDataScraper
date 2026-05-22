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
                logger.warning("Please run the SQL migration script provided in the implementation plan.")
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
            return self.client.table("leads").update(data).eq("unique_key", unique_key).execute()
        except Exception as e:
            logger.error("Error updating lead info for %s: %s", unique_key, e, exc_info=True)
            return None

    def get_pending_leads(self):
        """
        Retrieves leads that haven't been audited yet.
        """
        if not self.client:
            return []

        return self.client.table("leads").select("*").eq("audit_status", "Pending").execute()

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
        return self.client.table("leads").delete().gte("created_at", "1970-01-01").execute()

    def delete_all_jobs(self):
        """
        Deletes all rows from the 'orchestration_jobs' table.
        Earlier `.neq("id", "null")` threw `invalid input syntax for type
        uuid: "null"` because id is UUID, not text. Match on created_at
        instead — works regardless of column types.
        """
        if not self.client:
            return None
        return self.client.table("orchestration_jobs").delete().gte("created_at", "1970-01-01").execute()

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
            "address", "name", "email", "website", "lead_source", "updated_at",
            "audit_status", "audit_results",
            # Enrichment columns — referenced by /hunt-lead + orchestrator.
            "enrichment_status", "high_risk_flag", "seo_score", "company_size",
            "leadership_team", "key_offerings", "contact_details", "business_details",
            "target_clients", "pain_points", "facebook", "instagram", "linkedin",
            "outreach_score", "phone", "segment", "linkedin_hook", "email_hook",
            "tiktok", "pinterest", "first_name", "company_name", "priority_link", "needs_manual_review"
        ]

        try:
            # Optimization 1: Attempt to select all columns in a single row query
            # This handles the common case where all columns exist with only 1 query.
            try:
                self.client.table("leads").select(",".join(required_cols)).limit(1).execute()
                return []
            except Exception as e:
                # If selection fails, it's likely because one or more columns are missing
                if "column" not in str(e) or "does not exist" not in str(e):
                    # For other types of errors, log and return empty
                    logger.error("Error during bulk schema check: %s", e)
                    return []

            # Optimization 2: Individual checks (no generic exec_sql RPC).
            # The generic exec_sql RPC was removed for security; column-level
            # checks are slower but safe.
            missing = []
            for col in required_cols:
                try:
                    self.client.table("leads").select(col).limit(1).execute()
                except Exception as e:
                    if "column" in str(e) and "does not exist" in str(e):
                        missing.append(col)
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
                logger.warning("Auto-migration: Skipping invalid column name '%s' to prevent SQL injection", col_str)

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
                    col, e,
                )
        return success_any
