import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock, patch
import sys

# Mock external dependencies before imports
sys.modules['playwright'] = MagicMock()
sys.modules['playwright.async_api'] = MagicMock()
sys.modules['google'] = MagicMock()
sys.modules['google.genai'] = MagicMock()
sys.modules['google.generativeai'] = MagicMock()
sys.modules['supabase'] = MagicMock()
sys.modules['pandas'] = MagicMock()
sys.modules['numpy'] = MagicMock()
sys.modules['dotenv'] = MagicMock()
sys.modules['aiohttp'] = MagicMock()
sys.modules['bs4'] = MagicMock()
sys.modules['fake_useragent'] = MagicMock()

from src.core.agentic_router import AgenticRouter

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
