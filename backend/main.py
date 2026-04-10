from fastapi import FastAPI, UploadFile, File, BackgroundTasks, Depends, Security, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn
import os
import pandas as pd
import aiofiles
from datetime import datetime
from pathlib import PurePath
from typing import Optional, List
from dotenv import load_dotenv
from pydantic import BaseModel, Field, constr
from postgrest.exceptions import APIError
from fastapi.security import APIKeyHeader

from src.utils.supabase_helper import SupabaseHelper
from src.core.agentic_router import AgenticRouter
from src.core.parallel_auditor import ParallelAuditor
from src.core.task_orchestrator import TaskOrchestrator
from src.scripts.export_leads import export_leads
from src.utils.logging_config import setup_logging, get_logger
from fastapi.responses import FileResponse

logger = get_logger(__name__)

# --- API Key Authentication ---
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

async def verify_api_key(key: Optional[str] = Security(api_key_header)) -> str:
    expected = os.getenv("API_SECRET_KEY")
    if not expected:
        logger.warning("API_SECRET_KEY not set — requests are blocked. Set it in .env for production.")
        raise HTTPException(status_code=403, detail="API Key Verification is not configured")
    if not key or key != expected:
        raise HTTPException(status_code=403, detail="Invalid or missing API key")
    return key


def error_response(message, status_code=500) -> JSONResponse:
    body = {"error": message}
    return JSONResponse(content=body, status_code=status_code)


class CampaignCreate(BaseModel):
    name: str
    channel: str  # email, linkedin, multi
    segment_filter: Optional[str] = None

class CampaignUpdate(BaseModel):
    name: Optional[str] = None
    status: Optional[str] = None

class LeadProcessRequest(BaseModel):
    unique_key: str

class AskRequest(BaseModel):
    instruction: dict

class DiscoveryRequest(BaseModel):
    query: constr(min_length=1, max_length=500)
    location: Optional[str] = Field(default="", max_length=200)

class PipelineRequest(BaseModel):
    filters: Optional[dict] = None
    lead_ids: Optional[List[str]] = None
    tasks: Optional[List[str]] = None

class ExecutePlanRequest(BaseModel):
    task: str
    params: Optional[dict] = None

load_dotenv()

app = FastAPI(title="LeadDataScraper API")
db = SupabaseHelper()
router = AgenticRouter()
auditor = ParallelAuditor()
orchestrator = TaskOrchestrator()

# Configure CORS
allowed_origins_env = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000")
allowed_origins = [origin.strip() for origin in allowed_origins_env.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key"],
)

@app.get("/")
async def root():
    """Health check endpoint to verify API status."""
    return {"message": "LeadDataScraper API is running", "status": "online"}

@app.get("/leads", dependencies=[Depends(verify_api_key)])
async def list_leads():
    """Retrieve all leads from the database ordered by creation date."""
    try:
        if not db.client:
            return error_response("Database not connected", status_code=503)
        response = db.client.table("leads").select("*").order("created_at", desc=True).limit(200).execute()
        return {"leads": response.data}
    except APIError as e:
        logger.error("Database API Error fetching leads: %s", e, exc_info=True)
        return error_response("Failed to fetch leads from database", status_code=502)
    except Exception as e:
        logger.error("Unexpected error fetching leads: %s", e, exc_info=True)
        return error_response("An unexpected error occurred while fetching leads")

def validate_csv_upload(file: UploadFile, contents: bytes) -> Optional[JSONResponse]:
    """Validate uploaded file is a CSV and within size limits."""
    if not file.filename or not file.filename.lower().endswith('.csv'):
        return error_response("Only CSV files are allowed.", status_code=400)

    if file.content_type and file.content_type not in ["text/csv", "application/vnd.ms-excel", "application/octet-stream"]:
        return error_response(f"Invalid content type: {file.content_type}. Expected text/csv.", status_code=400)

    max_size = 50 * 1024 * 1024  # 50MB
    if len(contents) > max_size:
        return error_response(f"File too large. Maximum size is 50MB, got {len(contents) / (1024*1024):.1f}MB.", status_code=400)

    return None

