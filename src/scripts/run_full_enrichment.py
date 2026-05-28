import asyncio
import os
import sys

# Ensure project root is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.core.task_orchestrator import TaskOrchestrator
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


async def run_full_enrichment():
    """
    Triggers the TaskOrchestrator to process all pending leads in Supabase.
    Performs Audit, Enrichment, and Social Discovery.
    """
    orchestrator = TaskOrchestrator()

    print(
        "🚀 Triggering massive processing pipeline for ALL pending/incomplete leads..."
    )

    # We want to run audit, enrich, and hunt (social_discovery)
    # The TaskOrchestrator handles resumes automatically if a job is already running.
    # We filter for leads that need processing or have low retry counts.

    job_id = await orchestrator.run_massive_pipeline(tasks=["audit", "enrich", "hunt"])

    print(f"✅ Full Pipeline Job Started. ID: {job_id}")
    print("⏳ Processing in background. Monitoring progress for summary...")

    # Monitor for 20 iterations (approx 5 mins total)
    for i in range(20):
        await asyncio.sleep(15)
        status = await orchestrator.get_job_status(job_id)

        processed = status.get("processed_count", 0)
        total = status.get("total_count", 0)
        phase = status.get("current_phase", "Syncing")
        state = status.get("status", "running")

        print(
            f"📊 [{i + 1}/20] Status: {processed}/{total} processed. Current Phase: {phase}"
        )

        if state in ["completed", "failed", "stopped"]:
            print(f"🏁 Job finished with status: {state}")
            break

    print(
        "\n💡 Tip: You can check the 'orchestration_jobs' table in Supabase for real-time progress."
    )


if __name__ == "__main__":
    asyncio.run(run_full_enrichment())
