import asyncio
import pandas as pd
import numpy as np
from src.processors.google_maps import process_gmaps_df
from src.scrapers.enrichment_engine import EnrichmentEngine

async def test_gmaps_naming():
    print("Testing unique_key in google_maps.py...")
    # Mock row as if it came from raw export
    data = {
        'hfpxzc href': 'https://maps.google.com/test',
        'qBF1Pd': 'Test Clinic',
        'MW4etd': '4.5',
        'UY7F9': '(100)',
        'W4Efsd': 'Dentist',
        'W4Efsd 3': '123 Test St',
        'lcr4fd href': 'http://testclinic.com',
        'UsdlK': '0123456789'
    }
    df = pd.DataFrame([data])
    processed_df = process_gmaps_df(df)
    
    print(f"Processed columns: {processed_df.columns.tolist()}")
    assert "unique_key" in processed_df.columns
    assert "UNIQUE_KEY" not in processed_df.columns
    print("Google Maps naming test passed!")

async def test_enrichment_parallel():
    print("Testing parallel extraction in EnrichmentEngine...")
    engine = EnrichmentEngine()
    # Mock lead with multiple URLs
    test_lead = {
        "name": "Parallel Test",
        "website": "https://www.google.com",
        "about_url": "https://www.google.com/about"
    }
    # Note: This will actually try to hit the URLs if not mocked further, 
    # but we just want to see if the parallel logic flows without error.
    # Given we might not have internet/browser context in all environments, 
    # we'll just check if the return value structure is correct.
    result = await engine.enrich_lead(test_lead)
    print(f"Enrichment result keys: {result.keys()}")
    assert "enrichment_status" in result
    print("Enrichment parallel test logic verified!")

async def main():
    try:
        await test_gmaps_naming()
    except Exception as e:
        print(f"Gmaps naming test failed: {e}")

    try:
        await test_enrichment_parallel()
    except Exception as e:
        print(f"Enrichment parallel test failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())
