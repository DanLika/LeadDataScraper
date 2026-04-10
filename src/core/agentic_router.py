import os
import json
from google import genai
from dotenv import load_dotenv
from src.utils.supabase_helper import SupabaseHelper
from src.core.agent_tools import get_agent_tools
from src.utils.json_helper import extract_json_from_response
from src.utils.logging_config import get_logger
import pandas as pd
from src.core.data_manager import merge_and_deduplicate

load_dotenv()

logger = get_logger(__name__)

class AgenticRouter:
    """
    Main intelligence hub that routes user instructions to specific system tasks.
    Uses Google's Gemini AI to parse natural language and orchestration logic.
    """
    def __init__(self):
        """
        Initializes the router with API keys and connects to the Gemini model and Supabase.
        """
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.db = SupabaseHelper()
        if not self.api_key:
            logger.warning("GEMINI_API_KEY not found.")
            self.client = None
        else:
            self.client = genai.Client(api_key=self.api_key)

    async def route_instruction(self, instruction: str):
        """
        Parses a natural language instruction from the user using Gemini's Tool Calling.
        """
        if not self.client:
            return {"error": "AI model not initialized"}

        from google.genai import types

        tools = get_agent_tools()

        try:
            response = self.client.models.generate_content(
                model='gemini-flash-latest',
                contents=instruction,
                config=types.GenerateContentConfig(
                    tools=tools,
                    system_instruction="You are the main coordinator for a Lead Generation SaaS. Route the user instruction to the correct tool."
                )
            )

            # Process function calls
            if response.candidates and response.candidates[0].content.parts:
                for part in response.candidates[0].content.parts:
                    if part.function_call:
                        call = part.function_call
                        return {
                            "task": call.name.upper(),
                            "params": call.args if call.args else {},
                            "reasoning": f"AI selected tool: {call.name}"
                        }

            return {
                "task": "UNKNOWN",
                "params": {},
                "reasoning": "No tool was called by the model.",
                "raw": response.text if response.text else "No text response"
            }
        except Exception as e:
            logger.error("Route instruction failed: %s", e, exc_info=True)
            return {
                "task": "ERROR",
                "params": {},
                "reasoning": f"Tool calling failed: {str(e)}"
            }

    async def execute_task(self, orchestration_plan: dict):
        """
        Executes the specific task defined in an orchestration plan.
        Dispatches to internal specialized methods based on the task type.
        """
        task = orchestration_plan.get("task", "").upper()
        params = orchestration_plan.get("params", {})

        handlers = {
            "DATABASE_QUERY": lambda p: self._execute_database_query(p.get("query_text", ""), p),
            "STATUS_CHECK": lambda p: self._get_status_summary(),
            "SEO_AUDIT": self._execute_seo_audit,
            "OUTREACH_DRAFT": self._generate_outreach_draft,
            "GET_INSIGHTS": lambda p: self._get_strategic_insights(),
            "DATA_MERGE": lambda p: self._execute_data_merge(),
            "DEEP_HUNT": self._execute_deep_hunt,
            "RUN_MASSIVE_PIPELINE": self._execute_massive_pipeline,
            "LINKEDIN_DRAFT": self._generate_linkedin_draft,
            "DISCOVERY_SEARCH": self._execute_discovery_search,
            "DEEP_ENRICHMENT": self._execute_deep_enrichment,
            "CAMPAIGN_STRATEGY": self._generate_campaign_strategy,
        }

        handler = handlers.get(task)
        if handler:
            return await handler(params)
        else:
            return {"error": f"Unknown task: {task}"}

    async def _execute_seo_audit(self, params: dict):
        unique_key = params.get("unique_key")
        if unique_key:
            from src.core.parallel_auditor import ParallelAuditor
            auditor = ParallelAuditor()
            lead_data = self.db.client.table("leads").select("*").eq("unique_key", unique_key).execute()
            if lead_data.data:
                result = await auditor.audit_single_lead(lead_data.data[0])
                return {"message": "SEO Audit completed for single lead.", "result": result}
            return {"error": f"Lead {unique_key} not found for SEO Audit"}
        else:
            from src.core.task_orchestrator import TaskOrchestrator
            orchestrator = TaskOrchestrator()
            job_id = await orchestrator.run_massive_pipeline(tasks=["audit"])
            return {"message": "Massive SEO Audit pipeline started.", "job_id": job_id}

    async def _execute_deep_hunt(self, params: dict):
        unique_key = params.get("unique_key")
        if not unique_key:
            return {"error": "unique_key is required for DEEP_HUNT"}

        from src.utils.supabase_helper import SupabaseHelper
        db = SupabaseHelper()
        response = db.client.table("leads").select("*").eq("unique_key", unique_key).execute()
        leads = response.data if hasattr(response, 'data') else []

        if not leads:
            return {"error": "Lead not found"}

        from src.core.parallel_auditor import ParallelAuditor
        auditor = ParallelAuditor()
        result = await auditor.hunt_single_lead(leads[0])

        return {
            "message": "Deep Hunt completed.",
            "lead_name": leads[0].get("name"),
            "facebook": result.get("facebook"),
            "instagram": result.get("instagram")
        }

    async def _get_status_summary(self):
        """
        Fetches a high-level summary of the current lead database state.
        """
        if not self.db.client:
            return {"error": "Database not connected"}

        counts = self.db.client.table("leads").select("audit_status", count="exact").execute()
        return {
            "summary": "Database Status",
            "details": counts.data if hasattr(counts, 'data') else "No data available"
        }

    async def _execute_database_query(self, reasoning: str, params: dict):
        """
        Translates a natural language data request into an AI-summarized answer based on DB context.
        """
        if not self.db.client:
            return {"error": "Database not connected"}
        if not self.client:
            return {"error": "AI model not initialized. Set GEMINI_API_KEY."}

        # Fetch limited data for context (to avoid token limits)
        response = self.db.client.table("leads").select("name,company_name,audit_status,seo_score,lead_source").limit(50).execute()
        leads = response.data if hasattr(response, 'data') else []

        query_prompt = f"""
        User Goal: {reasoning}
        Context: You have access to the following 50 leads from the database.
        Leads: {json.dumps(leads)}

        Based on the User Goal and the provided data, provide a concise, professional answer.
        If the data is insufficient, say so.
        """

        try:
            summary_response = self.client.models.generate_content(
                model='gemini-flash-latest',
                contents=query_prompt
            )
            return {"answer": summary_response.text}
        except Exception as e:
            logger.error("Database query AI call failed: %s", e, exc_info=True)
            return {"error": f"AI query failed: {str(e)}"}

    async def _generate_outreach_draft(self, params: dict):
        """
        Generates a highly personalized outreach email for a specific lead using their SEO audit context.
        """
        unique_key = params.get("unique_key")
        if not unique_key:
            return {"error": "unique_key is required for outreach drafting"}
        if not self.client:
            return {"error": "AI model not initialized. Set GEMINI_API_KEY."}

        # Fetch full lead and audit context
        response = self.db.client.table("leads").select("*").eq("unique_key", unique_key).execute()
        leads = response.data if hasattr(response, 'data') else []
        if not leads:
            return {"error": "Lead not found in database"}

        lead = leads[0]
        audit = lead.get("audit_results", {})

        prompt = f"""
        Write a cold outreach email to a potential client. This email will be sent directly — it must be perfectly written, grammatically correct, and ready to send without any editing.

        Lead Details:
        - Contact Name: {lead.get('name', 'there')}
        - Company: {lead.get('company_name', 'your company')}
        - Website: {lead.get('website', '')}

        Technical Findings:
        - SEO Score: {audit.get('score', 'N/A')}/100
        - Missing Title Tag: {audit.get('missing_title', False)}
        - Missing Meta Description: {audit.get('missing_description', False)}
        - Missing H1 Tag: {audit.get('no_h1', False)}
        - SSL Certificate Valid: {audit.get('ssl_valid', 'N/A')}

        Business Pain Points:
        {audit.get('pain_points', 'No specific pain points identified.')}

        STRICT REQUIREMENTS:
        1. Maximum 150 words.
        2. Start with "Hi {{{{first_name}}}}," (use this exact placeholder).
        3. Be helpful and observant — NOT salesy or pushy.
        4. Reference ONE specific, concrete issue from the findings above.
        5. End with a soft, low-pressure call to action (e.g. "Would it be worth a quick chat?").
        6. Sign off with just "Best," on a new line (no name — that gets added by the email tool).
        7. Write in plain text — no markdown, no bold, no bullet points, no asterisks.
        8. Use proper grammar, punctuation, and natural sentence flow.
        9. Do NOT include a subject line — just the email body.

        Return ONLY the email body text, nothing else.
        """

        try:
            draft_response = self.client.models.generate_content(
                model='gemini-flash-latest',
                contents=prompt
            )
            return {
                "draft": draft_response.text.strip(),
                "lead_name": lead.get("name") or lead.get("company_name") or "there"
            }
        except Exception as e:
            logger.error("Outreach draft generation failed for %s: %s", unique_key, e, exc_info=True)
            return {"error": f"Failed to generate outreach draft: {str(e)}"}

    async def _generate_linkedin_draft(self, params: dict):
        """
        Generates a personalized, concise LinkedIn connection request for a specific lead.
        """
        unique_key = params.get("unique_key")
        if not unique_key:
            return {"error": "unique_key is required"}
        if not self.client:
            return {"error": "AI model not initialized. Set GEMINI_API_KEY."}

        response = self.db.client.table("leads").select("*").eq("unique_key", unique_key).execute()
        leads = response.data if hasattr(response, 'data') else []
        if not leads: return {"error": "Lead not found"}

        lead = leads[0]
        prompt = f"""
        Write a LinkedIn connection request message. This will be pasted directly into LinkedIn — it must be perfect and ready to send.

        About the person/company:
        - Person: {lead.get('leadership_team', 'Decision Maker')}
        - Company: {lead.get('company_name', 'your company')}
        - What they do: {lead.get('business_details', 'N/A')}
        - Their clients: {lead.get('target_clients', 'N/A')}

        STRICT REQUIREMENTS:
        1. MAXIMUM 300 characters (this is LinkedIn's hard limit — count carefully).
        2. Start with "Hi" — no exclamation marks in the greeting.
        3. Mention their company name or something specific about their business.
        4. Be warm, professional, and genuine — focus on connecting, NOT selling.
        5. Write in plain text — no markdown, no emojis, no special formatting.
        6. Must be one cohesive message, not multiple sentences if possible.
        7. Use proper grammar and punctuation.

        Good example (267 chars): "Hi, I came across {lead.get('company_name', 'your company')} and was really impressed by the work you're doing. I'm in a similar space and would love to connect and exchange ideas sometime."

        Return ONLY the message text, nothing else.
        """

        try:
            draft = self.client.models.generate_content(
                model='gemini-flash-latest',
                contents=prompt
            )
            return {
                "draft": draft.text.strip(),
                "recipient": lead.get('leadership_team', 'there')
            }
        except Exception as e:
            logger.error("LinkedIn draft generation failed for %s: %s", unique_key, e, exc_info=True)
            return {"error": f"Failed to generate LinkedIn draft: {str(e)}"}

    async def _get_strategic_insights(self):
        """
        Analyzes the lead database to identify patterns, vulnerabilities, and high-priority targets.
        """
        if not self.db.client:
            return {"error": "Database not connected"}
        if not self.client:
            return {"error": "AI model not initialized. Set GEMINI_API_KEY."}

        # Fetch recent leads with audit results
        response = self.db.client.table("leads").select("name,company_name,audit_status,seo_score,lead_source").limit(200).execute()
        leads = response.data if hasattr(response, 'data') else []

        if not leads:
            return {"summary": "No data yet to analyze. Try importing some leads!", "insights": [], "top_priorities": []}

        prompt = f"""
        You are a Database Analyst for a Lead Generation agency.
        Analyze the following lead data (including SEO audit results) and provide 3 key strategic insights.

        Focus on:
        - Critical vulnerabilities (missing SSL, title, etc).
        - Industry patterns (if detectable).
        - Recommended priorities for outreach.

        Leads Data: {json.dumps(leads)}

        Return a JSON object:
        {{
            "summary": "One sentence overview of the pipeline health",
            "insights": ["Insight 1", "Insight 2", "Insight 3"],
            "top_priorities": [
                {{"name": "Company Name", "reason": "Why they should be contacted first"}}
            ]
        }}
        """

        try:
            ai_response = self.client.models.generate_content(
                model='gemini-flash-latest',
                contents=prompt
            )
            result = extract_json_from_response(ai_response.text)
            if result:
                return result
            return {"summary": "System analysis completed.", "insights": [ai_response.text[:100]], "top_priorities": []}
        except Exception as e:
            logger.error("Strategic insights AI call failed: %s", e, exc_info=True)
            return {"summary": "Insights currently unavailable.", "insights": [], "top_priorities": []}

    async def _execute_data_merge(self):
        """
        Triggers a deduplication and standardization process across all leads in the database.
        """
        if not self.db.client:
            return {"error": "Database not connected"}

        response = self.db.client.table("leads").select("*").execute()
        leads_data = response.data if hasattr(response, 'data') else []

        if not leads_data:
            return {"message": "No leads found to merge."}

        df = pd.DataFrame(leads_data)

        cleaned_df = merge_and_deduplicate([df])

        # Calculate how many were removed
        removed_count = len(df) - len(cleaned_df)

        return {
            "message": "Data merge and deduplication complete.",
            "original_count": len(df),
            "final_count": len(cleaned_df),
            "removed_duplicates": removed_count
        }

    async def _execute_discovery_search(self, params: dict):
        """
        Triggers the DiscoveryEngine through TaskOrchestrator to find new leads.
        """
        query = params.get("query")
        location = params.get("location", "")

        if not query:
            return {"error": "query is required for discovery search"}

        from src.core.task_orchestrator import TaskOrchestrator
        orchestrator = TaskOrchestrator()

        job_id = await orchestrator.run_discovery_job(query, location)

        return {
            "message": f"Discovery search started for '{query}'. Tracking progress via job system.",
            "job_id": job_id,
            "status_url": f"/orchestrator/status/{job_id}"
        }

    async def _execute_deep_enrichment(self, params: dict):
        """
        Triggers the EnrichmentEngine to find deep company data for a specific lead.
        """
        unique_key = params.get("unique_key")
        if not unique_key:
            return {"error": "unique_key is required for DEEP_ENRICHMENT"}

        # Fetch lead from DB
        response = self.db.client.table("leads").select("*").eq("unique_key", unique_key).execute()
        leads = response.data if hasattr(response, 'data') else []

        if not leads:
            return {"error": "Lead not found in database"}

        from src.scrapers.enrichment_engine import EnrichmentEngine
        from src.processors.leadhunter import LeadHunter
        engine = EnrichmentEngine()
        hunter = LeadHunter()

        enriched_lead = await engine.enrich_lead(leads[0])

        # Calculate outreach score
        enriched_lead["outreach_score"] = hunter.calculate_outreach_score(enriched_lead)

        # Phase 10: Segmentation & Outreach Hooks
        enriched_lead["segment"] = hunter.segment_lead(enriched_lead)
        if enriched_lead.get("pain_points") and enriched_lead["pain_points"] != "Could not analyze pain points.":
            hooks = await hunter.generate_outreach_hooks_async(
                enriched_lead["pain_points"],
                enriched_lead.get("company_name") or enriched_lead.get("name") or "Prospect"
            )
            enriched_lead["linkedin_hook"] = hooks.get("linkedin_hook", "")
            enriched_lead["email_hook"] = hooks.get("email_hook", "")

        # Upsert the enriched lead back to DB
        self.db.upsert_leads([enriched_lead])

        return {
            "message": "Deep Enrichment completed.",
            "lead": enriched_lead
        }

    async def _execute_massive_pipeline(self, params: dict):
        """
        Starts a large-scale orchestration job to process many leads in the background.
        """
        try:
            from src.core.task_orchestrator import TaskOrchestrator
            orchestrator = TaskOrchestrator()

            filter_type = params.get("filters") or params.get("type")
            filters_dict = {"type": filter_type} if filter_type else None
            job_id = await orchestrator.run_massive_pipeline(filters=filters_dict)

            return {
                "message": "Massive pipeline orchestration started.",
                "job_id": job_id,
                "status_url": f"/orchestrator/status/{job_id}"
            }
        except Exception as e:
            logger.error("Massive pipeline execution failed: %s", e, exc_info=True)
            return {"error": f"Failed to start massive pipeline: {str(e)}"}

    async def _generate_campaign_strategy(self, params: dict):
        """
        Creates a bulk outreach campaign by selecting top leads and generating personalized drafts.
        """
        try:
            filters = params.get("filters", "high-risk")

            # 1. Fetch leads
            query = self.db.client.table("leads").select("*")
            if filters == "high-risk":
                query = query.filter("outreach_score", "gt", 0).order("outreach_score", desc=True)
            else:
                query = query.order("outreach_score", desc=True)

            response = query.limit(5).execute()
            leads = response.data if hasattr(response, 'data') else []

            if not leads:
                return {"message": "No suitable leads found for the campaign strategy."}

            from src.processors.leadhunter import LeadHunter
            hunter = LeadHunter()

            campaign_leads = []
            for lead in leads:
                # Extract first name for personalization
                name_src = lead.get("leadership_team") or lead.get("name") or ""
                unique_key = lead.get("unique_key")

                # Use hunter to get a clean first name
                first_name = hunter.extract_personal_name(name_src)

                # Generate personalized draft
                draft_result = await self._generate_outreach_draft({"unique_key": unique_key})

                campaign_leads.append({
                    "unique_key": unique_key,
                    "company": lead.get("company_name") or lead.get("name"),
                    "first_name": first_name or "there",
                    "draft": draft_result.get("draft", "No draft generated.")
                })

            return {
                "message": f"Campaign strategy generated for {len(campaign_leads)} leads.",
                "campaign": campaign_leads,
                "reasoning": f"Curated a selection of {filters} leads for immediate outreach."
            }
        except Exception as e:
            logger.error("Campaign strategy generation failed: %s", e, exc_info=True)
            return {"error": f"Failed to generate campaign strategy: {str(e)}"}
