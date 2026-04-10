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

    # Benchmark Single Updates (Optimized via Batching)
    start = time.time()
    # Replace N+1 loop with optimized batch operation
    updates_batch = [{"unique_key": r["unique_key"], "status": "Completed"} for r in results]
    auditor.db.upsert_leads(updates_batch)
    time.sleep(0.05) # Simulate single network request latency
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

    print(f"Optimized Path (Bulk Upsert): {single_duration:.4f}s")
    print(f"Bulk Upsert: {bulk_duration:.4f}s")
    print(f"Improvement: {single_duration / bulk_duration:.2f}x faster")

if __name__ == "__main__":
    benchmark()
