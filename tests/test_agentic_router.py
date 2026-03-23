import sys
from unittest.mock import MagicMock, AsyncMock, patch
import pytest
import unittest
import os

# Mock external dependencies before importing the module to be tested
sys.modules['playwright'] = MagicMock()
sys.modules['playwright.async_api'] = MagicMock()
sys.modules['google'] = MagicMock()
sys.modules['google.genai'] = MagicMock()
sys.modules['google.genai.types'] = MagicMock()
sys.modules['google.generativeai'] = MagicMock()
sys.modules['src.utils.supabase_helper'] = MagicMock()
sys.modules['supabase'] = MagicMock()
sys.modules['dotenv'] = MagicMock()
sys.modules['pandas'] = MagicMock()
sys.modules['numpy'] = MagicMock()
sys.modules['aiohttp'] = MagicMock()
sys.modules['bs4'] = MagicMock()
sys.modules['fake_useragent'] = MagicMock()

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

class TestAgenticRouterExecuteTask(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Initialize without real dependencies
        with patch('src.core.agentic_router.os.getenv', return_value='fake_key'), \
             patch('src.core.agentic_router.SupabaseHelper'):
            self.router = AgenticRouter()

    @patch.object(AgenticRouter, '_execute_database_query', new_callable=AsyncMock)
    async def test_execute_database_query(self, mock_method):
        mock_method.return_value = {"answer": "fake answer"}
        result = await self.router.execute_task({
            "task": "DATABASE_QUERY",
            "params": {"query_text": "hello"}
        })
        mock_method.assert_called_once_with("hello", {"query_text": "hello"})
        self.assertEqual(result, {"answer": "fake answer"})

    @patch.object(AgenticRouter, '_get_status_summary', new_callable=AsyncMock)
    async def test_execute_status_check(self, mock_method):
        mock_method.return_value = {"summary": "fake summary"}
        result = await self.router.execute_task({
            "task": "STATUS_CHECK"
        })
        mock_method.assert_called_once_with()
        self.assertEqual(result, {"summary": "fake summary"})

    @patch('src.core.parallel_auditor.ParallelAuditor')
    async def test_execute_seo_audit_single(self, mock_auditor_class):
        mock_auditor = mock_auditor_class.return_value
        mock_auditor.audit_single_lead = AsyncMock(return_value={"score": 100})

        # Mock DB response
        self.router.db.client.table.return_value.select.return_value.eq.return_value.execute = MagicMock(
            return_value=MagicMock(data=[{"unique_key": "test_key"}])
        )

        result = await self.router.execute_task({
            "task": "SEO_AUDIT",
            "params": {"unique_key": "test_key"}
        })

        mock_auditor.audit_single_lead.assert_called_once_with({"unique_key": "test_key"})
        self.assertEqual(result, {"message": "SEO Audit completed for single lead.", "result": {"score": 100}})

    @patch('src.core.parallel_auditor.ParallelAuditor')
    async def test_execute_seo_audit_single_not_found(self, mock_auditor_class):
        # Mock DB response with no data
        self.router.db.client.table.return_value.select.return_value.eq.return_value.execute = MagicMock(
            return_value=MagicMock(data=[])
        )

        result = await self.router.execute_task({
            "task": "SEO_AUDIT",
            "params": {"unique_key": "test_key"}
        })

        self.assertEqual(result, {"error": "Lead test_key not found for SEO Audit"})

    @patch('src.core.task_orchestrator.TaskOrchestrator')
    async def test_execute_seo_audit_massive(self, mock_orchestrator_class):
        mock_orchestrator = mock_orchestrator_class.return_value
        mock_orchestrator.run_massive_pipeline = AsyncMock(return_value="job_123")

        result = await self.router.execute_task({
            "task": "SEO_AUDIT"
        })

        mock_orchestrator.run_massive_pipeline.assert_called_once_with(tasks=["audit"])
        self.assertEqual(result, {"message": "Massive SEO Audit pipeline started.", "job_id": "job_123"})

    @patch.object(AgenticRouter, '_generate_outreach_draft', new_callable=AsyncMock)
    async def test_execute_outreach_draft(self, mock_method):
        mock_method.return_value = {"draft": "hello"}
        result = await self.router.execute_task({
            "task": "OUTREACH_DRAFT",
            "params": {"unique_key": "test_key"}
        })
        mock_method.assert_called_once_with({"unique_key": "test_key"})
        self.assertEqual(result, {"draft": "hello"})

    @patch.object(AgenticRouter, '_get_strategic_insights', new_callable=AsyncMock)
    async def test_execute_get_insights(self, mock_method):
        mock_method.return_value = {"insights": []}
        result = await self.router.execute_task({
            "task": "GET_INSIGHTS"
        })
        mock_method.assert_called_once_with()
        self.assertEqual(result, {"insights": []})

    @patch.object(AgenticRouter, '_execute_data_merge', new_callable=AsyncMock)
    async def test_execute_data_merge(self, mock_method):
        mock_method.return_value = {"message": "done"}
        result = await self.router.execute_task({
            "task": "DATA_MERGE"
        })
        mock_method.assert_called_once_with()
        self.assertEqual(result, {"message": "done"})

    @patch.object(AgenticRouter, '_execute_deep_hunt', new_callable=AsyncMock)
    async def test_execute_deep_hunt(self, mock_method):
        mock_method.return_value = {"message": "hunted"}
        result = await self.router.execute_task({
            "task": "DEEP_HUNT",
            "params": {"unique_key": "key"}
        })
        mock_method.assert_called_once_with({"unique_key": "key"})
        self.assertEqual(result, {"message": "hunted"})

    @patch.object(AgenticRouter, '_execute_massive_pipeline', new_callable=AsyncMock)
    async def test_execute_run_massive_pipeline(self, mock_method):
        mock_method.return_value = {"message": "started"}
        result = await self.router.execute_task({
            "task": "RUN_MASSIVE_PIPELINE",
            "params": {"filters": "high-risk"}
        })
        mock_method.assert_called_once_with({"filters": "high-risk"})
        self.assertEqual(result, {"message": "started"})

    @patch.object(AgenticRouter, '_generate_linkedin_draft', new_callable=AsyncMock)
    async def test_execute_linkedin_draft(self, mock_method):
        mock_method.return_value = {"draft": "hi"}
        result = await self.router.execute_task({
            "task": "LINKEDIN_DRAFT",
            "params": {"unique_key": "key"}
        })
        mock_method.assert_called_once_with({"unique_key": "key"})
        self.assertEqual(result, {"draft": "hi"})

    @patch.object(AgenticRouter, '_execute_discovery_search', new_callable=AsyncMock)
    async def test_execute_discovery_search(self, mock_method):
        mock_method.return_value = {"message": "searching"}
        result = await self.router.execute_task({
            "task": "DISCOVERY_SEARCH",
            "params": {"query": "pizza"}
        })
        mock_method.assert_called_once_with({"query": "pizza"})
        self.assertEqual(result, {"message": "searching"})

    @patch.object(AgenticRouter, '_execute_deep_enrichment', new_callable=AsyncMock)
    async def test_execute_deep_enrichment(self, mock_method):
        mock_method.return_value = {"message": "enriched"}
        result = await self.router.execute_task({
            "task": "DEEP_ENRICHMENT",
            "params": {"unique_key": "key"}
        })
        mock_method.assert_called_once_with({"unique_key": "key"})
        self.assertEqual(result, {"message": "enriched"})

    @patch.object(AgenticRouter, '_generate_campaign_strategy', new_callable=AsyncMock)
    async def test_execute_campaign_strategy(self, mock_method):
        mock_method.return_value = {"message": "planned"}
        result = await self.router.execute_task({
            "task": "CAMPAIGN_STRATEGY",
            "params": {"filters": "high-risk"}
        })
        mock_method.assert_called_once_with({"filters": "high-risk"})
        self.assertEqual(result, {"message": "planned"})

    async def test_execute_unknown_task(self):
        result = await self.router.execute_task({
            "task": "UNKNOWN_TASK"
        })
        self.assertEqual(result, {"error": "Unknown task: UNKNOWN_TASK"})

if __name__ == "__main__":
    unittest.main()
