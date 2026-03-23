import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock, patch, sys

# Mock external dependencies before imports
sys.modules['playwright'] = MagicMock()
sys.modules['playwright.async_api'] = MagicMock()
sys.modules['google.generativeai'] = MagicMock()
sys.modules['google.genai'] = MagicMock()
sys.modules['google.genai.types'] = MagicMock()
sys.modules['google'] = MagicMock()
sys.modules['supabase'] = MagicMock()

from src.core.agentic_router import AgenticRouter

class TestAgenticRouter(unittest.IsolatedAsyncioTestCase):
    async def test_route_instruction_error_path(self):
        """Verify that route_instruction correctly handles and formats exceptions during AI calling."""
        # Instantiate router
        router = AgenticRouter()

        # Override client with a mock
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = Exception("Simulated AI Failure")
        router.client = mock_client

        # Act
        result = await router.route_instruction("do something")

        # Assert
        self.assertEqual(result.get("task"), "ERROR")
        self.assertEqual(result.get("params"), {})
        self.assertEqual(result.get("reasoning"), "Tool calling failed: Simulated AI Failure")

if __name__ == "__main__":
    unittest.main()
