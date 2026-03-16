import asyncio
from src.core.parallel_auditor import ParallelAuditor
import pandas as pd
import os

async def test_scaling_logic():
    print("--- Starting Scaling Engine Stress Test ---")
    
    # 1. Generate 100 dummy leads (simulating a large batch)
    dummy_leads = []
    for i in range(1, 101):
        dummy_leads.append({
            "unique_key": f"test_{i}",
            "website": "https://example.com", # Speed up test with same URL
            "name": f"Test Business {i}"
        })
    
    # 2. Initialize Auditor with low concurrency to test chunking
    auditor = ParallelAuditor(max_concurrent=5)
    
    print(f"Auditing {len(dummy_leads)} leads in parallel (5 at a time)...")
    start_time = asyncio.get_event_loop().time()
    
    # We run run_batch directly instead of orchestrate_scaling to avoid needing Supabase connection for this unit test
    results = await auditor.run_batch(dummy_leads)
    
    end_time = asyncio.get_event_loop().time()
    
    # 3. Verify results
    completed = [r for r in results if r["status"] == "Completed"]
    failed = [r for r in results if r["status"] == "Failed"]
    
    print(f"\nTest Summary:")
    print(f"- Total Leads: {len(dummy_leads)}")
    print(f"- Successfully Audited: {len(completed)}")
    print(f"- Failed: {len(failed)}")
    print(f"- Time Taken: {end_time - start_time:.2f} seconds")
    
    assert len(completed) > 0, "No leads were successfully audited"
    print("\n--- Stress Test Passed! ---")

if __name__ == "__main__":
    asyncio.run(test_scaling_logic())
