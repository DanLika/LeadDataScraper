import asyncio
from src.processors.leadhunter import LeadHunter
from src.core.data_manager import merge_and_deduplicate
import pandas as pd

async def test_pain_points():
    print("Testing analyze_pain_points_async...")
    hunter = LeadHunter()
    if not hunter.model:
        print("⚠️ Gemini Model not initialized (missing API key). Skipping AI call, but checking fallback string.")
        sample_text = "Some text"
        result = await hunter.analyze_pain_points_async(sample_text)
        assert result == "No page content available for analysis."
        print("Fallback test passed!")
        return

    sample_text = """
    Our website is very slow and we are losing customers. 
    We don't have a mobile app and our customers are complaining.
    The checkout process is broken.
    """
    pain_points = await hunter.analyze_pain_points_async(sample_text)
    print(f"Detected Pain Points: {pain_points}")
    assert isinstance(pain_points, str)
    print("Pain points test passed!")

def test_naming_unification():
    print("Testing unique_key unification in data_manager...")
    data = [
        {"unique_key": "key1", "name": "Lead 1", "email": "test1@example.com"},
        {"unique_key": "key1", "name": "Lead 1 Duplicate", "email": "test1@example.com"}
    ]
    df = pd.DataFrame(data)
    merged_df = merge_and_deduplicate([df])
    print(f"Merged columns: {merged_df.columns.tolist()}")
    assert "unique_key" in merged_df.columns
    assert len(merged_df) == 1
    print("Naming unification test passed!")

async def main():
    try:
        await test_pain_points()
    except Exception as e:
        print(f"Pain points test failed: {e}")
        
    try:
        test_naming_unification()
    except Exception as e:
        print(f"Naming unification test failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())
