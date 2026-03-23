import asyncio
import aiohttp
from typing import List, Dict
from src.scrapers.seo_audit import perform_seo_audit_async
from src.utils.supabase_helper import SupabaseHelper
from src.utils.csv_helper import save_csv
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
            "current_chunk": 0
        }

    def reset_status(self, total_pending: int):
        self.status = {
            "active": True,
            "total": total_pending,
            "processed": 0,
            "completed": 0,
            "failed": 0,
            "current_chunk": 0,
            "stop_requested": False
        }

    def stop(self):
        self.status["stop_requested"] = True
        self.status["active"] = False

    async def hunt_single_lead(self, lead: Dict):
        """
        Performs a deep hunt for a single lead, focusing on social links and business info.
        """
        unique_key = lead.get("unique_key")
        business_name = lead.get("name")
        phone = lead.get("phone")
        website = lead.get("website")
        existing_email = lead.get("email")
        update_payload = {}

        try:
            hunter = LeadHunter()

            # 1. Scrape business details from website if we have it
            scraped_name, scraped_phone, scraped_email, page_text = None, None, None, None
            if website and website != 'nan':
                scraped_name, scraped_phone, scraped_email, page_text = await hunter.scrape_business_details_async(website)

            # 2. Search for social links
            # Use the best available name for search
            search_name = scraped_name or business_name
            fb, insta, linkedin, tiktok, pinterest = await hunter.trazi_social_linkove_async(search_name, scraped_phone or phone)

            # 3. Email Hunting if missing
            final_email = existing_email or scraped_email
            hunted_email = None
            if not final_email:
                hunted_email = await hunter.search_for_email_async(search_name, website)
                final_email = hunted_email

            if final_email:
                update_payload["email"] = final_email

            # 4. AI Enrichment (Company size, leadership, etc.)
            enrichment_data = {}
            if page_text:
                enrichment_data = await hunter.enrich_business_data_async(page_text, search_name)

            # 5. Outreach Readiness Logic
            # First Name extraction for personalization
            contact_person = enrichment_data.get("leadership_team", "")
            first_name = hunter.extract_personal_name(contact_person) if contact_person else ""

            # Priority link for manual research
            priority_link = hunter.get_priority_link(fb, insta, website)

            # Manual review flag (No email + has social)
            needs_manual_review = not final_email and (fb or insta or linkedin or tiktok or pinterest)

            # Scoring & Segmentation
            outreach_score = hunter.calculate_outreach_score(
                {**lead, **update_payload, **enrichment_data},
                {"facebook": fb, "instagram": insta, "linkedin": linkedin, "tiktok": tiktok, "pinterest": pinterest}
            )

            segment = hunter.segment_lead(
                {**lead, **update_payload, **enrichment_data},
                enrichment_data.get("pain_points", [])
            )

            # 6. Prepare update payload
            update_payload.update(enrichment_data)
            if scraped_name:
                update_payload["company_name"] = scraped_name
            if scraped_phone:
                update_payload["phone"] = scraped_phone

            update_payload.update({
                "first_name": first_name,
                "priority_link": priority_link,
                "needs_manual_review": needs_manual_review,
                "outreach_score": outreach_score,
                "segment": segment,
                "facebook": fb,
                "instagram": insta,
                "linkedin": linkedin,
                "tiktok": tiktok,
                "pinterest": pinterest
            })

            # Prepare result payload for orchestrator
            return {
                "unique_key": unique_key,
                "status": "Completed",
                "facebook": fb,
                "instagram": insta,
                "linkedin": linkedin,
                "tiktok": tiktok,
                "pinterest": pinterest,
                "company_name": scraped_name,
                "phone": scraped_phone or phone,
                "email": final_email,
                "first_name": first_name,
                "priority_link": priority_link,
                "needs_manual_review": needs_manual_review,
                "outreach_score": outreach_score,
                "segment": segment,
                "enrichment_data": enrichment_data
            }
        except asyncio.TimeoutError:
            logger.warning("Hunt Timeout for %s", unique_key)
            return {"unique_key": unique_key, "status": "Failed", "error": "Timeout"}
        except Exception as e:
            logger.error("Error hunting lead %s: %s", unique_key, e, exc_info=True)
            return {"unique_key": unique_key, "status": "Failed", "error": str(e)}

    async def audit_single_lead(self, lead: Dict):
        """
        Audits a single lead with concurrency control and error handling.
        """
        unique_key = lead.get("unique_key")
        website = lead.get("website")
        business_name = lead.get("name")

        if not website or website == 'nan':
            return {"unique_key": unique_key, "status": "Failed", "error": "No website"}

        async with self.semaphore:
            from src.processors.leadhunter import LeadHunter
            hunter = LeadHunter()
            pain_points = []

            try:
                # 1. SEO Audit
                result = await perform_seo_audit_async(website)

                # 2. Pain Point Analysis (if we got text)
                if "page_text" in result and result["page_text"]:
                    pain_points = await hunter.analyze_pain_points_async(
                        result["page_text"],
                        business_name,
                        audit_results=result
                    )
                    result["pain_points"] = pain_points

                    # Generate outreach hooks based on pain points and audit data
                    hooks = await hunter.generate_outreach_hooks_async(
                        pain_points,
                        business_name or "your team",
                        audit_results=result
                    )
                    result.update(hooks)

                # 3. High Risk Determination
                score = result.get("score")
                if score is None:
                    score = 0
                red_flags = result.get("red_flags", [])
                # Defensive check for red flags content
                red_flags_str = " ".join([str(f) for f in red_flags])
                is_high_risk = score < 50 or "SSL Certificate Error" in red_flags_str or "Connection Failed" in red_flags_str
                result["high_risk_flag"] = bool(is_high_risk)

                # 4. Deep Email Hunting if still missing
                final_email = lead.get("email")
                if not result.get("emails") and not final_email:
                    hunted_email = await hunter.search_for_email_async(business_name, website)
                    if hunted_email:
                        result["emails"] = [hunted_email]
                        final_email = hunted_email
                else:
                    final_email = final_email or (result.get("emails")[0] if result.get("emails") else None)

                # 5. Manual Review Flag & Priority Link
                # Determine the best research link for the user
                result["priority_link"] = hunter.get_priority_link(
                    fb=result.get("facebook"),
                    insta=result.get("instagram"),
                    website=website
                )

                # Calculate final scores and segment
                result["outreach_score"] = hunter.calculate_outreach_score(result)
                result["segment"] = hunter.segment_lead(result, pain_points=pain_points)

                # If no email found during audit, mark for manual review
                result["needs_manual_review"] = not final_email

                return {"unique_key": unique_key, "status": "Completed", "result": result}
            except asyncio.TimeoutError:
                logger.warning("Audit Timeout for %s", unique_key)
                return {"unique_key": unique_key, "status": "Failed", "error": "Timeout", "audit_status": "Timeout"}
            except Exception as e:
                error_msg = str(e)
                status = "Failed"
                if "403" in error_msg: status = "403 Forbidden"
                elif "404" in error_msg: status = "404 Not Found"
                elif "DNS" in error_msg or "not resolve" in error_msg: status = "Invalid URL"

                logger.error("Error auditing lead %s: %s", unique_key, e, exc_info=True)
                return {"unique_key": unique_key, "status": "Failed", "error": error_msg, "audit_status": status}

    async def run_batch(self, leads: List[Dict], task_type: str = "audit"):
        """
        Runs a batch of tasks in parallel.
        """
        if task_type == "hunt":
            tasks = [self.hunt_single_lead(lead) for lead in leads]
        else:
            tasks = [self.audit_single_lead(lead) for lead in leads]
        return await asyncio.gather(*tasks)

    async def orchestrate_scaling(self, chunk_size: int = 100, task_type: str = "audit"):
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
                response = self.db.client.table("leads").select("*").or_("facebook.is.null,instagram.is.null").limit(chunk_size).execute()
            else:
                response = self.db.get_pending_leads()

            leads = response.data if hasattr(response, 'data') else []

            if not leads:
                logger.info("No more leads for %s. Process complete.", task_type)
                self.status["active"] = False
                break

            if self.status["total"] == 0:
                self.status["total"] = len(leads)

            current_batch = leads[:chunk_size]
            logger.info("Processing chunk of %d leads for %s...", len(current_batch), task_type)
            self.status["current_chunk"] += 1

            results = await self.run_batch(current_batch, task_type=task_type)

            # Update stats
            leads_to_upsert = []
            for r in results:
                self.status["processed"] += 1
                if r.get("status") == "Completed":
                    self.status["completed"] += 1

                    # Prepare bulk update payload
                    lead_data = {k: v for k, v in r.items() if k not in ["status", "error", "result"]}

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
                            lead_data["high_risk_flag"] = bool(audit_res["high_risk_flag"])

                    leads_to_upsert.append(lead_data)
                else:
                    self.status["failed"] += 1

            if leads_to_upsert:
                self.db.upsert_leads(leads_to_upsert)

            logger.info("Finished chunk. Resuming in 2 seconds...")
            await asyncio.sleep(2)

        self.status["active"] = False
