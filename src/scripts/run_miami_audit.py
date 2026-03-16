import asyncio
import os
import sys

# Ensure project root is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from src.core.task_orchestrator import TaskOrchestrator

async def run_miami_audit():
    orchestrator = TaskOrchestrator()
    
    # We want to run audit, enrich, and hunt (social_discovery)
    # The TaskOrchestrator handles resumes automatically if a job is already running, 
    # but we'll start a fresh one for these leads.
    
    print("🚀 Triggering massive pipeline for pending leads (including Miami Dentists)...")
    job_id = await orchestrator.run_massive_pipeline(tasks=["audit", "enrich", "hunt"])
    
    print(f"✅ Pipeline started. Job ID: {job_id}")
    print("⏳ Processing in background. Monitoring progress...")
    
    # Monitor for a bit
    for _ in range(10):
        await asyncio.sleep(15)
        status = await orchestrator.get_job_status(job_id)
        print(f"📊 Progress: {status.get('processed_count')}/{status.get('total_count')} - Phase: {status.get('current_phase')}")
        if status.get("status") in ["completed", "failed", "stopped"]:
            break

if __name__ == "__main__":
    asyncio.run(run_miami_audit())
