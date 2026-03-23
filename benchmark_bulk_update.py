import time
from unittest.mock import MagicMock
from src.core.parallel_auditor import ParallelAuditor
from src.utils.supabase_helper import SupabaseHelper

def benchmark():
    auditor = ParallelAuditor()
    auditor.db = MagicMock()
    # Mocking single update
    auditor.db.update_lead_info = MagicMock()
    # Mocking bulk update
    auditor.db.upsert_leads = MagicMock()

    # Generate dummy results
    results = [{"unique_key": f"key_{i}", "status": "Completed", "result": {"score": 100}} for i in range(100)]

    # Benchmark Single Updates (Baseline)
    start = time.time()
    for r in results:
        auditor.db.update_lead_info(r["unique_key"], {"status": "Completed"})
        # Let's add artificial delay to simulate network latency for 100 requests (e.g., 10ms each)
        time.sleep(0.01)
    end = time.time()
    single_duration = end - start

    # Benchmark Bulk Upsert
    start = time.time()
    updates = [{"unique_key": r["unique_key"], "status": "Completed"} for r in results]
    auditor.db.upsert_leads(updates)
    # Simulate single network request latency
    time.sleep(0.05)
    end = time.time()
    bulk_duration = end - start

    print(f"Baseline (Single Updates): {single_duration:.4f}s")
    print(f"Bulk Upsert: {bulk_duration:.4f}s")
    print(f"Improvement: {single_duration / bulk_duration:.2f}x faster")

if __name__ == "__main__":
    benchmark()
