import asyncio
import uuid
import random
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from typing import List, Dict, Any
from src.utils.supabase_helper import SupabaseHelper
from src.core.parallel_auditor import ParallelAuditor
from src.scrapers.enrichment_engine import EnrichmentEngine
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

class TaskOrchestrator:
    """
    Orchestrates large-scale lead processing jobs, including auditing and enrichment.
    Manages concurrency, state persistence in Supabase, and error recovery.
    """
    def __init__(self, max_concurrent: int = 10):
        self.db = SupabaseHelper()
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self._job_lock = asyncio.Lock()

    async def run_discovery_job(self, query: str, location: str = ""):
        """
        Starts a discovery search as a trackable orchestration job.
        """
        job_id = str(uuid.uuid4())
        job_data = {
            "id": job_id,
            "status": "starting",
            "total_count": 0,
            "processed_count": 0,
            "current_phase": "Initializing Discovery",
            "filters": {"query": query, "location": location}
        }
        self.db.client.table("orchestration_jobs").insert(job_data).execute()

        asyncio.create_task(self._process_discovery(job_id, query, location))
        return job_id

    async def _process_discovery(self, job_id: str, query: str, location: str):
        """
        Runs the discovery engine and updates job status.
        """
        from src.scrapers.discovery_engine import DiscoveryEngine
        engine = DiscoveryEngine()

        try:
            await self._update_job_status(job_id, {
                "status": "running",
                "current_phase": f"Searching for '{query}' in '{location}'..."
            })

            leads = await engine.find_leads(query, location)

            if leads and not any(l.get("status") == "CAPTCHA_REQUIRED" for l in leads):
                # Import leads
                self.db.upsert_leads(leads)
                await self._update_job_status(job_id, {
                    "status": "completed",
                    "current_phase": f"Discovery complete. Found {len(leads)} leads.",
                    "total_count": len(leads),
                    "processed_count": len(leads)
                })
            elif leads and any(l.get("status") == "CAPTCHA_REQUIRED" for l in leads):
                await self._update_job_status(job_id, {
                    "status": "failed",
                    "current_phase": "CAPTCHA Required - Manual intervention needed."
                })
            else:
                await self._update_job_status(job_id, {
                    "status": "completed",
                    "current_phase": "No leads found for this query.",
                    "total_count": 0,
                    "processed_count": 0
                })
        except Exception as e:
            logger.error("Discovery job %s failed: %s", job_id, e, exc_info=True)
            await self._update_job_status(job_id, {
                "status": "failed",
                "current_phase": f"Discovery failed: {str(e)}"
            })

    async def run_massive_pipeline(self, filters: Dict[str, Any] = None, lead_ids: List[str] = None, tasks: List[str] = None):
        """
        Main entry point for starting (or resuming) a lead processing job.
        """
        if tasks is None:
            tasks = ["audit", "enrich"]

        # Mapping social_discovery to its actual task sequence
        if "social_discovery" in tasks:
            tasks = ["audit", "enrich", "hunt"]

        async with self._job_lock:
            # 1. Check if there's already a running job
            if not lead_ids:
                existing_job = self.db.client.table("orchestration_jobs").select("*").eq("status", "running").limit(1).execute()
                if existing_job.data:
                    job_id = existing_job.data[0]["id"]
                    logger.info("Resuming existing job: %s", job_id)
                    asyncio.create_task(self._process_in_chunks(job_id, filters=filters, tasks=tasks))
                    return job_id

            # 2. Create new job record
            job_id = str(uuid.uuid4())
            job_data = {
                "id": job_id,
                "status": "starting",
                "total_count": len(lead_ids) if lead_ids else 0,
                "processed_count": 0,
                "current_phase": "initialization",
                "filters": filters
            }
            self.db.client.table("orchestration_jobs").insert(job_data).execute()

        # 3. Start background task (outside lock)
        asyncio.create_task(self._process_in_chunks(job_id, filters=filters, lead_ids=lead_ids, tasks=tasks))

        return job_id

    async def _update_job_status(self, job_id: str, updates: Dict[str, Any]):
        """
        Helper method to update the persistent state of a job in the Supabase database.
        """
        self.db.client.table("orchestration_jobs").update(updates).eq("id", job_id).execute()

    async def _process_in_chunks(self, job_id: str, **kwargs):
        """
        Processes leads in chunks with batch updates and centralized concurrency.
        """
        filters = kwargs.get("filters")
        lead_ids = kwargs.get("lead_ids")
        tasks = kwargs.get("tasks")
        chunk_size = kwargs.get("chunk_size", 50)

        auditor = ParallelAuditor()
        enricher = EnrichmentEngine()

        if tasks is None:
            tasks = ["audit", "enrich"]

        try:
            # 1. Get total leads count
            if lead_ids:
                total_leads = len(lead_ids)
            else:
                query = self.db.client.table("leads").select("unique_key", count="exact")
                query = query.or_("audit_status.neq.Completed,enrichment_status.neq.COMPLETED").lt("retry_count", 3)

                if filters:
                    for k, v in filters.items():
                        query = query.eq(k, v)

                response = query.execute()
                total_leads = response.count if hasattr(response, 'count') else 0

            await self._update_job_status(job_id, {
                "status": "running",
                "total_count": total_leads,
                "current_phase": "Initializing Pipeline"
            })

            processed_count = 0
            consecutive_failures = 0

            # Check for crash recovery - resume from last checkpoint
            job_status = await self.get_job_status(job_id)
            if job_status.get("processed_count", 0) > 0 and not lead_ids:
                processed_count = job_status["processed_count"]
                logger.info("Resuming from checkpoint: %d already processed", processed_count)

            while True:
                status_check = await self.get_job_status(job_id)
                if status_check.get("status") in ["stopped", "failed"]:
                    return

                # Fetch next chunk
                if lead_ids:
                    slice_start = processed_count
                    slice_end = min(processed_count + chunk_size, total_leads)
                    if slice_start >= total_leads:
                        break

                    current_ids = lead_ids[slice_start:slice_end]
                    chunk_resp = self.db.client.table("leads").select("*").in_("unique_key", current_ids).execute()
                else:
                    chunk_resp = self.db.client.table("leads").select("*") \
                        .or_("audit_status.neq.Completed,enrichment_status.neq.COMPLETED") \
                        .lt("retry_count", 3) \
                        .order("last_processed_at", nullsfirst=True) \
                        .limit(chunk_size).execute()

                chunk = chunk_resp.data if chunk_resp.data else []
                if not chunk:
                    break

                await self._update_job_status(job_id, {"current_phase": f"Processing batch ({processed_count}/{total_leads})"})

                # Process chunk items with semaphore
                tasks_list = [self._process_single_lead(lead, auditor, enricher, tasks) for lead in chunk]
                results = await asyncio.gather(*tasks_list, return_exceptions=True)

                # Batch update Supabase
                leads_to_upsert = []
                batch_success = False
                for res in results:
                    if isinstance(res, dict) and 'unique_key' in res:
                        leads_to_upsert.append(res)
                        if not res.get('last_error'):
                            batch_success = True
                    elif isinstance(res, Exception):
                        logger.error("Task exception: %s", res)

                if leads_to_upsert:
                    self.db.upsert_leads(leads_to_upsert)

                if not batch_success and len(chunk) > 0:
                    consecutive_failures += 1
                else:
                    consecutive_failures = 0

                if consecutive_failures >= 5:
                    raise Exception("5 consecutive batches failed completely.")

                processed_count += len(chunk)
                await self._update_job_status(job_id, {"processed_count": processed_count})

                base_wait = 2
                if consecutive_failures > 0:
                    wait_time = min(base_wait * (2 ** consecutive_failures) + random.uniform(0, 2), 120)
                else:
                    wait_time = base_wait + random.uniform(0, 1)
                await asyncio.sleep(wait_time)

            await self._update_job_status(job_id, {
                "status": "completed",
                "current_phase": "Finished",
                "processed_count": total_leads
            })

        except Exception as e:
            logger.error("Fatal pipeline error for job %s: %s", job_id, e, exc_info=True)
            await self._update_job_status(job_id, {
                "status": "failed",
                "current_phase": f"Error: {str(e)}"
            })
            raise e

    async def _process_single_lead(self, lead: Dict[str, Any], auditor: ParallelAuditor, enricher: EnrichmentEngine, tasks: List[str] = None) -> Dict[str, Any]:
        """
        Processes a single lead and returns the updated object (Internal only).
        """
        if tasks is None:
            tasks = ["audit", "enrich"]

        lead_id = lead.get('unique_key')
        updated_lead = lead.copy()

        try:
            async with self.semaphore:
                # Phase 1: Audit
                if "audit" in tasks:
                    audit_res = await auditor.audit_single_lead(lead)
                    if audit_res.get("status") == "Failed":
                        raise Exception(f"Audit failed: {audit_res.get('error')}")
                    # Update audit results in local object
                    updated_lead.update({
                        "audit_status": "Completed",
                        "audit_results": audit_res.get("result")
                    })
                    # Also update seo_score and high_risk_flag if present
                    if "result" in audit_res:
                        res = audit_res["result"]
                        updated_lead["seo_score"] = res.get("score")
                        updated_lead["high_risk_flag"] = res.get("high_risk_flag")
                        updated_lead["pain_points"] = res.get("pain_points")
                        updated_lead["linkedin_hook"] = res.get("linkedin_hook")
                        updated_lead["email_hook"] = res.get("email_hook")

                        if res.get("emails") and not updated_lead.get("email"):
                            updated_lead["email"] = res["emails"][0]

                # Phase 2: Enrichment
                if "enrich" in tasks:
                    enrichment_res = await enricher.enrich_lead(updated_lead)
                    updated_lead.update(enrichment_res)

                # Phase 3: Hunting (Deep social discovery)
                if "hunt" in tasks:
                    hunt_res = await auditor.hunt_single_lead(updated_lead)
                    if hunt_res.get("status") == "Completed":
                        updated_lead.update({
                            "facebook": hunt_res.get("facebook"),
                            "instagram": hunt_res.get("instagram"),
                            "linkedin": hunt_res.get("linkedin"),
                            "tiktok": hunt_res.get("tiktok"),
                            "pinterest": hunt_res.get("pinterest"),
                            "phone": hunt_res.get("phone"),
                            "company_name": hunt_res.get("company_name")
                        })

                        if hunt_res.get("email") and not updated_lead.get("email"):
                            updated_lead["email"] = hunt_res["email"]

                        if hunt_res.get("enrichment_data"):
                            updated_lead.update(hunt_res["enrichment_data"])

                # Success cleanup
                updated_lead.update({
                    "last_error": None,
                    "retry_count": 0,
                    "last_processed_at": datetime.now(timezone.utc).isoformat()
                })
                return updated_lead

        except Exception as e:
            logger.error("Error processing lead %s: %s", lead_id, e, exc_info=True)
            retry_count = (lead.get("retry_count") or 0) + 1
            updated_lead.update({
                "last_error": str(e),
                "retry_count": retry_count,
                "audit_status": "Failed" if retry_count >= 3 else lead.get("audit_status"),
                "last_processed_at": datetime.now(timezone.utc).isoformat()
            })
            return updated_lead

    async def get_job_status(self, job_id: str):
        response = self.db.client.table("orchestration_jobs").select("*").eq("id", job_id).execute()
        return response.data[0] if response.data else {"status": "not_found"}

    async def stop_job(self, job_id: str):
        await self._update_job_status(job_id, {"status": "stopped", "current_phase": "Stopped by user"})
        return {"status": "stopping", "job_id": job_id}

    async def ingest_leads_from_csv(self, csv_path: str, merge_with_local: bool = True):
        """
        Ingests leads from a CSV, deduplicates, and merges with existing records.
        """
        from src.utils.csv_helper import load_csv_with_unique_key, save_csv

        # 1. Load New Leads
        df_new = load_csv_with_unique_key(csv_path, "New Upload")
        if df_new.empty:
            return {"status": "error", "message": "CSV is empty or invalid."}

        # 2. Sync with existing Supabase records for deduplication
        existing_res = self.db.client.table("leads").select("unique_key,email,audit_status").execute()
        existing_keys = {r['unique_key'] for r in existing_res.data} if existing_res.data else set()

        # 3. Mark as New or Merge
        df_new['is_new'] = ~df_new['unique_key'].isin(existing_keys)

        # 4. Final list for upsert
        leads_list = df_new.to_dict('records')
        self.db.upsert_leads(leads_list)

        # 5. Local File Governance (for user's comfort with CSVs)
        if merge_with_local:
            # Re-fetch everything to ensure we have the full updated set
            full_res = self.db.client.table("leads").select("*").execute()
            df_full = pd.DataFrame(full_res.data)

            if not df_full.empty:
                # Standardize columns for filtering
                if 'email' in df_full.columns:
                    df_full['email'] = df_full['email'].replace(['', 'nan', 'None'], np.nan)

                # Split and Save like Colab
                df_with_email = df_full[df_full['email'].notna()]
                df_no_email = df_full[df_full['email'].isna()]

                save_csv(df_with_email, 'FINALNA_LISTA_SA_EMAILOM.csv')
                save_csv(df_no_email, 'LEADOVI_BEZ_EMAILA.csv')

        return {
            "status": "success",
            "total_ingested": len(df_new),
            "new_leads": int(df_new['is_new'].sum())
        }

    async def recover_interrupted_jobs(self):
        response = self.db.client.table("orchestration_jobs") \
            .select("id", "updated_at") \
            .in_("status", ["starting", "running"]) \
            .execute()

        now = datetime.now(timezone.utc)
        for job in response.data:
            updated_at = datetime.fromisoformat(job["updated_at"].replace('Z', '+00:00'))
            if (now - updated_at).total_seconds() > 600:
                await self._update_job_status(job["id"], {
                    "status": "failed",
                    "current_phase": "Process timed out or was interrupted. Please restart."
                })
