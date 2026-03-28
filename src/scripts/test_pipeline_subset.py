import asyncio
import os
import sys

# Ensure project root is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from src.core.task_orchestrator import TaskOrchestrator
from src.utils.supabase_helper import SupabaseHelper

async def test_subset():
    db = SupabaseHelper()
    orchestrator = TaskOrchestrator(max_concurrent=2)
    
    # Get 2 leads from new niches with websites
    resp = db.client.table("leads").select("unique_key,name,website") \
        .not_.is_("website", "null") \
        .not_.eq("website", "nan") \
        .in_("segment", ["HVAC Services", "Roofing Companies", "Personal Injury Lawyers"]) \
        .limit(2).execute()
        
    leads = resp.data if resp.data else []
    
    if not leads:
        print("❌ No leads found matching criteria for test.")
        return
        
    lead_ids = [l["unique_key"] for l in leads]
    print(f"🧪 Testing pipeline for: {[l['name'] for l in leads]}")
    
    # Run pipeline for these specific IDs
    job_id = await orchestrator.run_massive_pipeline(lead_ids=lead_ids, tasks=["audit", "enrich", "hunt"])
    print(f"🚀 Job started: {job_id}")
    
    # Monitor closely
    for i in range(10):
        await asyncio.sleep(20)
        status = await orchestrator.get_job_status(job_id)
        print(f"📊 [{i+1}/10] Status: {status.get('processed_count')}/{status.get('total_count')} - Phase: {status.get('current_phase')}")
        if status.get("status") == "completed":
            print("✅ Test batch completed successfully!")
            break
        if status.get("status") == "failed":
            print(f"❌ Test batch failed: {status.get('current_phase')}")
            break

if __name__ == "__main__":
    asyncio.run(test_subset())
