import unittest
from unittest.mock import MagicMock, AsyncMock, patch
import sys

class TestRunMiamiAudit(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        # Mock external dependencies to avoid actual database/API interactions
        cls.sys_modules_patcher = patch.dict('sys.modules', {
            'playwright': MagicMock(),
            'playwright.async_api': MagicMock(),
            'google.generativeai': MagicMock(),
            'supabase': MagicMock()
        })
        cls.sys_modules_patcher.start()

        # Import the script locally after patching sys.modules
        global run_miami_audit
        from src.scripts.run_miami_audit import run_miami_audit

    @classmethod
    def tearDownClass(cls):
        cls.sys_modules_patcher.stop()

    async def test_run_miami_audit_success(self):
        """Test the run_miami_audit script completes successfully."""
        with patch('src.scripts.run_miami_audit.TaskOrchestrator') as MockOrchestrator, \
             patch('asyncio.sleep', new_callable=AsyncMock) as mock_sleep:

            # Setup the mocked TaskOrchestrator
            mock_orchestrator_instance = MagicMock()
            MockOrchestrator.return_value = mock_orchestrator_instance

            # Mock the massive pipeline run
            mock_orchestrator_instance.run_massive_pipeline = AsyncMock(return_value="mock_job_id")

            # Mock get_job_status to return "running" then "completed"
            mock_orchestrator_instance.get_job_status = AsyncMock(side_effect=[
                {"status": "running", "processed_count": 0, "total_count": 10, "current_phase": "Phase 1"},
                {"status": "running", "processed_count": 5, "total_count": 10, "current_phase": "Phase 2"},
                {"status": "completed", "processed_count": 10, "total_count": 10, "current_phase": "Finished"},
                # Ensure we don't accidentally call it more times
                {"status": "completed", "processed_count": 10, "total_count": 10, "current_phase": "Finished"}
            ])

            # Run the script
            await run_miami_audit()

            # Verify the pipeline was triggered with correct tasks
            mock_orchestrator_instance.run_massive_pipeline.assert_awaited_once_with(tasks=["audit", "enrich", "hunt"])

            # Verify the sleep was awaited (it monitors 10 times, but our loop breaks on 'completed', which is the 3rd item)
            self.assertEqual(mock_sleep.call_count, 3)

            # Verify get_job_status was called the expected number of times
            self.assertEqual(mock_orchestrator_instance.get_job_status.call_count, 3)
            mock_orchestrator_instance.get_job_status.assert_awaited_with("mock_job_id")

    async def test_run_miami_audit_early_exit_on_failed(self):
        """Test the run_miami_audit script exits early if the job fails."""
        with patch('src.scripts.run_miami_audit.TaskOrchestrator') as MockOrchestrator, \
             patch('asyncio.sleep', new_callable=AsyncMock) as mock_sleep:

            # Setup the mocked TaskOrchestrator
            mock_orchestrator_instance = MagicMock()
            MockOrchestrator.return_value = mock_orchestrator_instance

            # Mock the massive pipeline run
            mock_orchestrator_instance.run_massive_pipeline = AsyncMock(return_value="mock_job_id")

            # Mock get_job_status to return "failed" immediately
            mock_orchestrator_instance.get_job_status = AsyncMock(return_value={"status": "failed", "processed_count": 0, "total_count": 10, "current_phase": "Error"})

            # Run the script
            await run_miami_audit()

            # Verify the pipeline was triggered
            mock_orchestrator_instance.run_massive_pipeline.assert_awaited_once()

            # Verify the loop broke after 1 iteration
            self.assertEqual(mock_sleep.call_count, 1)
            self.assertEqual(mock_orchestrator_instance.get_job_status.call_count, 1)

    async def test_run_miami_audit_max_iterations(self):
        """Test the run_miami_audit script runs for exactly 10 iterations if status remains running."""
        with patch('src.scripts.run_miami_audit.TaskOrchestrator') as MockOrchestrator, \
             patch('asyncio.sleep', new_callable=AsyncMock) as mock_sleep:

            # Setup the mocked TaskOrchestrator
            mock_orchestrator_instance = MagicMock()
            MockOrchestrator.return_value = mock_orchestrator_instance

            # Mock the massive pipeline run
            mock_orchestrator_instance.run_massive_pipeline = AsyncMock(return_value="mock_job_id")

            # Mock get_job_status to always return "running"
            mock_orchestrator_instance.get_job_status = AsyncMock(return_value={
                "status": "running", "processed_count": 0, "total_count": 10, "current_phase": "Running"
            })

            # Run the script
            await run_miami_audit()

            # Verify the pipeline was triggered
            mock_orchestrator_instance.run_massive_pipeline.assert_awaited_once()

            # Verify it completed all 10 iterations exactly
            self.assertEqual(mock_sleep.call_count, 10)
            self.assertEqual(mock_orchestrator_instance.get_job_status.call_count, 10)

if __name__ == '__main__':
    unittest.main()
