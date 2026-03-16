import asyncio
import os
import sys

# Ensure project root is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from src.scrapers.discovery_engine import DiscoveryEngine

async def run_miami_dentists_discovery():
    engine = DiscoveryEngine()
    
    # Search for Dentists in Miami
    query = "Dentist"
    location = "Miami, FL"
    
    print(f"🚀 Launching discovery for {query} in {location}...")
    leads = await engine.find_leads(query, location)
    
    if leads:
        print(f"✨ Found {len(leads)} leads. Saving to Supabase...")
        await engine.enrich_and_save(leads)
        print("✅ Discovery and persistence complete.")
    else:
        print("❌ No leads found.")

if __name__ == "__main__":
    asyncio.run(run_miami_dentists_discovery())
