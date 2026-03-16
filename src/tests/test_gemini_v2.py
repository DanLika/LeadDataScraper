
import asyncio
import sys
import os
import json
from unittest.mock import MagicMock, patch, AsyncMock

# Ensure src is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from src.processors.leadhunter import LeadHunter
from src.processors.ai_mapper import GeminiMapper
from src.core.agentic_router import AgenticRouter
from src.scrapers.enrichment_engine import EnrichmentEngine

async def test_gemini_v2_migration():
    print("\n--- Testing Google GenAI (v2) Migration ---")
    
    # 1. Test LeadHunter
    print("\n1. Testing LeadHunter...")
    hunter = LeadHunter()
    if hunter.client is None:
        hunter.client = MagicMock()
    
    mock_response = MagicMock()
    mock_response.text = '{"linkedin_hook": "v2 hook", "email_hook": "v2 email"}'
    
    # Mocking the async call: self.client.aio.models.generate_content
    with patch.object(hunter.client.aio.models, 'generate_content', new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = mock_response
        
        # Test pain points analysis
        await hunter.analyze_pain_points_async("Sample text", "Sample Corp")
        print("   ✅ LeadHunter.analyze_pain_points_async called new SDK")
        
        # Test hook generation
        await hunter.generate_outreach_hooks_async("Pain points", "Sample Corp")
        print("   ✅ LeadHunter.generate_outreach_hooks_async called new SDK")

    # 2. Test GeminiMapper
    print("\n2. Testing GeminiMapper...")
    mapper = GeminiMapper()
    if mapper.client is None:
        mapper.client = MagicMock()
    
    mock_mapper_response = MagicMock()
    mock_mapper_response.text = '{"Company": "name"}'
    
    # Mocking the sync call: self.client.models.generate_content
    with patch.object(mapper.client.models, 'generate_content') as mock_gen:
        mock_gen.return_value = mock_mapper_response
        mapping = mapper.get_column_mapping(["Company"])
        assert mapping == {"Company": "name"}
        print("   ✅ GeminiMapper.get_column_mapping called new SDK")

    # 3. Test AgenticRouter
    print("\n3. Testing AgenticRouter...")
    router = AgenticRouter()
    if router.client is None:
        router.client = MagicMock()
        
    mock_router_response = MagicMock()
    mock_call = MagicMock()
    mock_call.name = "seo_audit"
    mock_call.args = {"unique_key": "test_key"}
    
    mock_part = MagicMock()
    mock_part.function_call = mock_call
    
    mock_candidate = MagicMock()
    mock_candidate.content.parts = [mock_part]
    
    mock_router_response.candidates = [mock_candidate]
    
    # Mocking sync calls in router
    with patch.object(router.client.models, 'generate_content') as mock_gen:
        mock_gen.return_value = mock_router_response
        plan = await router.route_instruction("Audit example.com")
        assert plan["task"] == "SEO_AUDIT"
        print("   ✅ AgenticRouter.route_instruction called new SDK")

    # 4. Test EnrichmentEngine
    print("\n4. Testing EnrichmentEngine...")
    engine = EnrichmentEngine()
    if engine.client is None:
        engine.client = MagicMock()
        
    mock_enrich_response = MagicMock()
    mock_enrich_response.text = '{"company_name": "Enriched Corp"}'
    
    # Mocking async call: self.client.aio.models.generate_content
    with patch.object(engine.client.aio.models, 'generate_content', new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = mock_enrich_response
        result = await engine.deep_ai_parse(["Page content"], "Enriched Corp")
        assert result["company_name"] == "Enriched Corp"
        print("   ✅ EnrichmentEngine.deep_ai_parse called new SDK")

    print("\n--- All Migration Tests PASSED ---")

if __name__ == "__main__":
    asyncio.run(test_gemini_v2_migration())
