
import asyncio
import sys
import os
from unittest.mock import MagicMock, patch

# Ensure src is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from src.scrapers.seo_audit import perform_seo_audit_async
from src.processors.leadhunter import LeadHunter

async def test_full_audit_enrichment_flow():
    print("\n--- Testing Full Phase 14 Integration Flow ---")
    
    # Mock HTML that includes CMS markers and infrastructure references
    mock_html = """
    <html>
        <head>
            <title>Elite Shopify Store</title>
            <meta name="description" content="We sell premium widgets on Shopify.">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <script src="https://cdn.shopify.com/s/files/1/0000/0000/assets/theme.js"></script>
            <script src="https://www.googletagmanager.com/gtm.js?id=GTM-XXXX"></script>
        </head>
        <body>
            <h1>Welcome to Elite Store</h1>
            <p>We've been in business for 10 years, serving global clients.</p>
            <a href="/login">Login to Dashboard</a>
            <a href="/sitemap.xml">Sitemap</a>
        </body>
    </html>
    """
    
    # Mock response for aiohttp
    class MockResponse:
        def __init__(self, text):
            self._text = text
            self.status = 200
        async def text(self):
            return self._text
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            pass

    # Patch aiohttp.ClientSession.get to return our mock HTML
    with patch('aiohttp.ClientSession.get', return_value=MockResponse(mock_html)):
        print("1. Performing SEO Audit...")
        audit_results = await perform_seo_audit_async("https://example-shopify-store.com")
        
        print(f"   - CMS Detected: {audit_results.get('cms')}")
        print(f"   - Response Time: {audit_results.get('response_time')}s")
        print(f"   - Tech Flags: {audit_results.get('tech_flags')}")
        print(f"   - Score: {audit_results.get('score')}")
        
        assert audit_results['cms'] == "Shopify"
        assert audit_results['tech_flags']['has_viewport'] is True
        assert audit_results['tech_flags']['has_gtm'] is True
        assert audit_results['tech_flags']['has_portal'] is True
        assert audit_results['tech_flags']['has_sitemap'] is True

    # 2. Test LeadHunter Integration with Mocked Gemini
    print("\n2. Testing LeadHunter Analysis with Tech Data...")
    hunter = LeadHunter()
    
    # Ensure hunter.client is mockable even without API key
    if hunter.client is None:
        hunter.client = MagicMock()
    
    # We'll mock the Gemini response to see if it receives the right context
    mock_response = MagicMock()
    mock_response.text = '{"linkedin_hook": "Tech-aware LinkedIn hook", "email_hook": "Tech-aware Email hook"}'
    
    # Use AsyncMock for the async call: self.client.aio.models.generate_content
    from unittest.mock import AsyncMock
    with patch.object(hunter.client.aio.models, 'generate_content', new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = mock_response
        
        pain_points = await hunter.analyze_pain_points_async(
            audit_results['page_text'], 
            business_name="Elite Shopify Store",
            audit_results=audit_results
        )
        print(f"   - Pain Points Analysis generated.")
        
        # Verify the prompt contained tech info
        args, kwargs = mock_gen.call_args
        prompt_text = kwargs.get('contents') or (args[0] if args else "")
        print(f"DEBUG: prompt_text length: {len(prompt_text)}")
        print(f"DEBUG: prompt_text snippet: {prompt_text[:100]}")
        assert "CMS/Platform: Shopify" in prompt_text
        assert "Site has a client portal/dashboard" in prompt_text
        
        hooks = await hunter.generate_outreach_hooks_async(
            "Fake pain points", 
            "Elite Shopify Store",
            audit_results=audit_results
        )
        print(f"   - Hooks generated: {hooks}")
        
        # Verify hook prompt contained CMS
        args, kwargs = mock_gen.call_args
        prompt_text = kwargs.get('contents') or (args[0] if args else "")
        assert "Site is built on Shopify" in prompt_text

    print("\n--- Phase 14 Integration Test PASSED ---")

if __name__ == "__main__":
    asyncio.run(test_full_audit_enrichment_flow())
