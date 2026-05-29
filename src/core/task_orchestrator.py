import asyncio
import uuid
import random
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from src.utils.supabase_helper import SupabaseHelper
from src.core.parallel_auditor import ParallelAuditor
from src.scrapers.enrichment_engine import EnrichmentEngine
from src.utils.logging_config import get_logger
from src.utils.stats_cache import stats_cache

logger = get_logger(__name__)

# Allowlist of columns callers may filter leads by. Mirrors the API-layer
# allowlist in backend/main.py — keep in sync. Anything outside this set is
# silently dropped (with a warning) so an attacker cannot probe arbitrary
# DB columns via PostgREST error messages or bypass segment scoping.
_LEAD_FILTER_ALLOWLIST = frozenset(
    {
        "segment",
        "audit_status",
        "enrichment_status",
        "high_risk_flag",
        "company_size",
        "campaign_id",
        "country",
        "city",
        "language",
        "outreach_score",
        "seo_score",
    }
)


def _sanitize_filters(filters: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not filters:
        return {}
    safe = {}
    for k, v in filters.items():
        if k in _LEAD_FILTER_ALLOWLIST:
            safe[k] = v
        else:
            logger.warning("Dropping disallowed lead filter key: %r", k)
    return safe


class TaskOrchestrator:
    """
    Orchestrates large-scale lead processing jobs, including auditing and enrichment.
    Manages concurrency, state persistence in Supabase, and error recovery.
    """

    def __init__(self, max_concurrent: int = 10):
        self.db = SupabaseHelper()
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self._job_lock = asyncio.Lock()
        # Discovery launches a fresh Chromium per call (Google Maps ~400-550 MB
        # peak). Two parallel /discovery/start calls on a 512 MB Render starter
        # OOM-killed the container twice on 2026-05-28 (14:46:03 / 14:51:25).
        # Serialise discovery so only one Chromium runs at a time per worker.
        self._discovery_sem = asyncio.Semaphore(1)
        # Active auditor + enricher per job_id, so stop_job can propagate a
        # cooperative cancel into the gather currently running inside
        # _process_in_chunks. Without this map, stop_job only flipped the DB
        # row to status=stopped and the in-flight gather completed regardless.
        self._active_auditors: Dict[str, ParallelAuditor] = {}

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
            "filters": {"query": query, "location": location},
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

        async with self._discovery_sem:
            try:
                await self._update_job_status(
                    job_id,
                    {
                        "status": "running",
                        "current_phase": f"Searching for '{query}' in '{location}'...",
                    },
                )

                leads = await engine.find_leads(query, location)

                if leads and not any(l.get("status") == "CAPTCHA_REQUIRED" for l in leads):
                    # Import leads
                    self.db.upsert_leads(leads)
                    await self._update_job_status(
                        job_id,
                        {
                            "status": "completed",
                            "current_phase": f"Discovery complete. Found {len(leads)} leads.",
                            "total_count": len(leads),
                            "processed_count": len(leads),
                        },
                    )
                elif leads and any(l.get("status") == "CAPTCHA_REQUIRED" for l in leads):
                    await self._update_job_status(
                        job_id,
                        {
                            "status": "failed",
                            "current_phase": "CAPTCHA Required - Manual intervention needed.",
                        },
                    )
                else:
                    await self._update_job_status(
                        job_id,
                        {
                            "status": "completed",
                            "current_phase": "No leads found for this query.",
                            "total_count": 0,
                            "processed_count": 0,
                        },
                    )
            except Exception as e:
                logger.error("Discovery job %s failed: %s", job_id, e, exc_info=True)
                await self._update_job_status(
                    job_id,
                    {"status": "failed", "current_phase": f"Discovery failed: {str(e)}"},
                )

    async def run_massive_pipeline(
        self,
        filters: Dict[str, Any] = None,
        lead_ids: List[str] = None,
        tasks: List[str] = None,
    ):
        """
        Main entry point for starting (or resuming) a lead processing job.
        """
        if tasks is None:
            tasks = ["audit", "enrich"]

        # Mapping social_discovery to its actual task sequence
        if "social_discovery" in tasks:
            tasks = ["audit", "enrich", "hunt"]

        async with self._job_lock:
            # 1. Check if there's already a running job. /process-lead and
            #    /process-all both flow through here on the request thread,
            #    so the PostgREST round-trip must NOT block the uvicorn
            #    event loop — see SupabaseHelper.find_running_job /
            #    insert_orchestration_job for the asyncio.to_thread hop.
            if not lead_ids:
                rows = await self.db.find_running_job()
                if rows:
                    job_id = rows[0]["id"]
                    logger.info("Resuming existing job: %s", job_id)
                    asyncio.create_task(
                        self._process_in_chunks(job_id, filters=filters, tasks=tasks)
                    )
                    return job_id

            # 2. Create new job record
            job_id = str(uuid.uuid4())
            job_data = {
                "id": job_id,
                "status": "starting",
                "total_count": len(lead_ids) if lead_ids else 0,
                "processed_count": 0,
                "current_phase": "initialization",
                # Defensive normalize: the JSONB shape gate
                # (check_jsonb_shapes.py) requires `filters` to be NULL
                # OR match {type:str} OR {query:str, location:str}.
                # Empty `{}` matches neither — would land as a future
                # orphan row that fails the daily gate. Callers
                # passing None / {} / [] / falsy-empty collapse to
                # NULL (accepted by the gate's NULL-tolerant branch).
                # Genuine pipeline / discovery callers still pass
                # their populated dict and remain unchanged.
                "filters": filters if filters else None,
            }
            await self.db.insert_orchestration_job(job_data)

        # 3. Start background task (outside lock)
        asyncio.create_task(
            self._process_in_chunks(
                job_id, filters=filters, lead_ids=lead_ids, tasks=tasks
            )
        )

        return job_id

    async def _update_job_status(self, job_id: str, updates: Dict[str, Any]):
        """
        Helper method to update the persistent state of a job in the Supabase database.
        """
        self.db.client.table("orchestration_jobs").update(updates).eq(
            "id", job_id
        ).execute()

    @staticmethod
    def _status_predicates_for_tasks(tasks: Optional[List[str]]) -> str:
        """Comma-joined OR predicate for PostgREST `.or_(...)`. Selects leads
        where AT LEAST ONE requested task still needs to run.

        Without task-awareness, a job started with ``tasks=['audit']`` re-fetches
        every lead whose ``enrichment_status`` is still ``PENDING`` even after
        ``audit_status`` flipped to ``Completed`` — and re-bills Gemini for the
        audit it already finished. The Phase 9.10 live-pipeline run
        (PR #274, Finding A) caught this multiplying spend by 3× per lead.

        Status values written by the engines:
        - ``audit_status``: 'Pending' → 'Completed' or 'Failed'
          (plus a handful of error-reason variants the schema allowlists).
        - ``enrichment_status``: 'PENDING' → 'COMPLETED' / 'FAILED'
          / 'FAILED_NO_CONTENT'.
        - 'hunt' has no dedicated status column today; it populates social
          fields directly. Fall back to a broad predicate when only hunt
          is requested.
        """
        tasks = list(tasks or ["audit", "enrich"])
        parts: List[str] = []
        if "audit" in tasks:
            parts.append("audit_status.not.in.(Completed,Failed)")
        if "enrich" in tasks:
            parts.append(
                "enrichment_status.not.in.(COMPLETED,FAILED,FAILED_NO_CONTENT)"
            )
        if "hunt" in tasks and not parts:
            # No hunt_status column; fall back to the historical predicate.
            parts.append("audit_status.neq.Completed")
            parts.append("enrichment_status.neq.COMPLETED")
        if not parts:
            # Unknown tasks list — fail-safe to the historical predicate so we
            # don't accidentally select every row in the table.
            parts.append("audit_status.neq.Completed")
            parts.append("enrichment_status.neq.COMPLETED")
        return ",".join(parts)

    def _get_total_leads(
        self,
        lead_ids: List[str],
        filters: Dict[str, Any],
        tasks: Optional[List[str]] = None,
    ) -> int:
        """Count total leads to process, either from explicit IDs or via DB query."""
        if lead_ids:
            return len(lead_ids)

        query = self.db.client.table("leads").select("unique_key", count="exact")
        query = query.or_(self._status_predicates_for_tasks(tasks)).lt("retry_count", 3)

        for k, v in _sanitize_filters(filters).items():
            query = query.eq(k, v)

        response = query.execute()
        return response.count if hasattr(response, "count") else 0

    def _fetch_chunk(
        self,
        lead_ids: List[str],
        processed_count: int,
        chunk_size: int,
        total_leads: int,
        tasks: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch the next chunk of leads from DB or explicit ID list."""
        if lead_ids:
            slice_start = processed_count
            slice_end = min(processed_count + chunk_size, total_leads)
            if slice_start >= total_leads:
                return []

            current_ids = lead_ids[slice_start:slice_end]
            chunk_resp = (
                self.db.client.table("leads")
                .select("*")
                .in_("unique_key", current_ids)
                .execute()
            )
        else:
            chunk_resp = (
                self.db.client.table("leads")
                .select("*")
                .or_(self._status_predicates_for_tasks(tasks))
                .lt("retry_count", 3)
                .order("last_processed_at", nullsfirst=True)
                .limit(chunk_size)
                .execute()
            )

        return chunk_resp.data if chunk_resp.data else []

    async def _process_and_upsert_chunk(
        self,
        chunk: List[Dict[str, Any]],
        auditor: ParallelAuditor,
        enricher: EnrichmentEngine,
        tasks: List[str],
    ) -> bool:
        """Process a chunk of leads concurrently and batch-upsert results. Returns True if any succeeded."""
        tasks_list = [
            self._process_single_lead(lead, auditor, enricher, tasks) for lead in chunk
        ]
        results = await asyncio.gather(*tasks_list, return_exceptions=True)

        leads_to_upsert = []
        batch_success = False
        for res in results:
            if isinstance(res, dict) and "unique_key" in res:
                leads_to_upsert.append(res)
                if not res.get("last_error"):
                    batch_success = True
            elif isinstance(res, asyncio.CancelledError):
                # Operator stop: lead was mid-flight; don't write a Failed
                # row (it was never given a fair attempt). Leaving the row
                # at its prior state lets a retry pick it up clean.
                logger.info("Lead cancelled by stop request — leaving row untouched.")
            elif isinstance(res, Exception):
                logger.error("Task exception: %s", res)

        if leads_to_upsert:
            self.db.upsert_leads(leads_to_upsert)

        return batch_success

    def _calculate_wait_time(self, consecutive_failures: int) -> float:
        """Calculate wait time with exponential backoff on failures."""
        base_wait = 2
        if consecutive_failures > 0:
            return min(
                base_wait * (2**consecutive_failures) + random.uniform(0, 2), 120
            )
        return base_wait + random.uniform(0, 1)

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

        # Register so stop_job(job_id) can call auditor.stop() and trigger the
        # cooperative cancel inside audit_single_lead / hunt_single_lead.
        self._active_auditors[job_id] = auditor

        if tasks is None:
            tasks = ["audit", "enrich"]

        try:
            total_leads = self._get_total_leads(lead_ids, filters, tasks=tasks)

            await self._update_job_status(
                job_id,
                {
                    "status": "running",
                    "total_count": total_leads,
                    "current_phase": "Initializing Pipeline",
                },
            )

            processed_count = 0
            consecutive_failures = 0

            # Check for crash recovery - resume from last checkpoint
            job_status = await self.get_job_status(job_id)
            if job_status.get("processed_count", 0) > 0 and not lead_ids:
                processed_count = job_status["processed_count"]
                logger.info(
                    "Resuming from checkpoint: %d already processed", processed_count
                )

            while True:
                status_check = await self.get_job_status(job_id)
                if status_check.get("status") in ["stopped", "failed"]:
                    return

                chunk = self._fetch_chunk(
                    lead_ids, processed_count, chunk_size, total_leads, tasks=tasks
                )
                if not chunk:
                    break

                await self._update_job_status(
                    job_id,
                    {
                        "current_phase": f"Processing batch ({processed_count}/{total_leads})"
                    },
                )

                batch_success = await self._process_and_upsert_chunk(
                    chunk, auditor, enricher, tasks
                )

                if not batch_success and len(chunk) > 0:
                    consecutive_failures += 1
                else:
                    consecutive_failures = 0

                if consecutive_failures >= 5:
                    raise Exception("5 consecutive batches failed completely.")

                processed_count += len(chunk)
                await self._update_job_status(
                    job_id, {"processed_count": processed_count}
                )

                wait_time = self._calculate_wait_time(consecutive_failures)
                await asyncio.sleep(wait_time)

            await self._update_job_status(
                job_id,
                {
                    "status": "completed",
                    "current_phase": "Finished",
                    "processed_count": total_leads,
                },
            )

        except Exception as e:
            logger.error(
                "Fatal pipeline error for job %s: %s", job_id, e, exc_info=True
            )
            await self._update_job_status(
                job_id, {"status": "failed", "current_phase": f"Error: {str(e)}"}
            )
            raise e
        finally:
            # Unregister the auditor so stop_job stops finding a stale reference
            # after the job has exited. pop with default to be defensive against
            # double-finally invocations or unexpected job_id collisions.
            self._active_auditors.pop(job_id, None)
            # Tear down the shared Chromium process the enricher held open
            # across the batch. Without this the playwright Node subprocess
            # outlives the job and accumulates with every pipeline run.
            try:
                await enricher.aclose()
            except Exception as exc:  # noqa: BLE001 — must not mask job result
                logger.warning("EnrichmentEngine.aclose raised: %s", exc)
            # Lead rows are written chunk-by-chunk, so even a failed/stopped
            # job has mutated audit_status / seo_score / lead_source on at
            # least a partial chunk. Drop the cached /stats payload so the
            # next request sees fresh aggregations.
            stats_cache.invalidate()

    async def _process_single_lead(
        self,
        lead: Dict[str, Any],
        auditor: ParallelAuditor,
        enricher: EnrichmentEngine,
        tasks: List[str] = None,
    ) -> Dict[str, Any]:
        """
        Processes a single lead and returns the updated object (Internal only).
        """
        if tasks is None:
            tasks = ["audit", "enrich"]

        lead_id = lead.get("unique_key")
        updated_lead = lead.copy()

        try:
            async with self.semaphore:
                # Phase 1: Audit
                if "audit" in tasks:
                    audit_res = await auditor.audit_single_lead(lead)
                    if audit_res.get("status") == "Failed":
                        raise Exception(f"Audit failed: {audit_res.get('error')}")
                    # Update audit results in local object
                    updated_lead.update(
                        {
                            "audit_status": "Completed",
                            "audit_results": audit_res.get("result"),
                        }
                    )
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
                        updated_lead.update(
                            {
                                "facebook": hunt_res.get("facebook"),
                                "instagram": hunt_res.get("instagram"),
                                "linkedin": hunt_res.get("linkedin"),
                                "tiktok": hunt_res.get("tiktok"),
                                "pinterest": hunt_res.get("pinterest"),
                                "phone": hunt_res.get("phone"),
                                "company_name": hunt_res.get("company_name"),
                            }
                        )

                        if hunt_res.get("email") and not updated_lead.get("email"):
                            updated_lead["email"] = hunt_res["email"]

                        if hunt_res.get("enrichment_data"):
                            updated_lead.update(hunt_res["enrichment_data"])

                # Success cleanup
                updated_lead.update(
                    {
                        "last_error": None,
                        "retry_count": 0,
                        "last_processed_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                return updated_lead

        except Exception as e:
            logger.error("Error processing lead %s: %s", lead_id, e, exc_info=True)
            retry_count = (lead.get("retry_count") or 0) + 1
            updated_lead.update(
                {
                    "last_error": str(e),
                    "retry_count": retry_count,
                    "audit_status": "Failed"
                    if retry_count >= 3
                    else lead.get("audit_status"),
                    "last_processed_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            return updated_lead

    @staticmethod
    def _is_valid_uuid(value: str) -> bool:
        """`orchestration_jobs.id` is a UUID column. PostgREST rejects a
        non-UUID `.eq("id", ...)` filter with an APIError that bubbles to
        a generic 500. Validating the format here turns an attacker
        probing `/orchestrator/status/notauuid` into a clean `not_found`
        instead of a 500 (OWASP API8 — improper error handling)."""
        try:
            uuid.UUID(str(value))
            return True
        except (ValueError, TypeError, AttributeError):
            return False

    async def get_job_status(self, job_id: str):
        if not self._is_valid_uuid(job_id):
            return {"status": "not_found"}
        response = (
            self.db.client.table("orchestration_jobs")
            .select("*")
            .eq("id", job_id)
            .execute()
        )
        return response.data[0] if response.data else {"status": "not_found"}

    async def stop_job(self, job_id: str):
        if not self._is_valid_uuid(job_id):
            return {"status": "not_found", "job_id": job_id}
        # Mark the DB row first so the outer chunk loop in _process_in_chunks
        # bails before fetching the next chunk.
        await self._update_job_status(
            job_id, {"status": "stopped", "current_phase": "Stopped by user"}
        )
        # Propagate the stop into the active auditor so any audit/hunt
        # coroutine currently mid-flight raises CancelledError at its next
        # cooperative checkpoint, instead of running to completion (the
        # B9 race in E2E_TEST_REPORT.md).
        active = self._active_auditors.get(job_id)
        if active is not None:
            active.stop()
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
        existing_res = (
            self.db.client.table("leads")
            .select("unique_key,email,audit_status")
            .execute()
        )
        existing_keys = (
            {r["unique_key"] for r in existing_res.data} if existing_res.data else set()
        )

        # 3. Mark as New or Merge
        df_new["is_new"] = ~df_new["unique_key"].isin(existing_keys)

        # 4. Final list for upsert
        leads_list = df_new.to_dict("records")
        self.db.upsert_leads(leads_list)

        # 5. Local File Governance (for user's comfort with CSVs)
        if merge_with_local:
            # Re-fetch everything to ensure we have the full updated set
            full_res = self.db.client.table("leads").select("*").execute()
            df_full = pd.DataFrame(full_res.data)

            if not df_full.empty:
                # Standardize columns for filtering
                if "email" in df_full.columns:
                    df_full["email"] = df_full["email"].replace(
                        ["", "nan", "None"], np.nan
                    )

                # Split and Save like Colab
                df_with_email = df_full[df_full["email"].notna()]
                df_no_email = df_full[df_full["email"].isna()]

                save_csv(df_with_email, "FINALNA_LISTA_SA_EMAILOM.csv")
                save_csv(df_no_email, "LEADOVI_BEZ_EMAILA.csv")

        return {
            "status": "success",
            "total_ingested": len(df_new),
            "new_leads": int(df_new["is_new"].sum()),
        }

    async def recover_interrupted_jobs(self):
        response = (
            self.db.client.table("orchestration_jobs")
            .select("id", "updated_at")
            .in_("status", ["starting", "running"])
            .execute()
        )

        now = datetime.now(timezone.utc)
        for job in response.data:
            updated_at = datetime.fromisoformat(
                job["updated_at"].replace("Z", "+00:00")
            )
            if (now - updated_at).total_seconds() > 600:
                await self._update_job_status(
                    job["id"],
                    {
                        "status": "failed",
                        "current_phase": "Process timed out or was interrupted. Please restart.",
                    },
                )
