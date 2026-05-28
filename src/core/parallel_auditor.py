import asyncio
from typing import List, Dict
from src.scrapers.seo_audit import perform_seo_audit_async
from src.utils.supabase_helper import SupabaseHelper
from src.processors.leadhunter import LeadHunter
from src.utils.logging_config import get_logger
import time

logger = get_logger(__name__)


class ParallelAuditor:
    def __init__(self, max_concurrent: int = 20):
        self.max_concurrent = max_concurrent
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.db = SupabaseHelper()
        self.status = {
            "active": False,
            "total": 0,
            "processed": 0,
            "completed": 0,
            "failed": 0,
            "current_chunk": 0,
        }

    def reset_status(self, total_pending: int):
        self.status = {
            "active": True,
            "total": total_pending,
            "processed": 0,
            "completed": 0,
            "failed": 0,
            "current_chunk": 0,
            "stop_requested": False,
        }

    def stop(self):
        self.status["stop_requested"] = True
        self.status["active"] = False

    def _raise_if_stop_requested(self) -> None:
        """Cooperative cancellation point.

        Active audit/hunt coroutines call this between awaits so a STOP
        click can actually interrupt an in-flight job (not just prevent the
        next chunk from starting). Raises CancelledError, which `_process_
        and_upsert_chunk` and `run_batch` swallow via `gather(..., return_
        exceptions=True)`.
        """
        if self.status.get("stop_requested"):
            raise asyncio.CancelledError("stop requested by operator")

    async def _scrape_business_details(self, hunter: LeadHunter, lead: Dict) -> Dict:
        """Scrape website and find social links. Returns intermediate data dict."""
        website = lead.get("website")
        business_name = lead.get("name")
        phone = lead.get("phone")

        scraped_name, scraped_phone, scraped_email, page_text = None, None, None, None
        if website and website != "nan":
            (
                scraped_name,
                scraped_phone,
                scraped_email,
                page_text,
            ) = await hunter.scrape_business_details_async(website)

        search_name = scraped_name or business_name
        (
            fb,
            insta,
            linkedin,
            tiktok,
            pinterest,
        ) = await hunter.trazi_social_linkove_async(search_name, scraped_phone or phone)

        return {
            "scraped_name": scraped_name,
            "scraped_phone": scraped_phone,
            "scraped_email": scraped_email,
            "page_text": page_text,
            "search_name": search_name,
            "socials": {
                "facebook": fb,
                "instagram": insta,
                "linkedin": linkedin,
                "tiktok": tiktok,
                "pinterest": pinterest,
            },
        }

    async def _hunt_for_email(
        self,
        hunter: LeadHunter,
        existing_email: str,
        scraped_email: str,
        search_name: str,
        website: str,
    ) -> str:
        """Find the best email — existing, scraped, or actively hunted."""
        final_email = existing_email or scraped_email
        if not final_email:
            final_email = await hunter.search_for_email_async(search_name, website)
        return final_email

    async def _enrich_business_data(
        self, hunter: LeadHunter, lead: Dict, scraped: Dict, final_email: str
    ) -> Dict:
        """Run AI enrichment, scoring, and segmentation. Returns the full update payload."""
        socials = scraped["socials"]
        fb, insta, linkedin, tiktok, pinterest = (
            socials["facebook"],
            socials["instagram"],
            socials["linkedin"],
            socials["tiktok"],
            socials["pinterest"],
        )
        search_name = scraped["search_name"]

        enrichment_data = {}
        if scraped["page_text"]:
            enrichment_data = await hunter.enrich_business_data_async(
                scraped["page_text"], search_name
            )

        contact_person = enrichment_data.get("leadership_team", "")
        first_name = (
            hunter.extract_personal_name(contact_person) if contact_person else ""
        )
        priority_link = hunter.get_priority_link(fb, insta, lead.get("website"))
        needs_manual_review = not final_email and (
            fb or insta or linkedin or tiktok or pinterest
        )

        outreach_score = hunter.calculate_outreach_score(
            {**lead, "email": final_email, **enrichment_data}, socials
        )
        segment = hunter.segment_lead(
            {**lead, "email": final_email, **enrichment_data},
            enrichment_data.get("pain_points", []),
        )

        return {
            "first_name": first_name,
            "priority_link": priority_link,
            "needs_manual_review": needs_manual_review,
            "outreach_score": outreach_score,
            "segment": segment,
            "enrichment_data": enrichment_data,
            **socials,
        }

    async def hunt_single_lead(self, lead: Dict):
        """
        Performs a deep hunt for a single lead, focusing on social links and business info.
        """
        unique_key = lead.get("unique_key")

        # Hunter holds an aiohttp.ClientSession lazily; without an explicit
        # close() in finally, every hunt leaks one connector + several response
        # handlers (visible in test runs as "Unclosed client session" /
        # "Unclosed connector" logs). At "Hunt All" volume this adds up.
        hunter = LeadHunter()
        try:
            # Bail before doing any work if the operator already clicked STOP.
            self._raise_if_stop_requested()

            # 1. Scrape website and find social links
            scraped = await self._scrape_business_details(hunter, lead)
            self._raise_if_stop_requested()

            # 2. Email hunting
            final_email = await self._hunt_for_email(
                hunter,
                lead.get("email"),
                scraped["scraped_email"],
                scraped["search_name"],
                lead.get("website"),
            )
            self._raise_if_stop_requested()

            # 3. AI enrichment, scoring, segmentation
            enriched = await self._enrich_business_data(
                hunter, lead, scraped, final_email
            )

            # 4. Build result payload
            result = {
                "unique_key": unique_key,
                "status": "Completed",
                "company_name": scraped["scraped_name"],
                "phone": scraped["scraped_phone"] or lead.get("phone"),
                "email": final_email,
                **enriched,
            }
            return result
        except asyncio.CancelledError:
            # Operator-initiated stop. Don't mark the lead Failed — it was
            # never given a fair attempt. Re-raise so gather sees the
            # cancellation; the orchestrator filters these out before
            # upserting.
            logger.info("Hunt cancelled by stop request for %s", unique_key)
            raise
        except asyncio.TimeoutError:
            logger.warning("Hunt Timeout for %s", unique_key)
            return {"unique_key": unique_key, "status": "Failed", "error": "Timeout"}
        except Exception as e:
            logger.error("Error hunting lead %s: %s", unique_key, e, exc_info=True)
            return {"unique_key": unique_key, "status": "Failed", "error": str(e)}
        finally:
            try:
                await hunter.close()
            except Exception:  # noqa: BLE001 — closing must never re-raise
                pass

    async def audit_single_lead(self, lead: Dict):
        """
        Audits a single lead with concurrency control and error handling.
        """
        unique_key = lead.get("unique_key")
        website = lead.get("website")
        business_name = lead.get("name")

        if not website or website == "nan":
            return {"unique_key": unique_key, "status": "Failed", "error": "No website"}

        async with self.semaphore:
            from src.processors.leadhunter import LeadHunter

            # See hunt_single_lead: hunter owns an aiohttp session that must
            # be closed even when the audit fails, or we leak per-call.
            hunter = LeadHunter()
            pain_points = []

            try:
                self._raise_if_stop_requested()

                # 1. SEO Audit
                result = await perform_seo_audit_async(website)
                self._raise_if_stop_requested()

                # 2. Pain Point Analysis (if we got text)
                if "page_text" in result and result["page_text"]:
                    pain_points = await hunter.analyze_pain_points_async(
                        result["page_text"], business_name, audit_results=result
                    )
                    result["pain_points"] = pain_points
                    self._raise_if_stop_requested()

                    # Generate outreach hooks based on pain points and audit data
                    hooks = await hunter.generate_outreach_hooks_async(
                        pain_points, business_name or "your team", audit_results=result
                    )
                    result.update(hooks)
                    self._raise_if_stop_requested()

                # 3. High Risk Determination
                score = result.get("score")
                if score is None:
                    score = 0
                red_flags = result.get("red_flags", [])
                # Defensive check for red flags content
                red_flags_str = " ".join([str(f) for f in red_flags])
                is_high_risk = (
                    score < 50
                    or "SSL Certificate Error" in red_flags_str
                    or "Connection Failed" in red_flags_str
                )
                result["high_risk_flag"] = bool(is_high_risk)

                # 4. Deep Email Hunting if still missing
                final_email = lead.get("email")
                if not result.get("emails") and not final_email:
                    hunted_email = await hunter.search_for_email_async(
                        business_name, website
                    )
                    if hunted_email:
                        result["emails"] = [hunted_email]
                        final_email = hunted_email
                else:
                    final_email = final_email or (
                        result.get("emails")[0] if result.get("emails") else None
                    )

                # 5. Manual Review Flag & Priority Link
                # Determine the best research link for the user
                result["priority_link"] = hunter.get_priority_link(
                    fb=result.get("facebook"),
                    insta=result.get("instagram"),
                    website=website,
                )

                # Calculate final scores and segment
                result["outreach_score"] = hunter.calculate_outreach_score(result)
                result["segment"] = hunter.segment_lead(result, pain_points=pain_points)

                # If no email found during audit, mark for manual review
                result["needs_manual_review"] = not final_email

                return {
                    "unique_key": unique_key,
                    "status": "Completed",
                    "result": result,
                }
            except asyncio.CancelledError:
                # Operator-initiated stop. See hunt_single_lead — re-raise
                # so gather treats this as a cancellation, not a failure.
                logger.info("Audit cancelled by stop request for %s", unique_key)
                raise
            except asyncio.TimeoutError:
                logger.warning("Audit Timeout for %s", unique_key)
                return {
                    "unique_key": unique_key,
                    "status": "Failed",
                    "error": "Timeout",
                    "audit_status": "Timeout",
                }
            except Exception as e:
                error_msg = str(e)
                status = "Failed"
                if "403" in error_msg:
                    status = "403 Forbidden"
                elif "404" in error_msg:
                    status = "404 Not Found"
                elif "DNS" in error_msg or "not resolve" in error_msg:
                    status = "Invalid URL"

                logger.error("Error auditing lead %s: %s", unique_key, e, exc_info=True)
                return {
                    "unique_key": unique_key,
                    "status": "Failed",
                    "error": error_msg,
                    "audit_status": status,
                }
            finally:
                try:
                    await hunter.close()
                except Exception:  # noqa: BLE001 — closing must never re-raise
                    pass

    async def run_batch(self, leads: List[Dict], task_type: str = "audit"):
        """
        Runs a batch of tasks in parallel.
        """
        if task_type == "hunt":
            tasks = [self.hunt_single_lead(lead) for lead in leads]
        else:
            tasks = [self.audit_single_lead(lead) for lead in leads]
        return await asyncio.gather(*tasks)

    async def orchestrate_scaling(
        self, chunk_size: int = 100, task_type: str = "audit"
    ):
        """
        Fetch leads from Supabase and process them in chunks.
        """
        if not self.db.client:
            logger.warning("Supabase not connected. Scaling engine aborted.")
            return

        while True:
            if self.status.get("stop_requested"):
                logger.info("Stop requested for %s. Terminating...", task_type)
                self.status["active"] = False
                break

            # Fetch next chunk
            if task_type == "hunt":
                # For hunting, we might want to target those without social links
                response = (
                    self.db.client.table("leads")
                    .select("*")
                    .or_("facebook.is.null,instagram.is.null")
                    .limit(chunk_size)
                    .execute()
                )
            else:
                response = self.db.get_pending_leads()

            leads = response.data if hasattr(response, "data") else []

            if not leads:
                logger.info("No more leads for %s. Process complete.", task_type)
                self.status["active"] = False
                break

            if self.status["total"] == 0:
                self.status["total"] = len(leads)

            current_batch = leads[:chunk_size]
            logger.info(
                "Processing chunk of %d leads for %s...", len(current_batch), task_type
            )
            self.status["current_chunk"] += 1

            results = await self.run_batch(current_batch, task_type=task_type)

            # Update stats
            leads_to_upsert = []
            for r in results:
                self.status["processed"] += 1
                if r.get("status") == "Completed":
                    self.status["completed"] += 1

                    # Prepare bulk update payload
                    lead_data = {
                        k: v
                        for k, v in r.items()
                        if k not in ["status", "error", "result"]
                    }

                    if task_type == "audit" and "result" in r:
                        audit_res = r["result"]
                        lead_data["audit_status"] = "Completed"
                        lead_data["audit_results"] = audit_res

                        if "emails" in audit_res and audit_res["emails"]:
                            lead_data["email"] = audit_res["emails"][0]

                        if "score" in audit_res:
                            try:
                                lead_data["seo_score"] = float(audit_res["score"])
                            except (ValueError, TypeError):
                                lead_data["seo_score"] = 0

                        if "high_risk_flag" in audit_res:
                            lead_data["high_risk_flag"] = bool(
                                audit_res["high_risk_flag"]
                            )

                    leads_to_upsert.append(lead_data)
                else:
                    self.status["failed"] += 1
                    # Persist the failure to the lead row so the UI can show
                    # it and the user can decide to retry. Without this, the
                    # row stayed at "PENDING" / "Pending" indefinitely and a
                    # "retry" button would just hit the same failure path.
                    # The `audit_status` field returned by audit_single_lead
                    # ("Timeout", "403 Forbidden", "404 Not Found", "Invalid
                    # URL", "Failed") is more specific than a generic boolean.
                    fail_data = {"unique_key": r.get("unique_key")}
                    err = r.get("error") or ""
                    if err:
                        fail_data["last_error"] = err[:500]
                    if task_type == "hunt":
                        fail_data["enrichment_status"] = "FAILED"
                    else:
                        fail_data["audit_status"] = r.get("audit_status") or "Failed"
                    if fail_data.get("unique_key"):
                        leads_to_upsert.append(fail_data)

            if leads_to_upsert:
                upsert_result = self.db.upsert_leads(leads_to_upsert)
                if upsert_result is None:
                    # supabase_helper already logged the underlying APIError.
                    # Surface the symptom here so a chunk-level failure is
                    # visible in the orchestrator log (otherwise the chunk
                    # processed-counter increments while nothing landed in
                    # the DB — the same lying-success class of bug fixed in
                    # backend.main.process_csv_background).
                    logger.error(
                        "Chunk upsert failed: %d leads did not land in Supabase.",
                        len(leads_to_upsert),
                    )

            logger.info("Finished chunk. Resuming in 2 seconds...")
            await asyncio.sleep(2)

        self.status["active"] = False
