import os
import pandas as pd
from src.utils.csv_helper import save_csv
from src.processors.google_maps import process_gmaps_df
from src.processors.ai_mapper import normalize_df_with_ai
from src.core.data_manager import merge_and_deduplicate
from src.scrapers.seo_audit import perform_seo_audit_async as audit_website

def run_pipeline(messy_file=None, gmaps_file=None, api_key=None):
    """
    Demonstrates the full lead processing pipeline.
    """
    processed_dfs = []

    # 1. Process Google Maps Data if provided
    if gmaps_file and os.path.exists(gmaps_file):
        print(f"--- Processing Google Maps: {gmaps_file} ---")
        df_gmaps_raw = pd.read_csv(gmaps_file, dtype=str)
        df_gmaps_clean = process_gmaps_df(df_gmaps_raw, source_label="Google Maps Demo")
        processed_dfs.append(df_gmaps_clean)

    # 2. Process Messy AI-mapped Data if provided
    if messy_file and os.path.exists(messy_file):
        print(f"--- Processing Messy Data with AI: {messy_file} ---")
        df_messy = pd.read_csv(messy_file, dtype=str)
        df_standardized = normalize_df_with_ai(df_messy, api_key)
        # Generate UNIQUE_KEY for this normalized data
        df_standardized['UNIQUE_KEY'] = df_standardized['Website'].fillna('') + "_" + df_standardized['Name'].fillna('')
        processed_dfs.append(df_standardized)

    # 3. Merge and Deduplicate
    if processed_dfs:
        final_leads = merge_and_deduplicate(processed_dfs)
        save_csv(final_leads, "data/processed/final_leads.csv")
        
        # 4. Optional: Run SEO Audit on first 3 leads as a demo
        print("\n--- Running SEO Audit Demo (First 3 unique websites) ---")
        for idx, row in final_leads.dropna(subset=['Website']).head(3).iterrows():
            site = row['Website']
            if site and site.startswith('http'):
                print(f"Checking {row['Name']} ({site})...")
                audit_res = audit_website(site)
                print(f"   Flags: {audit_res['red_flags']}")
    else:
        print("❌ No input files found to process.")

if __name__ == "__main__":
    # Example usage:
    # run_pipeline(messy_file="data/raw/apify_messy.csv", api_key="YOUR_KEY")
    print("Lead Data Scraper Pipeline initialized.")
    run_pipeline()
