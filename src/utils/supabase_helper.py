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
        key: str = os.environ.get("SUPABASE_ANON_KEY")
        if not url or not key:
            logger.warning("SUPABASE_URL or SUPABASE_ANON_KEY not found in environment.")
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
            logger.info("Successfully upserted %d leads to Supabase.", len(leads))
            return result
        except Exception as e:
            if "column" in str(e) and "does not exist" in str(e):
                logger.error("DATABASE SCHEMA MISMATCH: %s", e)
                logger.warning("Please run the SQL migration script provided in the implementation plan.")
            else:
                logger.error("Error upserting leads: %s", e, exc_info=True)
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

    def update_audit(self, unique_key: str, audit_data: dict):
        """
        Updates the audit results for a specific lead.
        """
        if not self.client:
            return None

        update_data = {
            "audit_status": "Completed",
            "audit_results": audit_data
        }

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
            return self.client.table("leads").update(update_data).eq("unique_key", unique_key).execute()
        except Exception as e:
            logger.error("Error updating audit for %s: %s", unique_key, e, exc_info=True)
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
        """
        if not self.client:
            return None
        # In Supabase/PostgREST, we need a filter to delete.
        # Since we want all, we can filter for anything not null.
        return self.client.table("leads").delete().neq("unique_key", "null").execute()

    def delete_all_jobs(self):
        """
        Deletes all rows from the 'orchestration_jobs' table.
        """
        if not self.client:
            return None
        return self.client.table("orchestration_jobs").delete().neq("id", "null").execute()

    def check_schema(self):
        """
        Polls the database to check if all necessary columns exist in the 'leads' table.
        Returns a list of missing columns.
        """
        if not self.client:
            return []

        required_cols = [
            "enrichment_status", "high_risk_flag", "seo_score", "company_size",
            "leadership_team", "key_offerings", "contact_details", "business_details",
            "target_clients", "pain_points", "facebook", "instagram", "linkedin",
            "outreach_score", "phone", "segment", "linkedin_hook", "email_hook",
            "tiktok", "pinterest", "first_name", "company_name", "priority_link", "needs_manual_review"
        ]

        try:
            # Fetch one lead (or empty result) to get columns
            response = self.client.table("leads").select("*").limit(1).execute()
            if not response.data:
                pass

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
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", str(col)):
                valid_columns.append(col)
            else:
                logger.warning("Auto-migration: Skipping invalid column name '%s' to prevent SQL injection", col)

        if not valid_columns:
            logger.warning("Auto-migration: No valid columns to migrate.")
            return False

        try:
            # Try using rpc to run ALTER TABLE (requires a Supabase SQL function)
            sql = "ALTER TABLE leads " + ", ".join(
                [f"ADD COLUMN IF NOT EXISTS {col} TEXT" for col in valid_columns]
            ) + ";"
            self.client.rpc("exec_sql", {"query": sql}).execute()
            logger.info("Auto-migration: Added columns %s", valid_columns)
            return True
        except Exception as e:
            logger.warning("RPC migration failed (exec_sql function may not exist): %s", e)
            return False
