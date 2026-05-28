import os
import pandas as pd
from datetime import datetime
from src.utils.supabase_helper import SupabaseHelper
from src.utils.csv_helper import sanitize_dataframe_for_csv


def check_vulnerability(row):
    audit = row.get("audit_results") or {}
    # missing_title, missing_description, no_h1, ssl_valid
    no_ssl = audit.get("ssl_valid") is False
    no_h1 = audit.get("no_h1") is True
    return no_ssl or no_h1


def is_high_priority(row):
    audit = row.get("audit_results") or {}
    score = audit.get("score", 100)
    try:
        score_val = float(score)
    except Exception:
        score_val = 100
    return score_val < 50


def is_outreach_ready(row):
    has_contact = bool(row.get("email")) or bool(row.get("phone"))
    score = row.get("outreach_score") or 0
    try:
        score = float(score)
    except (TypeError, ValueError):
        score = 0
    return has_contact and score > 30


def extract_names(row):
    leader = row.get("leadership_team", "Unknown")
    if leader and leader != "Unknown":
        # Take the first name in the string
        parts = leader.replace(",", " ").split()
        if len(parts) >= 2:
            return parts[0], " ".join(parts[1:])
        elif len(parts) == 1:
            return parts[0], ""
    return "Business", "Owner"


def export_leads():
    """
    Fetches all leads from Supabase and exports them into three specific CSV files.
    """
    db = SupabaseHelper()
    if not db.client:
        print("❌ Error: Database not connected.")
        return

    print("🚀 Fetching leads from Supabase...")
    response = db.client.table("leads").select("*").execute()
    leads = response.data if hasattr(response, "data") else []

    if not leads:
        print("⚠️ No leads in database — writing empty header-only export.")
        # Still create a timestamped empty CSV so /export/download doesn't
        # leak stale rows from a previous, larger export. Header schema
        # mirrors leads table columns.
        empty_cols = [
            "unique_key",
            "name",
            "company_name",
            "website",
            "email",
            "audit_status",
            "audit_results",
            "created_at",
        ]
        df = pd.DataFrame(columns=empty_cols)
    else:
        df = pd.DataFrame(leads)

    # Create exports directory if it doesn't exist
    export_dir = "exports"
    if not os.path.exists(export_dir):
        os.makedirs(export_dir)
        print(f"📁 Created directory: {export_dir}")

    timestamp = datetime.now().strftime("%Y%m%d")

    # 1. Full Leads All Data
    full_path = f"{export_dir}/full_leads_all_data_{timestamp}.csv"
    sanitize_dataframe_for_csv(df).to_csv(full_path, index=False)
    print(f"✅ Exported all leads: {full_path}")

    # 2. Leads Vulnerable (No SSL or No H1)
    # Note: audit_results is a JSON column in Supabase
    vulnerable_df = df[df.apply(check_vulnerability, axis=1)]
    vulnerable_path = f"{export_dir}/leads_vulnerable_no_ssl_no_h1_{timestamp}.csv"
    sanitize_dataframe_for_csv(vulnerable_df).to_csv(vulnerable_path, index=False)
    print(f"✅ Exported vulnerable leads ({len(vulnerable_df)}): {vulnerable_path}")

    # 3. High Priority Outreach List (SEO score < 50)
    high_priority_df = df[df.apply(is_high_priority, axis=1)]
    hp_path = f"{export_dir}/high_priority_outreach_{timestamp}.csv"
    sanitize_dataframe_for_csv(high_priority_df).to_csv(hp_path, index=False)
    print(f"✅ Exported high priority leads ({len(high_priority_df)}): {hp_path}")

    # 4. Outreach Ready List (Has Email OR Phone AND Score > 30)
    outreach_df = df[df.apply(is_outreach_ready, axis=1)].copy()

    # Simple First/Last Name extraction from leadership_team for outreach tools
    if not outreach_df.empty:
        outreach_df[["_first_name", "_last_name"]] = outreach_df.apply(
            lambda x: pd.Series(extract_names(x)), axis=1
        )

        # Build Instantly-compatible export
        # Standard Instantly fields: email, first_name, last_name, company_name, website, phone
        # Custom variables become {{variable_name}} in email templates
        outreach_export = pd.DataFrame()

        # Required field
        outreach_export["email"] = outreach_df.get("email", "")

        # Standard fields (Instantly recognizes these automatically)
        outreach_export["first_name"] = outreach_df["_first_name"]
        outreach_export["last_name"] = outreach_df["_last_name"]
        outreach_export["company_name"] = outreach_df.get(
            "company_name", outreach_df.get("name", "")
        )
        outreach_export["website"] = outreach_df.get("website", "")
        outreach_export["phone"] = outreach_df.get("phone", "")

        # Custom variables for Instantly email templates
        # Use these in Instantly as: {{email_hook}}, {{pain_points}}, {{linkedin_hook}}, etc.
        outreach_export["email_hook"] = outreach_df.get("email_hook", "")
        outreach_export["linkedin_hook"] = outreach_df.get("linkedin_hook", "")
        outreach_export["pain_points"] = outreach_df.get("pain_points", "")
        outreach_export["linkedin"] = outreach_df.get("linkedin", "")
        outreach_export["segment"] = outreach_df.get("segment", "")
        outreach_export["business_details"] = outreach_df.get("business_details", "")
        outreach_export["company_size"] = outreach_df.get("company_size", "")

        # Clean up NaN values - replace with empty strings for clean CSV
        outreach_export = outreach_export.fillna("")

        # Drop rows without email - Instantly requires email
        outreach_export = outreach_export[outreach_export["email"].str.strip() != ""]

        outreach_path = f"{export_dir}/outreach_ready_leads_{timestamp}.csv"
        sanitize_dataframe_for_csv(outreach_export).to_csv(outreach_path, index=False)
        print(
            f"✅ Exported outreach ready leads ({len(outreach_export)}): {outreach_path}"
        )


if __name__ == "__main__":
    export_leads()
