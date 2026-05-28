import asyncio
import os
import sys

# Ensure project root is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.scrapers.discovery_engine import DiscoveryEngine
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

# Define the 10 new niches for Wave 7
NICHES = [
    "Yoga and Pilates Studios",
    "Dance Schools",
    "Appliance Repair Services",
    "Handyman Services",
    "Window Replacement",
    "Cabinet Makers",
    "Electronic Repair Shops",
    "Smart Home Installers",
    "Carpet Cleaning Services",
    "Auto Glass Repair",
]

LOCATION = "Miami"


async def run_discovery_for_niches():
    """
    Iterates through defined niches and performs discovery for each.
    """
    engine = DiscoveryEngine()

    print(f"🚀 Starting Multi-Niche Discovery in {LOCATION}...")

    for niche in NICHES:
        print(f"🔍 Searching for: {niche}...")
        try:
            # We use max_results=50 to get a solid base for each niche
            leads = await engine.find_leads(niche, LOCATION, max_results=50)

            if leads:
                # Add segment/niche info to each lead before saving
                for lead in leads:
                    lead["segment"] = niche

                print(f"✅ Found {len(leads)} leads for {niche}. Saving to database...")
                await engine.enrich_and_save(leads)
            else:
                print(f"⚠️ No leads found for {niche}.")

            # Rate limiting / Anti-detection buffer between niches
            await asyncio.sleep(10)

        except Exception as e:
            logger.error("Error during discovery for %s: %s", niche, e, exc_info=True)
            print(f"❌ Failed discovery for {niche}: {e}")

    print("✨ Multi-niche discovery process completed.")


if __name__ == "__main__":
    asyncio.run(run_discovery_for_niches())