@app.post("/upload", dependencies=[Depends(verify_api_key)])
async def upload_leads(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    """
    Handle CSV file upload, map columns using AI, and upsert leads to the database.
    Processing happens in the background.
    """
    contents = await file.read()
    validation_error = validate_csv_upload(file, contents)
    if validation_error:
        return validation_error

    # Save uploaded file temporarily — sanitize filename to prevent path traversal
    safe_name = PurePath(file.filename).name
    temp_path = f"tmp_{safe_name}"
    async with aiofiles.open(temp_path, "wb") as buffer:
        await buffer.write(contents)

    background_tasks.add_task(process_csv_background, temp_path)
    return {"filename": file.filename, "status": "processing", "message": "Leads are being imported in the background."}


def _load_and_standardize_csv(temp_path: str) -> pd.DataFrame:
    from src.utils.csv_helper import load_csv_with_unique_key
    df = load_csv_with_unique_key(temp_path)
    df.columns = [col.lower().replace(" ", "_") for col in df.columns]
    return df

def _apply_ai_mapping(df: pd.DataFrame) -> pd.DataFrame:
    from src.processors.ai_mapper import GeminiMapper
    mapper = GeminiMapper()
    mapping = mapper.get_column_mapping(df.columns.tolist())
    if mapping:
        logger.info("AI suggested mapping: %s", mapping)
        df = df.rename(columns=mapping)
    return df

def _filter_valid_columns(df: pd.DataFrame) -> pd.DataFrame:
    valid_cols = [
        "unique_key", "name", "company_name", "website", "email", "phone", "address",
        "rating", "reviews", "lead_source", "audit_status", "audit_results",
        "enrichment_status", "high_risk_flag", "seo_score", "outreach_score",
        "company_size", "leadership_team", "key_offerings", "contact_details",
        "business_details", "target_clients", "pain_points", "segment",
        "email_hook", "linkedin_hook",
        "facebook", "instagram", "linkedin", "tiktok", "pinterest"
    ]
    return df[[col for col in df.columns if col in valid_cols]]

def _upsert_leads_to_db(df: pd.DataFrame):
    leads_dict = df.to_dict('records')
    # Clean up NaN for JSON serialization
    leads_dict = [{k: (None if pd.isna(v) else v) for k, v in lead.items()} for lead in leads_dict]
    logger.info("Upserting %d leads with columns: %s", len(leads_dict), df.columns.tolist())
    db.upsert_leads(leads_dict)
    return len(leads_dict)

def process_csv_background(temp_path: str):
    """Background task to process the uploaded CSV."""
    try:
        # 1 & 2. Load and standardize data
        df = _load_and_standardize_csv(temp_path)

        # 3. AI Mapping
        df = _apply_ai_mapping(df)

        # 4. Filter columns
        final_df = _filter_valid_columns(df)

        # 5. Upsert to database
        upserted_count = _upsert_leads_to_db(final_df)

        # Cleanup
        os.remove(temp_path)
        logger.info("Successfully processed and upserted %d leads.", upserted_count)
    except Exception as e:
        logger.error("Error processing upload: %s", e, exc_info=True)
        if os.path.exists(temp_path):
            os.remove(temp_path)

@app.post("/process-lead", dependencies=[Depends(verify_api_key)])
async def process_single_lead(payload: LeadProcessRequest):
    """Trigger a single lead SEO audit and enrichment via orchestrator."""
    job_id = await orchestrator.run_massive_pipeline(lead_ids=[payload.unique_key])
    return {"status": "started", "unique_key": payload.unique_key, "job_id": job_id}

@app.post("/process-all", dependencies=[Depends(verify_api_key)])
async def process_all_pending():
    """Trigger the audit orchestrator to process all pending leads."""
    job_id = await orchestrator.run_massive_pipeline(tasks=["audit"])
    return {"status": "job_started", "job_id": job_id}

@app.get("/audit-status", dependencies=[Depends(verify_api_key)])
async def get_audit_status():
    """
    Get the current status of the batch audit process.
    Legacy endpoint for single-batch monitoring.
    """
    if auditor.status.get("active"):
        return auditor.status

    # Fallback to checking for any running orchestrator job
    response = db.client.table("orchestration_jobs").select("*").eq("status", "running").order("created_at", desc=True).limit(1).execute()
    if response.data:
        job = response.data[0]
        return {
            "active": True,
            "processed": job["processed_count"],
            "total": job["total_count"],
            "current_chunk": 0
        }
    return {"active": False, "processed": 0, "total": 0}

@app.post("/audit/stop", dependencies=[Depends(verify_api_key)])
async def stop_audit():
    """Signal the orchestrator to stop all running jobs."""
    db.client.table("orchestration_jobs").update({
        "status": "stopped",
        "current_phase": "Stopped by user"
    }).eq("status", "running").execute()

    auditor.stop()
    return {"status": "stopped"}

@app.get("/health/schema", dependencies=[Depends(verify_api_key)])
async def health_schema():
    missing = db.check_schema()
    return {
        "status": "healthy" if not missing else "degraded",
        "missing_columns_count": len(missing),
    }

@app.on_event("startup")
async def startup_event():
    setup_logging()
    logger.info("Lead Data Scraper Backend Starting...")
    missing = db.check_schema()
    if missing:
        logger.warning("DATABASE SCHEMA MISMATCH: Missing columns: %s", missing)
        logger.warning("Attempting automatic migration...")
        migrated = db.auto_migrate(missing)
        if migrated:
            logger.info("Migration successful - columns added.")
        else:
            logger.warning("Auto-migration failed. Run this SQL manually in Supabase SQL Editor:")
            logger.warning("   ALTER TABLE leads %s;", ', '.join([f'ADD COLUMN IF NOT EXISTS {col} TEXT' for col in missing]))
    else:
        logger.info("Database schema is up to date.")
    await orchestrator.recover_interrupted_jobs()

@app.post("/ask", dependencies=[Depends(verify_api_key)])
async def ask_ai(payload: AskRequest, background_tasks: BackgroundTasks):
    """
    Process natural language instructions.
    Can execute simple tasks immediately or propose a multi-step plan for confirmation.
    """
    try:
        instruction_obj = payload.instruction
        if not instruction_obj:
             return error_response("Missing 'instruction' object", status_code=400)

        prompt = instruction_obj.get("text")

        if not prompt:
            return error_response("Missing 'text' in 'instruction'", status_code=400)

        # 1. Route the instruction to a task
        plan = await router.route_instruction(prompt)

        # 2. For informational or simple tasks, execute immediately
        if plan.get("task") in ["DATABASE_QUERY", "STATUS_CHECK"]:
            result = await router.execute_task(plan)
            return {"response": result.get("answer") or result.get("message") or "Query executed."}

        # 3. For process-heavy tasks, return the plan for UI confirmation OR just start it
        return {"plan": plan, "response": "I've analyzed your request. Should I proceed with the task: " + plan.get("task", "Unknown") + "?"}
    except Exception as e:
        logger.error("Error in /ask: %s", e, exc_info=True)
        return error_response("Failed to process instruction")

@app.get("/insights", dependencies=[Depends(verify_api_key)])
async def get_insights():
    try:
        plan = {"task": "GET_INSIGHTS"}
        result = await router.execute_task(plan)
        return result
    except Exception as e:
        logger.error("Error getting insights: %s", e, exc_info=True)
        return error_response("Insights currently unavailable")

@app.get("/stats", dependencies=[Depends(verify_api_key)])
async def get_stats():
    """Retrieve structured statistics about leads for charting."""
    try:
        if not db.client:
            return error_response("Database not connected", status_code=503)

        # 1. Fetch relevant columns for all leads (limit to 1000 for stats)
        response = db.client.table("leads").select("audit_status", "audit_results", "seo_score", "lead_source").execute()
        leads = response.data

        if not leads:
            return {
                "total_leads": 0,
                "audit_status_distribution": [],
                "seo_score_ranges": [],
                "source_distribution": []
            }

        df = pd.DataFrame(leads)

        # 2. Audit Status Distribution
        status_dist = df['audit_status'].value_counts().to_dict()
        status_list = [{"name": k, "value": int(v)} for k, v in status_dist.items()]

        # 3. SEO Score Ranges
        # Handle potential None/NaN in seo_score
        scores = pd.to_numeric(df['seo_score'], errors='coerce').dropna()
        score_bins = [0, 20, 40, 60, 80, 100]
        score_labels = ["0-20", "21-40", "41-60", "61-80", "81-100"]
        score_ranges = pd.cut(scores, bins=score_bins, labels=score_labels).value_counts().to_dict()
        score_list = [{"range": k, "count": int(v)} for k, v in score_ranges.items()]

        # 4. Source Distribution
        source_dist = df['lead_source'].fillna('Unknown').value_counts().head(5).to_dict()
        source_list = [{"name": k, "value": int(v)} for k, v in source_dist.items()]

        return {
            "total_leads": len(df),
            "audit_status_distribution": status_list,
            "seo_score_ranges": score_list,
            "source_distribution": source_list
        }
    except Exception as e:
        logger.error("Error fetching stats: %s", e, exc_info=True)
        return error_response("Failed to fetch stats")

@app.post("/draft-outreach", dependencies=[Depends(verify_api_key)])
async def draft_outreach(payload: LeadProcessRequest):
    plan = {
        "task": "OUTREACH_DRAFT",
        "params": {"unique_key": payload.unique_key}
    }

    result = await router.execute_task(plan)
    return result

@app.post("/draft-linkedin", dependencies=[Depends(verify_api_key)])
async def draft_linkedin(payload: LeadProcessRequest):
    plan = {
        "task": "LINKEDIN_DRAFT",
        "params": {"unique_key": payload.unique_key}
    }

    result = await router.execute_task(plan)
    return result

@app.post("/execute", dependencies=[Depends(verify_api_key)])
async def execute_plan(plan: ExecutePlanRequest, background_tasks: BackgroundTasks):
    """Execute a multi-step plan previously proposed by the AI."""
    plan_dict = plan.model_dump()
    if plan.task == "SEO_AUDIT":
        job_id = await orchestrator.run_massive_pipeline(tasks=["audit"])
        return {"result": {"message": "Scaling SEO Audit started", "job_id": job_id}}

    result = await router.execute_task(plan_dict)
    return {"result": result}

@app.post("/hunt-lead", dependencies=[Depends(verify_api_key)])
async def hunt_single_lead(payload: LeadProcessRequest):
    job_id = await orchestrator.run_massive_pipeline(lead_ids=[payload.unique_key], tasks=["hunt"])
    return {"status": "hunting_started", "unique_key": payload.unique_key, "job_id": job_id}

@app.post("/hunt-all", dependencies=[Depends(verify_api_key)])
async def hunt_all_leads():
    """Start a deep digital hunt for all leads missing social data."""
    job_id = await orchestrator.run_massive_pipeline(tasks=["hunt"])
    return {"status": "job_started", "job_id": job_id}

@app.post("/discovery/start", dependencies=[Depends(verify_api_key)])
async def start_discovery(payload: DiscoveryRequest):
    """Start a deep discovery search on Google Maps for new leads in the background."""
    job_id = await orchestrator.run_discovery_job(payload.query, payload.location)
    return {"status": "discovery_started", "job_id": job_id, "query": payload.query, "location": payload.location}

@app.post("/enrich/start", dependencies=[Depends(verify_api_key)])
async def start_enrichment(payload: LeadProcessRequest):
    """Trigger the enrichment engine to find missing digital footprints via orchestrator."""
    job_id = await orchestrator.run_massive_pipeline(lead_ids=[payload.unique_key], tasks=["enrich"])
    return {"status": "enrichment_started", "unique_key": payload.unique_key, "job_id": job_id}

@app.delete("/leads/clear", dependencies=[Depends(verify_api_key)])
async def clear_leads():
    """Purge all leads and job history (Danger Zone)."""
    db.delete_all_leads()
    db.delete_all_jobs()
    return {"status": "cleared", "message": "All leads and jobs have been deleted."}

@app.post("/orchestrator/start", dependencies=[Depends(verify_api_key)])
async def start_massive_pipeline(payload: PipelineRequest):
    job_id = await orchestrator.run_massive_pipeline(filters=payload.filters, lead_ids=payload.lead_ids, tasks=payload.tasks)
    return {"status": "job_started", "job_id": job_id}

@app.get("/orchestrator/status/{job_id}", dependencies=[Depends(verify_api_key)])
async def get_job_status(job_id: str):
    status = await orchestrator.get_job_status(job_id)
    return status

@app.post("/orchestrator/stop/{job_id}", dependencies=[Depends(verify_api_key)])
async def stop_job(job_id: str):
    result = await orchestrator.stop_job(job_id)
    return result

@app.get("/export", dependencies=[Depends(verify_api_key)])
async def trigger_export():
    try:
        export_leads()
        return {"message": "Exports generated successfully in the 'exports' directory."}
    except Exception as e:
        logger.error("Export error: %s", e, exc_info=True)
        return error_response("Export failed")

@app.get("/export/download", dependencies=[Depends(verify_api_key)])
async def download_full_export():
    try:
        # 1. Always regenerate for fresh data
        export_leads()

        # 2. Find the latest full export
        export_dir = "exports"
        if not os.path.exists(export_dir):
            return error_response("Exports directory not found after generation.", status_code=404)

        files = [f for f in os.listdir(export_dir) if f.startswith("full_leads_all_data_")]
        if not files:
            return error_response("No full export files found after generation.", status_code=404)

        latest_file = sorted(files)[-1]
        file_path = os.path.join(export_dir, latest_file)

        return FileResponse(
            path=file_path,
            filename=f"leads_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            media_type="text/csv"
        )
    except Exception as e:
        logger.error("Export download error: %s", e, exc_info=True)
        return error_response("Export download failed")

@app.get("/export/outreach", dependencies=[Depends(verify_api_key)])
async def download_outreach_export():
    try:
        export_leads()
        export_dir = "exports"
        files = [f for f in os.listdir(export_dir) if f.startswith("outreach_ready_leads_")]
        if not files:
            return error_response("No outreach export files found.", status_code=404)

        latest_file = sorted(files)[-1]
        file_path = os.path.join(export_dir, latest_file)

        return FileResponse(
            path=file_path,
            filename=f"crm_outreach_ready_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            media_type="text/csv"
        )
    except Exception as e:
        logger.error("Outreach export error: %s", e, exc_info=True)
        return error_response("Outreach export failed")


# ============================================================
# Campaign Management Endpoints (Step 4: Outreach)
# ============================================================

def _is_table_missing_error(e: Exception) -> bool:
    """Check if a Supabase error indicates a missing table (PGRST205)."""
    return "PGRST205" in str(e)

@app.post("/campaigns", dependencies=[Depends(verify_api_key)])
async def create_campaign(campaign: CampaignCreate):
    """Create a new outreach campaign."""
    try:
        import uuid
        campaign_data = {
            "id": str(uuid.uuid4()),
            "name": campaign.name,
            "channel": campaign.channel,
            "segment_filter": campaign.segment_filter,
            "status": "draft",
            "total_leads": 0,
            "sent_count": 0,
            "reply_count": 0,
        }
        result = db.client.table("campaigns").insert(campaign_data).execute()
        return {"campaign": result.data[0] if result.data else campaign_data}
    except Exception as e:
        if _is_table_missing_error(e):
            logger.warning("Campaigns table not found. Run the SQL from supabase_schema.sql to create it.")
            return error_response(
                "Campaigns table not created yet. Please run the campaigns migration SQL in Supabase SQL Editor.",
                status_code=503,
            )
        logger.error("Error creating campaign: %s", e, exc_info=True)
        return error_response("Failed to create campaign")

@app.get("/campaigns", dependencies=[Depends(verify_api_key)])
async def list_campaigns():
    """List all campaigns."""
    try:
        result = db.client.table("campaigns").select("*").order("created_at", desc=True).execute()
        return {"campaigns": result.data or []}
    except Exception as e:
        if _is_table_missing_error(e):
            return {"campaigns": [], "warning": "Campaigns table not created yet."}
        logger.error("Error listing campaigns: %s", e, exc_info=True)
        return error_response("Failed to list campaigns")

@app.get("/campaigns/{campaign_id}", dependencies=[Depends(verify_api_key)])
async def get_campaign(campaign_id: str):
    """Get campaign details with message statistics."""
    try:
        campaign = db.client.table("campaigns").select("*").eq("id", campaign_id).single().execute()

        # Performance optimization: Count stats at the database level instead of fetching all rows into memory
        stats = {"pending": 0, "sent": 0, "delivered": 0, "replied": 0, "bounced": 0}

        # We perform individual exact count queries for each status. This is much faster
        # and memory-efficient than returning potentially hundreds of thousands of full rows.
        for status in stats.keys():
            res = db.client.table("campaign_messages").select("id", count="exact").eq("campaign_id", campaign_id).eq("status", status).limit(1).execute()
            stats[status] = res.count or 0

        # We limit the payload to 50 messages to reduce network transfer time and API response size.
        # The frontend only displays the first 50 messages.
        messages = db.client.table("campaign_messages").select("*").eq("campaign_id", campaign_id).limit(50).execute()

        return {
            "campaign": campaign.data,
            "messages": messages.data or [],
            "stats": stats,
            "total_messages": sum(stats.values())
        }
    except Exception as e:
        logger.error("Error getting campaign %s: %s", campaign_id, e, exc_info=True)
        return error_response("Failed to get campaign")

@app.post("/campaigns/{campaign_id}/generate", dependencies=[Depends(verify_api_key)])
async def generate_campaign_messages(campaign_id: str, background_tasks: BackgroundTasks):
    """Generate personalized outreach messages for all leads in the campaign's segment."""
    try:
        campaign = db.client.table("campaigns").select("*").eq("id", campaign_id).single().execute()
        if not campaign.data:
            return error_response("Campaign not found", status_code=404)

        camp = campaign.data

        # Build lead query based on segment filter
        query = db.client.table("leads").select("*")
        if camp.get("segment_filter"):
            query = query.eq("segment", camp["segment_filter"])

        # Only leads with email for email campaigns, or linkedin for linkedin campaigns
        if camp["channel"] == "email":
            query = query.not_.is_("email", "null")
        elif camp["channel"] == "linkedin":
            query = query.not_.is_("linkedin", "null")

        leads_resp = query.execute()
        leads = leads_resp.data or []

        if not leads:
            return error_response("No matching leads found for this segment and channel.", status_code=404)

        # Generate messages in background
        async def generate_messages():
            from src.processors.leadhunter import LeadHunter
            hunter = LeadHunter()
            messages_to_insert = []

            for lead in leads:
                lead_name = lead.get("name") or lead.get("company_name") or "there"
                pain = lead.get("pain_points") or ""

                if camp["channel"] in ["email", "multi"]:
                    hook = lead.get("email_hook") or ""
                    company = lead.get("company_name") or lead_name
                    subject = f"Quick question about {company}"
                    if hook:
                        body = f"Hi {{{{first_name}}}},\n\n{hook}\n\nI'd love to share a few specific ideas that could help. Would you be open to a quick 10-minute chat this week?\n\nBest,"
                    else:
                        body = f"Hi {{{{first_name}}}},\n\nI came across {company}'s website and noticed a few areas where you might be leaving growth on the table. {pain[:200]}\n\nWould you be open to a quick chat about it?\n\nBest,"

                    messages_to_insert.append({
                        "campaign_id": campaign_id,
                        "lead_unique_key": lead["unique_key"],
                        "channel": "email",
                        "subject": subject,
                        "body": body,
                        "status": "pending"
                    })

                if camp["channel"] in ["linkedin", "multi"]:
                    hook = lead.get("linkedin_hook") or ""
                    company = lead.get("company_name") or lead_name
                    body = hook if hook else f"Hi, I came across {company} and was impressed by what you're building. I work in a similar space and would love to connect."

                    messages_to_insert.append({
                        "campaign_id": campaign_id,
                        "lead_unique_key": lead["unique_key"],
                        "channel": "linkedin",
                        "subject": None,
                        "body": body,
                        "status": "pending"
                    })

            if messages_to_insert:
                db.client.table("campaign_messages").insert(messages_to_insert).execute()
                db.client.table("campaigns").update({
                    "total_leads": len(leads),
                    "status": "draft"
                }).eq("id", campaign_id).execute()

        await generate_messages()

        return {"status": "generated", "lead_count": len(leads)}
    except Exception as e:
        logger.error("Error generating campaign messages: %s", e, exc_info=True)
        return error_response("Failed to generate campaign messages")

@app.post("/campaigns/{campaign_id}/start", dependencies=[Depends(verify_api_key)])
async def start_campaign(campaign_id: str):
    """Mark campaign as active (actual sending would be handled by email_sender integration)."""
    try:
        db.client.table("campaigns").update({
            "status": "active"
        }).eq("id", campaign_id).execute()
        return {"status": "active", "message": "Campaign started. Messages will be sent according to rate limits."}
    except Exception as e:
        logger.error("Error starting campaign %s: %s", campaign_id, e, exc_info=True)
        return error_response("Failed to start campaign")

@app.post("/campaigns/{campaign_id}/pause", dependencies=[Depends(verify_api_key)])
async def pause_campaign(campaign_id: str):
    """Pause a running campaign."""
    try:
        db.client.table("campaigns").update({
            "status": "paused"
        }).eq("id", campaign_id).execute()
        return {"status": "paused"}
    except Exception as e:
        logger.error("Error pausing campaign %s: %s", campaign_id, e, exc_info=True)
        return error_response("Failed to pause campaign")

@app.get("/campaigns/{campaign_id}/export", dependencies=[Depends(verify_api_key)])
async def export_campaign_messages(campaign_id: str):
    """Export campaign messages as CSV for import into external tools."""
    try:
        messages = db.client.table("campaign_messages").select(
            "lead_unique_key, channel, subject, body, status"
        ).eq("campaign_id", campaign_id).execute()

        if not messages.data:
            return error_response("No messages found for this campaign.", status_code=404)

        df = pd.DataFrame(messages.data)

        # Enrich with lead data
        unique_keys = df["lead_unique_key"].unique().tolist()
        leads_resp = db.client.table("leads").select(
            "unique_key, name, email, linkedin, company_name, first_name"
        ).in_("unique_key", unique_keys).execute()

        leads_df = pd.DataFrame(leads_resp.data) if leads_resp.data else pd.DataFrame()
        if not leads_df.empty:
            df = df.merge(leads_df, left_on="lead_unique_key", right_on="unique_key", how="left")

        export_path = f"exports/campaign_{campaign_id[:8]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        os.makedirs("exports", exist_ok=True)
        df.to_csv(export_path, index=False)

        return FileResponse(
            path=export_path,
            filename=f"campaign_export_{datetime.now().strftime('%Y%m%d')}.csv",
            media_type="text/csv"
        )
    except Exception as e:
        logger.error("Error exporting campaign messages: %s", e, exc_info=True)
        return error_response("Failed to export campaign messages")


if __name__ == "__main__":
    debug = os.getenv("DEBUG", "False").lower() == "true"
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=debug)
