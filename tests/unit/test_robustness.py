import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock, patch, sys

from datetime import datetime
from src.core.task_orchestrator import TaskOrchestrator


class TestRobustness(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.orchestrator = TaskOrchestrator(max_concurrent=2)
        self.orchestrator.db = MagicMock()
        self.orchestrator.db.client.table.return_value.update.return_value.eq.return_value.execute = MagicMock()
        self.orchestrator._update_job_status = AsyncMock()
        self.sleep_patcher = patch("asyncio.sleep", new_callable=AsyncMock)
        self.mock_sleep = self.sleep_patcher.start()

    def tearDown(self):
        self.sleep_patcher.stop()

    async def test_retry_increment(self):
        """Verify that retry_count increments on failure."""
        lead = {"unique_key": "test_lead", "retry_count": 0, "audit_status": "Pending"}
        auditor = MagicMock()
        auditor.audit_single_lead = AsyncMock(
            side_effect=Exception("Simulated Failure")
        )
        enricher = MagicMock()

        result = await self.orchestrator._process_single_lead(lead, auditor, enricher)

        # Check if the returned dictionary has retry_count=1, as the actual DB update
        # happens in batch later, not inside _process_single_lead
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("retry_count"), 1)
        self.assertEqual(result.get("audit_status"), "Pending")

    async def test_no_website_logs_warning_not_error(self):
        """`audit_single_lead` returning ``{"status":"Failed","error":"No website"}``
        is a graceful skip — must route to WARNING, not ERROR.

        Phase 13 dogfood smoke surfaced ~54/day ERROR-level
        ``Error processing lead ...: Audit failed: No website`` entries
        that polluted the operator dashboard. The orchestrator now raises
        a typed ``NoWebsiteError`` for this sentinel and the per-lead
        catch logs at WARNING (no ``exc_info``). retry_count + last_error
        mutations stay identical so the 3-strike behaviour is preserved.
        """
        lead = {"unique_key": "ghost_lead", "retry_count": 0, "audit_status": "Pending"}
        auditor = MagicMock()
        auditor.audit_single_lead = AsyncMock(
            return_value={
                "unique_key": "ghost_lead",
                "status": "Failed",
                "error": "No website",
            }
        )
        enricher = MagicMock()

        with self.assertLogs("src.core.task_orchestrator", level="WARNING") as cm:
            result = await self.orchestrator._process_single_lead(
                lead, auditor, enricher
            )

        # At least one WARNING; NO ERROR records on this path.
        levels = [r.levelname for r in cm.records]
        self.assertIn("WARNING", levels)
        self.assertNotIn("ERROR", levels)
        warn_record = next(r for r in cm.records if r.levelname == "WARNING")
        self.assertIn("ghost_lead", warn_record.getMessage())
        # exc_info must NOT be attached on the graceful path.
        self.assertIsNone(warn_record.exc_info)

        # 3-strike state machine still mutates identically.
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("retry_count"), 1)
        self.assertEqual(result.get("audit_status"), "Pending")
        self.assertEqual(result.get("last_error"), "no website to audit")

    async def test_fail_fast(self):
        """Verify that orchestrator fails fast after consecutive batch failures."""
        job_id = "test_job"

        # Mock getting job status
        self.orchestrator.get_job_status = AsyncMock(
            return_value={"status": "starting", "processed_count": 0}
        )

        # Mock chunk fetch to return 1 lead
        self.orchestrator.db.client.table.return_value.select.return_value.or_.return_value.lt.return_value.order.return_value.limit.return_value.execute = MagicMock(
            return_value=MagicMock(data=[{"unique_key": "fail_lead", "retry_count": 0}])
        )

        # Mock total count
        count_mock = MagicMock()
        count_mock.count = 10
        self.orchestrator.db.client.table.return_value.select.return_value.or_.return_value.lt.return_value.execute = MagicMock(
            return_value=count_mock
        )

        # Mock single lead process to always fail
        self.orchestrator._process_single_lead = AsyncMock(return_value=False)

        # Mock job status to return a valid dict so processed_count integer comparison works
        self.orchestrator.db.client.table.return_value.select.return_value.eq.return_value.execute = MagicMock(
            return_value=MagicMock(data=[{"processed_count": 0}])
        )

        # We need to stop the infinite while True loop in _process_in_chunks or it will run forever
        # For testing, we'll patch the while loop or the chunk fetcher to return empty after some calls
        side_effect_data = [
            MagicMock(data=[{"unique_key": "f1"}]),
            MagicMock(data=[{"unique_key": "f2"}]),
            MagicMock(data=[{"unique_key": "f3"}]),
            MagicMock(data=[{"unique_key": "f4"}]),
            MagicMock(data=[{"unique_key": "f5"}]),
            MagicMock(data=[]),  # Stop loop
        ]
        self.orchestrator.db.client.table.return_value.select.return_value.or_.return_value.lt.return_value.order.return_value.limit.return_value.execute.side_effect = side_effect_data

        with self.assertRaises(Exception) as cm:
            await self.orchestrator._process_in_chunks(job_id, chunk_size=1)
        self.assertIn("5 consecutive batches failed completely.", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
