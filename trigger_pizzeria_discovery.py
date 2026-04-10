import asyncio
from src.core.task_orchestrator import TaskOrchestrator
from dotenv import load_dotenv

async def main():
    load_dotenv()
    orchestrator = TaskOrchestrator()
    job_id = await orchestrator.run_discovery_job("pizzeria", "Miami")
    print(f"Started discovery job: {job_id}")
    
    # Wait a bit to ensure it doesn't fail immediately
    await asyncio.sleep(5)
    status = await orchestrator.get_job_status(job_id)
    print(f"Job status after 5s: {status}")

if __name__ == "__main__":
    asyncio.run(main())
