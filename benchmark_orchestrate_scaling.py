import asyncio
import time
from unittest.mock import MagicMock
from src.core.parallel_auditor import ParallelAuditor

async def run_benchmark():
    auditor = ParallelAuditor(max_concurrent=5)

    # Mock the database
    auditor.db.client = MagicMock()

    # Mock get_pending_leads to return a large batch
    batch_size = 500
    leads_batch = []
    for i in range(batch_size):
        leads_batch.append({
            "unique_key": f"test_{i}",
            "website": "https://example.com",
            "name": f"Test Business {i}"
        })

    def get_pending_leads_side_effect():
        if not hasattr(get_pending_leads_side_effect, 'called'):
            get_pending_leads_side_effect.called = True
            mock_resp = MagicMock()
            mock_resp.data = leads_batch
            return mock_resp
        else:
            mock_resp = MagicMock()
            mock_resp.data = []
            return mock_resp

    auditor.db.get_pending_leads = get_pending_leads_side_effect

    # Mock upsert_leads to measure calls
    auditor.db.upsert_leads = MagicMock()

    # Mock run_batch to simulate fast audit
    async def mock_run_batch(batch, task_type):
        # Return generic payload dicts similar to what real workers return
        return [{"unique_key": b["unique_key"], "status": "Completed", "score": 100, "emails": ["test@test.com"]} for b in batch]
    auditor.run_batch = mock_run_batch

    # Disable asyncio.sleep so benchmark runs fast
    original_sleep = asyncio.sleep
    async def fast_sleep(seconds):
        pass
    asyncio.sleep = fast_sleep

    start_time = time.time()
    await auditor.orchestrate_scaling(chunk_size=100, task_type="audit")
    end_time = time.time()

    # Restore sleep
    asyncio.sleep = original_sleep

    print(f"Time taken: {end_time - start_time:.4f}s")
    print(f"upsert_leads calls: {auditor.db.upsert_leads.call_count}")

if __name__ == '__main__':
    asyncio.run(run_benchmark())
