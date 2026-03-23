import sys
from unittest.mock import MagicMock, patch
import pytest
import unittest

# Mock external dependencies before importing the module to be tested
sys.modules['google'] = MagicMock()
sys.modules['google.genai'] = MagicMock()
sys.modules['google.genai.types'] = MagicMock()
sys.modules['src.utils.supabase_helper'] = MagicMock()
sys.modules['dotenv'] = MagicMock()

from src.core.agentic_router import AgenticRouter

class TestAgenticRouterRouteInstruction(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        # Patching SupabaseHelper and load_dotenv so they don't do real things
        self.patcher_supabase = patch('src.core.agentic_router.SupabaseHelper')
        self.mock_supabase = self.patcher_supabase.start()

        self.patcher_dotenv = patch('src.core.agentic_router.load_dotenv')
        self.patcher_dotenv.start()

        self.patcher_genai_client = patch('src.core.agentic_router.genai.Client')
        self.mock_genai_client = self.patcher_genai_client.start()

        self.patcher_getenv = patch('src.core.agentic_router.os.getenv')
        self.mock_getenv = self.patcher_getenv.start()

    def tearDown(self):
        patch.stopall()

    async def test_route_instruction_no_client(self):
        # Set os.getenv("GEMINI_API_KEY") to None
        self.mock_getenv.return_value = None

        router = AgenticRouter()

        instruction = "do something"
        result = await router.route_instruction(instruction)

        self.assertEqual(result, {"error": "AI model not initialized"})

    async def test_route_instruction_successful_tool_call(self):
        self.mock_getenv.return_value = "dummy_api_key"

        # Create a router instance. self.client will be initialized.
        router = AgenticRouter()

        # Mock the client's generate_content method response
        mock_response = MagicMock()
        mock_candidate = MagicMock()
        mock_part = MagicMock()
        mock_function_call = MagicMock()

        mock_function_call.name = "seo_audit"
        mock_function_call.args = {"unique_key": "12345"}

        mock_part.function_call = mock_function_call
        mock_candidate.content.parts = [mock_part]
        mock_response.candidates = [mock_candidate]

        router.client.models.generate_content.return_value = mock_response

        instruction = "audit website 12345"
        result = await router.route_instruction(instruction)

        expected_result = {
            "task": "SEO_AUDIT",
            "params": {"unique_key": "12345"},
            "reasoning": "AI selected tool: seo_audit"
        }
        self.assertEqual(result, expected_result)

    async def test_route_instruction_successful_tool_call_no_args(self):
        self.mock_getenv.return_value = "dummy_api_key"

        # Create a router instance. self.client will be initialized.
        router = AgenticRouter()

        # Mock the client's generate_content method response
        mock_response = MagicMock()
        mock_candidate = MagicMock()
        mock_part = MagicMock()
        mock_function_call = MagicMock()

        mock_function_call.name = "status_check"
        mock_function_call.args = None  # test the 'if call.args else {}' branch

        mock_part.function_call = mock_function_call
        mock_candidate.content.parts = [mock_part]
        mock_response.candidates = [mock_candidate]

        router.client.models.generate_content.return_value = mock_response

        instruction = "check status"
        result = await router.route_instruction(instruction)

        expected_result = {
            "task": "STATUS_CHECK",
            "params": {},
            "reasoning": "AI selected tool: status_check"
        }
        self.assertEqual(result, expected_result)

    async def test_route_instruction_no_tool_call_with_text(self):
        self.mock_getenv.return_value = "dummy_api_key"

        router = AgenticRouter()

        # Mock the client's generate_content method response
        mock_response = MagicMock()
        mock_candidate = MagicMock()
        mock_part = MagicMock()

        mock_part.function_call = None
        mock_candidate.content.parts = [mock_part]
        mock_response.candidates = [mock_candidate]
        mock_response.text = "Here is some text response."

        router.client.models.generate_content.return_value = mock_response

        instruction = "hello there"
        result = await router.route_instruction(instruction)

        expected_result = {
            "task": "UNKNOWN",
            "params": {},
            "reasoning": "No tool was called by the model.",
            "raw": "Here is some text response."
        }
        self.assertEqual(result, expected_result)

    async def test_route_instruction_no_tool_call_without_text(self):
        self.mock_getenv.return_value = "dummy_api_key"

        router = AgenticRouter()

        # Mock the client's generate_content method response
        mock_response = MagicMock()
        mock_candidate = MagicMock()
        mock_part = MagicMock()

        mock_part.function_call = None
        mock_candidate.content.parts = [mock_part]
        mock_response.candidates = [mock_candidate]
        mock_response.text = None # No text response branch

        router.client.models.generate_content.return_value = mock_response

        instruction = "hello there"
        result = await router.route_instruction(instruction)

        expected_result = {
            "task": "UNKNOWN",
            "params": {},
            "reasoning": "No tool was called by the model.",
            "raw": "No text response"
        }
        self.assertEqual(result, expected_result)

    async def test_route_instruction_exception_handling(self):
        self.mock_getenv.return_value = "dummy_api_key"

        router = AgenticRouter()

        # Mock generate_content to raise an Exception
        error_message = "API rate limit exceeded"
        router.client.models.generate_content.side_effect = Exception(error_message)

        instruction = "trigger failure"
        result = await router.route_instruction(instruction)

        expected_result = {
            "task": "ERROR",
            "params": {},
            "reasoning": f"Tool calling failed: {error_message}"
        }
        self.assertEqual(result, expected_result)

if __name__ == '__main__':
    unittest.main()
