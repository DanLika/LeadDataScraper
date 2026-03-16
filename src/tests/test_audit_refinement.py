import asyncio
import sys
import os
from pathlib import Path

# Add src to path
sys.path.append(str(Path(__file__).parent.parent))

from scrapers.seo_audit import perform_seo_audit_async
from processors.leadhunter import LeadHunter

async def test_seo_audit_refinements():
    print("\n--- Testing SEO Audit Refinements ---")
    # Test URL known for having many pixels or being complex
    test_url = "https://www.shopify.com" # Example
    
    # Mocking BeautifulSoup response if needed, but let's try a real one or local HTML
    # For now, let's use a mock state for validation
    from bs4 import BeautifulSoup
    
    # Mock HTML with missing SEO pieces and several pixels
    mock_html = """
    <html>
        <head>
            <title>Short</title>
            <meta name="description" content="Too short">
            <script src="https://connect.facebook.net/en_US/fbevents.js"></script>
            <script src="https://static.ads-twitter.com/uwt.js"></script>
            <script src="https://analytics.tiktok.com/i18n/pixel/events.js"></script>
            <script src="https://static.hotjar.com/c/hotjar-123.js"></script>
        </head>
        <body>
            <h1>First H1</h1>
            <h1>Second H1 (Error)</h1>
            <h2>Subhead 1</h2>
            <h2>Subhead 2</h2>
        </body>
    </html>
    """
    
    results = await perform_seo_audit_async("http://example.com", mock_html)
    
    print(f"Results: {results}")
    
    flags = results.get("tech_flags", {})
    assert flags.get("has_tiktok_pixel") is True
    assert flags.get("has_hotjar") is True
    assert flags.get("has_facebook_pixel") is True
    
    red_flags = results.get("red_flags", [])
    assert "Title Length Warning (5 chars)" in red_flags
    assert "Meta Description Length Warning (9 chars)" in red_flags
    assert "Multiple H1 Headers Detected" in red_flags
    
    assert results.get("h2_count") == 2
    print("✅ SEO Audit Refinements Verified!")

async def test_leadhunter_scoring_and_segmentation():
    print("\n--- Testing LeadHunter Scoring ---")
    hunter = LeadHunter()
    
    # 1. Reputation Repair Lead
    lead_low_rating = {
        "name": "Low Shop",
        "rating": "3.2",
        "reviews": "150",
        "email": "contact@lowshop.com",
        "phone": "123456789"
    }
    score1 = hunter.calculate_outreach_score(lead_low_rating)
    segment1 = hunter.segment_lead(lead_low_rating)
    print(f"Lead 1 (Low Rating): Score={score1}, Segment={segment1}")
    assert segment1 == "Reputation Repair"

    # 2. New Business / Growth Lead
    lead_growth = {
        "name": "New Boutique",
        "rating": "4.8",
        "reviews": "5",
        "email": "hello@newboutique.com"
    }
    score2 = hunter.calculate_outreach_score(lead_growth)
    segment2 = hunter.segment_lead(lead_growth)
    print(f"Lead 2 (Growth): Score={score2}, Segment={segment2}")
    assert segment2 == "New Business / Growth"
    
    # 3. High Value Lead
    lead_high = {
        "name": "Top Agency",
        "rating": "4.9",
        "reviews": "500",
        "email": "ceo@topagency.com",
        "phone": "987654321",
        "facebook": "https://facebook.com/topagency",
        "instagram": "https://instagram.com/topagency",
        "enrichment_data": {
            "leadership_team": "John Doe",
            "company_size": "50-100"
        },
        "pain_points": ["Slow Site", "Missing Facebook Pixel"],
        "high_risk_flag": True
    }
    score3 = hunter.calculate_outreach_score(lead_high)
    # We need to manually set outreach_score for segmentation if we call it separately
    lead_high['outreach_score'] = score3
    segment3 = hunter.segment_lead(lead_high)
    print(f"Lead 3 (High Value): Score={score3}, Segment={segment3}")
    assert score3 > 80
    
    print("✅ LeadHunter Scoring and Segmentation Verified!")

if __name__ == "__main__":
    asyncio.run(test_seo_audit_refinements())
    asyncio.run(test_leadhunter_scoring_and_segmentation())
    
