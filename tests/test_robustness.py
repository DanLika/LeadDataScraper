import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock, patch, sys

# Mock external dependencies before imports
sys.modules['playwright'] = MagicMock()
sys.modules['playwright.async_api'] = MagicMock()
sys.modules['google.generativeai'] = MagicMock()
sys.modules['supabase'] = MagicMock()

from datetime import datetime
from src.core.task_orchestrator import TaskOrchestrator

class TestRobustness(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.orchestrator = TaskOrchestrator(max_concurrent=2)
        self.orchestrator.db = MagicMock()
        self.orchestrator.db.client.table.return_value.update.return_value.eq.return_value.execute = MagicMock()
        self.orchestrator._update_job_status = AsyncMock()
        self.sleep_patcher = patch('asyncio.sleep', new_callable=AsyncMock)
        self.mock_sleep = self.sleep_patcher.start()

    def tearDown(self):
        self.sleep_patcher.stop()

    async def test_retry_increment(self):
        """Verify that retry_count increments on failure."""
        lead = {"unique_key": "test_lead", "retry_count": 0, "audit_status": "Pending"}
        auditor = MagicMock()
        auditor.audit_single_lead = AsyncMock(side_effect=Exception("Simulated Failure"))
        enricher = MagicMock()

        result = await self.orchestrator._process_single_lead(lead, auditor, enricher)
        
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("retry_count"), 1)
        self.assertEqual(result.get("audit_status"), "Pending")

    async def test_fail_fast(self):
        """Verify that orchestrator fails fast after consecutive batch failures."""
        job_id = "test_job"
        
        # Mock getting job status
        self.orchestrator.get_job_status = AsyncMock(return_value={"status": "starting", "processed_count": 0})

        # Mock chunk fetch to return 1 lead
        self.orchestrator.db.client.table.return_value.select.return_value.or_.return_value.lt.return_value.order.return_value.limit.return_value.execute = MagicMock(
            return_value=MagicMock(data=[{"unique_key": "fail_lead", "retry_count": 0}])
        )
        
        # Mock total count
        count_mock = MagicMock()
        count_mock.count = 10
        self.orchestrator.db.client.table.return_value.select.return_value.or_.return_value.lt.return_value.execute = MagicMock(return_value=count_mock)

        # Mock single lead process to always fail
        self.orchestrator._process_single_lead = AsyncMock(return_value=False)

        # We need to stop the infinite while True loop in _process_in_chunks or it will run forever
        # For testing, we'll patch the while loop or the chunk fetcher to return empty after some calls
        side_effect_data = [
            MagicMock(data=[{"unique_key": "f1"}]),
            MagicMock(data=[{"unique_key": "f2"}]),
            MagicMock(data=[{"unique_key": "f3"}]),
            MagicMock(data=[{"unique_key": "f4"}]),
            MagicMock(data=[{"unique_key": "f5"}]),
            MagicMock(data=[]) # Stop loop
        ]
        self.orchestrator.db.client.table.return_value.select.return_value.or_.return_value.lt.return_value.order.return_value.limit.return_value.execute.side_effect = side_effect_data

        with self.assertRaises(Exception) as cm:
            await self.orchestrator._process_in_chunks(job_id, chunk_size=1)
        
        self.assertIn("5 consecutive batches failed completely.", str(cm.exception))

if __name__ == "__main__":
    unittest.main()
