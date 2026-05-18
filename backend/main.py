from fastapi import FastAPI, UploadFile, File, BackgroundTasks, Depends, Security, HTTPException, Request
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn
import os
import secrets
import uuid
import pandas as pd
import aiofiles
from datetime import datetime
from typing import Literal, Optional, List
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, conlist, constr
from postgrest.exceptions import APIError
from fastapi.security import APIKeyHeader
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

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
    if not key or not secrets.compare_digest(key, expected):
        raise HTTPException(status_code=403, detail="Invalid or missing API key")
    return key


# Defence-in-depth: destructive endpoints require a second secret that must
# never be exposed to the browser (no NEXT_PUBLIC_* equivalent). Even if the
# API key leaks, an attacker cannot wipe data without ADMIN_TOKEN.
admin_token_header = APIKeyHeader(name="X-Admin-Token", auto_error=False)


async def verify_admin_token(token: Optional[str] = Security(admin_token_header)) -> str:
    expected = os.getenv("ADMIN_TOKEN")
    if not expected:
        logger.warning("ADMIN_TOKEN not set — destructive endpoints are disabled.")
        raise HTTPException(status_code=403, detail="Admin token not configured")
    if not token or not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="Invalid or missing admin token")
    return token


def error_response(message, status_code=500) -> JSONResponse:
    body = {"error": message}
    return JSONResponse(content=body, status_code=status_code)


# All inbound JSON models pin `extra='forbid'` (mass-assignment defense) and
# every string/list has bounded length (memory-DoS defense pre-handler).
# Free-form enum-like fields (`channel`, `status`) use Literal to keep DB
# values constrained at the boundary.

CampaignChannel = Literal["email", "linkedin", "multi"]
CampaignStatus = Literal["draft", "active", "paused", "completed", "archived"]
# Allowlist of columns callers may filter on in PipelineRequest. Keeping this
# tight stops the caller from probing arbitrary DB columns via error messages
# and bypassing intended segment scoping.
_PIPELINE_FILTER_KEYS = frozenset({
    "segment", "audit_status", "high_risk_flag", "company_size", "campaign_id",
    "country", "city", "language", "outreach_score", "seo_score",
})


class CampaignCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: constr(min_length=1, max_length=200)
    channel: CampaignChannel
    segment_filter: Optional[constr(max_length=200)] = None

class CampaignUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Optional[constr(min_length=1, max_length=200)] = None
    status: Optional[CampaignStatus] = None

class LeadProcessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unique_key: constr(min_length=1, max_length=128)

class AskInstruction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Cap the prompt that flows into Gemini to bound billing per request and
    # keep raw prompt-injection blobs from being forwarded.
    text: constr(min_length=1, max_length=4000)

class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instruction: AskInstruction

class DiscoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: constr(min_length=1, max_length=500)
    location: Optional[constr(max_length=200)] = ""

class PipelineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filters: Optional[dict] = None
    lead_ids: Optional[conlist(constr(min_length=1, max_length=128), max_length=10_000)] = None
    tasks: Optional[conlist(constr(min_length=1, max_length=64), max_length=64)] = None

# /execute is the AI router's "execute the proposed plan" surface. Lock the
# task name to the AgenticRouter handler allowlist and bound each accepted
# param. Without this an authed caller could craft any task/params dict and
# bypass the natural-language → tool gating that the rest of the flow relies
# on. Keys mirror what `AgenticRouter.execute_task` handlers actually read.
ExecutableTask = Literal[
    "DATABASE_QUERY",
    "STATUS_CHECK",
    "SEO_AUDIT",
    "OUTREACH_DRAFT",
    "GET_INSIGHTS",
    "DATA_MERGE",
    "DEEP_HUNT",
    "RUN_MASSIVE_PIPELINE",
    "LINKEDIN_DRAFT",
    "DISCOVERY_SEARCH",
    "DEEP_ENRICHMENT",
    "CAMPAIGN_STRATEGY",
]

class ExecutePlanParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unique_key: Optional[constr(min_length=1, max_length=128)] = None
    query: Optional[constr(min_length=1, max_length=500)] = None
    location: Optional[constr(max_length=200)] = None
    # Natural-language sub-question fenced as UNTRUSTED_DATA by the handler.
    query_text: Optional[constr(max_length=4000)] = None
    # Free-form bucket label ("high-risk" etc.). Handler treats anything other
    # than "high-risk" as "default" — bounded string is enough.
    filters: Optional[constr(max_length=64)] = None
    type: Optional[constr(max_length=64)] = None

class ExecutePlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: ExecutableTask
    params: Optional[ExecutePlanParams] = None

load_dotenv()


def _format_insights_response(result: dict) -> Optional[str]:
    """Render a GET_INSIGHTS result dict into a plain-text chat reply.

    GET_INSIGHTS returns `{summary, insights[], top_priorities[]}` — none of
    which match the `answer`/`message` fields the chat UI auto-extracts. This
    helper flattens it into a single paragraph so the assistant doesn't fall
    back to the generic "couldn't generate" message.
    """
    insights = result.get("insights") or []
    priorities = result.get("top_priorities") or []
    if not insights and not priorities:
        return None
    parts = []
    if result.get("summary"):
        parts.append(str(result["summary"]))
    for i, ins in enumerate(insights, 1):
        parts.append(f"{i}. {ins}")
    if priorities:
        parts.append("Top priorities:")
        for p in priorities[:5]:
            name = p.get("name", "Unknown") if isinstance(p, dict) else str(p)
            reason = p.get("reason", "") if isinstance(p, dict) else ""
            parts.append(f"- {name}" + (f": {reason}" if reason else ""))
    return "\n\n".join(parts) if parts else None


def _assert_single_tenant_if_enforced() -> None:
    """Optional single-tenancy invariant.

    The app's per-resource endpoints (`/process-lead`, `/draft-outreach`,
    `/orchestrator/status/{job_id}`, `/campaigns/{id}/...`) intentionally
    have no `owner_user_id` filter — the design assumes a single operator
    provisioned manually in the Supabase Auth dashboard. If a second user
    is ever added, every authed user gains full cross-user access.

    Setting `OPERATOR_EMAIL` enables a startup check that fails loudly if
    that invariant ever breaks (extra user added, or expected user not
    provisioned). Unset → check is skipped, behavior unchanged.
    """
    expected = os.getenv("OPERATOR_EMAIL", "").strip().lower()
    if not expected:
        return
    if not db.client:
        logger.warning("OPERATOR_EMAIL set but Supabase client missing — skipping tenancy check.")
        return
    try:
        users_resp = db.client.auth.admin.list_users()
        # supabase-py returns a list directly; tolerate paginated objects too.
        users = users_resp if isinstance(users_resp, list) else getattr(users_resp, "users", []) or []
        emails = [
            (getattr(u, "email", None) or "").strip().lower()
            for u in users
        ]
        emails = [e for e in emails if e]
        if emails != [expected]:
            raise RuntimeError(
                f"Single-tenant invariant violated: expected exactly [{expected}], "
                f"found {emails}. Either remove extra users or migrate to "
                "owner-scoped endpoints before continuing."
            )
        logger.info("Single-tenancy assertion passed (operator=%s).", expected)
    except RuntimeError:
        raise
    except Exception as e:
        # Don't hard-fail the boot for a transient Supabase Auth API hiccup —
        # log loudly so the invariant is still observable.
        logger.warning("Single-tenancy check could not run: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger.info("Lead Data Scraper Backend Starting...")
    _assert_single_tenant_if_enforced()
    try:
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
        await orchestrator.recover_interrupted_jobs()
        if not missing:
            logger.info("Database schema is up to date.")
    except Exception as e:
        logger.warning("Startup DB checks skipped — database unreachable: %s", e)
    yield

_docs_enabled = os.getenv("ENABLE_DOCS", "false").lower() == "true"
app = FastAPI(
    title="LeadDataScraper API",
    lifespan=lifespan,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)
db = SupabaseHelper()
router = AgenticRouter()
auditor = ParallelAuditor()
orchestrator = TaskOrchestrator()

# --- Rate limiting ---
# Trust X-Forwarded-For only when the request carries a valid API key. The Next.js
# proxy is the only legitimate holder of API_SECRET_KEY, so a matching key proves
# the XFF was set by the proxy (which strips client-supplied XFF). Without this
# guard, anyone hitting the backend directly could spoof XFF to spread load
# across rate-limit buckets.
def _rate_limit_key(request: Request) -> str:
    expected = os.getenv("API_SECRET_KEY") or ""
    api_key = request.headers.get("x-api-key") or ""
    if expected and api_key and secrets.compare_digest(api_key, expected):
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return get_remote_address(request)

limiter = Limiter(key_func=_rate_limit_key, headers_enabled=False)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
# Note: only endpoints decorated with @limiter.limit(...) are rate-limited.
# Adding SlowAPIMiddleware here would enable Limiter default_limits globally,
# but we prefer per-endpoint explicit caps so reads (/leads, /stats) and writes
# can have different budgets.


@app.exception_handler(Exception)
async def _json_exception_handler(request: Request, exc: Exception):
    """Ensure all uncaught exceptions return JSON, not plain-text 500 (browser clients call .json())."""
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(content={"error": "Internal server error"}, status_code=500)

# Configure CORS — explicit origins only. Wildcards are rejected.
allowed_origins_env = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000")
allowed_origins = [origin.strip() for origin in allowed_origins_env.split(",") if origin.strip()]
allowed_origins = [origin for origin in allowed_origins if origin != "*"]
if not allowed_origins:
    logger.warning(
        "ALLOWED_ORIGINS is empty or contained only wildcards. CORS is locked down "
        "and the browser will block cross-origin requests. Set ALLOWED_ORIGINS to a "
        "comma-separated list of trusted origins for production."
    )

# Refuse to start with a wildcard + credentials combination. Browsers reject it
# anyway, but the assert here makes the security invariant explicit: a future
# edit that drops the strip above will fail loudly instead of silently shipping
# `Access-Control-Allow-Origin: *` with cookie credentials.
assert "*" not in allowed_origins, "CORS misconfiguration: '*' is incompatible with allow_credentials=True"

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key", "X-Admin-Token"],
)

@app.get("/")
async def root():
    """Unauthenticated liveness probe. Intentionally returns no product /
    version metadata — anything richer is a free fingerprint for attackers."""
    return {"status": "ok"}

@app.get("/leads", dependencies=[Depends(verify_api_key)])
@limiter.limit("30/minute")
async def list_leads(request: Request):
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

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50MB


def validate_csv_metadata(file: UploadFile) -> Optional[JSONResponse]:
    """Validate filename + content-type before reading body."""
    if not file.filename or not file.filename.lower().endswith('.csv'):
        return error_response("Only CSV files are allowed.", status_code=400)

    if file.content_type and file.content_type not in ["text/csv", "application/vnd.ms-excel", "application/octet-stream"]:
        return error_response(f"Invalid content type: {file.content_type}. Expected text/csv.", status_code=400)
    return None


async def read_capped(file: UploadFile, max_bytes: int) -> tuple[Optional[bytes], Optional[JSONResponse]]:
    """Stream-read upload, abort once size exceeds max_bytes."""
    chunks = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)  # 1MB
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            return None, error_response(
                f"File too large. Maximum size is {max_bytes // (1024*1024)}MB.",
                status_code=413,
            )
        chunks.append(chunk)
    return b"".join(chunks), None

@app.post("/upload", dependencies=[Depends(verify_api_key)])
@limiter.limit("5/minute")
async def upload_leads(request: Request, background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    """
    Handle CSV file upload, map columns using AI, and upsert leads to the database.
    Processing happens in the background.
    """
    meta_error = validate_csv_metadata(file)
    if meta_error:
        return meta_error
    contents, size_error = await read_capped(file, MAX_UPLOAD_BYTES)
    if size_error:
        return size_error

    # Save uploaded file temporarily — UUID name under system tempdir to
    # prevent path traversal and keep uploads out of the cwd.
    import tempfile
    temp_path = os.path.join(tempfile.gettempdir(), f"leadscraper_{uuid.uuid4().hex}.csv")
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
    from pathlib import Path
    try:
        df = _load_and_standardize_csv(temp_path)
        df = _apply_ai_mapping(df)
        final_df = _filter_valid_columns(df)
        upserted_count = _upsert_leads_to_db(final_df)
        logger.info("Successfully processed and upserted %d leads.", upserted_count)
    except Exception as e:
        logger.error("Error processing upload: %s", e, exc_info=True)
    finally:
        Path(temp_path).unlink(missing_ok=True)

@app.post("/process-lead", dependencies=[Depends(verify_api_key)])
@limiter.limit("20/minute")
async def process_single_lead(request: Request, payload: LeadProcessRequest):
    """Trigger a single lead SEO audit and enrichment via orchestrator."""
    job_id = await orchestrator.run_massive_pipeline(lead_ids=[payload.unique_key])
    return {"status": "started", "unique_key": payload.unique_key, "job_id": job_id}

@app.post("/process-all", dependencies=[Depends(verify_api_key)])
@limiter.limit("3/minute")
async def process_all_pending(request: Request):
    """Trigger the audit orchestrator to process all pending leads."""
    job_id = await orchestrator.run_massive_pipeline(tasks=["audit"])
    return {"status": "job_started", "job_id": job_id}

@app.get("/audit-status", dependencies=[Depends(verify_api_key)])
@limiter.limit("60/minute")
async def get_audit_status(request: Request):
    """
    Get the current status of the batch audit process.
    Legacy endpoint for single-batch monitoring.
    """
    if auditor.status.get("active"):
        return auditor.status

    if not db.client:
        return error_response("Database not connected", status_code=503)

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
@limiter.limit("10/minute")
async def stop_audit(request: Request):
    """Signal the orchestrator to stop all running jobs."""
    if not db.client:
        return error_response("Database not connected", status_code=503)
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



@app.post("/ask", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
async def ask_ai(request: Request, payload: AskRequest, background_tasks: BackgroundTasks):
    """
    Process natural language instructions.
    Can execute simple tasks immediately or propose a multi-step plan for confirmation.
    """
    try:
        prompt = payload.instruction.text

        # 1. Route the instruction to a task
        plan = await router.route_instruction(prompt)

        # 2. For informational or read-only tasks, execute immediately and
        # surface the result as plain text. GET_INSIGHTS is included because
        # analytical questions ("which industry has the worst SEO?") should
        # return an answer, not a "Confirm task" plan card.
        if plan.get("task") in ["DATABASE_QUERY", "STATUS_CHECK", "GET_INSIGHTS"]:
            result = await router.execute_task(plan)
            text = (
                result.get("answer")
                or result.get("message")
                or _format_insights_response(result)
                or result.get("summary")
                or "I couldn't generate an answer — try rephrasing your question."
            )
            return {"response": text}

        # 3. Small-talk / unmapped prompts: the router returns task=UNKNOWN with
        # the model's free-text reply in `raw`. Return that as plain text so the
        # UI doesn't show a meaningless "Confirm task: UNKNOWN" plan card.
        if plan.get("task") == "UNKNOWN":
            text = plan.get("raw") or "I'm not sure what you'd like me to do. Try asking about your leads, scores, or audits."
            return {"response": text}

        # 4. Process-heavy tasks: return the plan for UI confirmation.
        return {"plan": plan, "response": "I've analyzed your request. Should I proceed with the task: " + plan.get("task", "Unknown") + "?"}
    except Exception as e:
        logger.error("Error in /ask: %s", e, exc_info=True)
        return error_response("Failed to process instruction")

@app.get("/insights", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
async def get_insights(request: Request):
    if not db.client:
        return error_response("Database not connected", status_code=503)
    try:
        plan = {"task": "GET_INSIGHTS"}
        result = await router.execute_task(plan)
        # If the router surfaced an error payload, propagate the right status
        # instead of returning HTTP 200 with an error body.
        if isinstance(result, dict) and result.get("error"):
            return error_response(result["error"], status_code=503)
        return result
    except Exception as e:
        logger.error("Error getting insights: %s", e, exc_info=True)
        return error_response("Insights currently unavailable")

@app.get("/stats", dependencies=[Depends(verify_api_key)])
@limiter.limit("30/minute")
async def get_stats(request: Request):
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
@limiter.limit("20/minute")
async def draft_outreach(request: Request, payload: LeadProcessRequest):
    plan = {
        "task": "OUTREACH_DRAFT",
        "params": {"unique_key": payload.unique_key}
    }

    result = await router.execute_task(plan)
    return result

@app.post("/draft-linkedin", dependencies=[Depends(verify_api_key)])
@limiter.limit("20/minute")
async def draft_linkedin(request: Request, payload: LeadProcessRequest):
    plan = {
        "task": "LINKEDIN_DRAFT",
        "params": {"unique_key": payload.unique_key}
    }

    result = await router.execute_task(plan)
    return result

@app.post("/execute", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
async def execute_plan(request: Request, plan: ExecutePlanRequest, background_tasks: BackgroundTasks):
    """Execute a multi-step plan previously proposed by the AI."""
    # exclude_none so unset Pydantic fields don't shadow handler defaults
    # (e.g. _generate_campaign_strategy uses params.get("filters", "high-risk")
    # which would otherwise resolve to None instead of the intended default).
    plan_dict = plan.model_dump(exclude_none=True)
    if plan.task == "SEO_AUDIT":
        job_id = await orchestrator.run_massive_pipeline(tasks=["audit"])
        return {"result": {"message": "Scaling SEO Audit started", "job_id": job_id}}

    result = await router.execute_task(plan_dict)
    return {"result": result}

@app.post("/hunt-lead", dependencies=[Depends(verify_api_key)])
@limiter.limit("20/minute")
async def hunt_single_lead(request: Request, payload: LeadProcessRequest):
    job_id = await orchestrator.run_massive_pipeline(lead_ids=[payload.unique_key], tasks=["hunt"])
    return {"status": "hunting_started", "unique_key": payload.unique_key, "job_id": job_id}

@app.post("/hunt-all", dependencies=[Depends(verify_api_key)])
@limiter.limit("3/minute")
async def hunt_all_leads(request: Request):
    """Start a deep digital hunt for all leads missing social data."""
    job_id = await orchestrator.run_massive_pipeline(tasks=["hunt"])
    return {"status": "job_started", "job_id": job_id}

@app.post("/discovery/start", dependencies=[Depends(verify_api_key)])
@limiter.limit("5/minute")
async def start_discovery(request: Request, payload: DiscoveryRequest):
    """Start a deep discovery search on Google Maps for new leads in the background."""
    job_id = await orchestrator.run_discovery_job(payload.query, payload.location)
    return {"status": "discovery_started", "job_id": job_id, "query": payload.query, "location": payload.location}

@app.post("/enrich/start", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
async def start_enrichment(request: Request, payload: LeadProcessRequest):
    """Trigger the enrichment engine to find missing digital footprints via orchestrator."""
    job_id = await orchestrator.run_massive_pipeline(lead_ids=[payload.unique_key], tasks=["enrich"])
    return {"status": "enrichment_started", "unique_key": payload.unique_key, "job_id": job_id}

@app.delete(
    "/leads/clear",
    dependencies=[Depends(verify_api_key), Depends(verify_admin_token)],
)
@limiter.limit("3/hour")
async def clear_leads(request: Request):
    """Purge all leads and job history (Danger Zone). Requires X-Admin-Token."""
    db.delete_all_leads()
    db.delete_all_jobs()
    logger.warning("DESTRUCTIVE: /leads/clear invoked — all leads + jobs wiped.")
    return {"status": "cleared", "message": "All leads and jobs have been deleted."}

@app.post("/orchestrator/start", dependencies=[Depends(verify_api_key)])
@limiter.limit("3/minute")
async def start_massive_pipeline(request: Request, payload: PipelineRequest):
    if not db.client:
        return error_response("Database not connected", status_code=503)
    job_id = await orchestrator.run_massive_pipeline(filters=payload.filters, lead_ids=payload.lead_ids, tasks=payload.tasks)
    return {"status": "job_started", "job_id": job_id}

@app.get("/orchestrator/status/{job_id}", dependencies=[Depends(verify_api_key)])
@limiter.limit("60/minute")
async def get_job_status(request: Request, job_id: str):
    if not db.client:
        return error_response("Database not connected", status_code=503)
    status = await orchestrator.get_job_status(job_id)
    return status

@app.post("/orchestrator/stop/{job_id}", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
async def stop_job(request: Request, job_id: str):
    if not db.client:
        return error_response("Database not connected", status_code=503)
    result = await orchestrator.stop_job(job_id)
    return result

@app.get("/export", dependencies=[Depends(verify_api_key)])
@limiter.limit("6/hour")
async def trigger_export(request: Request):
    try:
        export_leads()
        return {"message": "Exports generated successfully in the 'exports' directory."}
    except Exception as e:
        logger.error("Export error: %s", e, exc_info=True)
        return error_response("Export failed")

@app.get("/export/download", dependencies=[Depends(verify_api_key)])
@limiter.limit("6/hour")
async def download_full_export(request: Request):
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
@limiter.limit("6/hour")
async def download_outreach_export(request: Request):
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
@limiter.limit("20/minute")
async def create_campaign(request: Request, campaign: CampaignCreate):
    """Create a new outreach campaign."""
    if not db.client:
        return error_response("Database not connected", status_code=503)
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
@limiter.limit("60/minute")
async def list_campaigns(request: Request):
    """List all campaigns."""
    if not db.client:
        return error_response("Database not connected", status_code=503)
    try:
        result = db.client.table("campaigns").select("*").order("created_at", desc=True).execute()
        return {"campaigns": result.data or []}
    except Exception as e:
        if _is_table_missing_error(e):
            return {"campaigns": [], "warning": "Campaigns table not created yet."}
        logger.error("Error listing campaigns: %s", e, exc_info=True)
        return error_response("Failed to list campaigns")

@app.get("/campaigns/{campaign_id}", dependencies=[Depends(verify_api_key)])
@limiter.limit("60/minute")
async def get_campaign(request: Request, campaign_id: str):
    """Get campaign details with message statistics."""
    if not db.client:
        return error_response("Database not connected", status_code=503)
    try:
        # maybe_single returns data=None on 0 rows instead of raising APIError;
        # lets us return a proper 404 instead of falling through to the generic 500.
        campaign = db.client.table("campaigns").select("*").eq("id", campaign_id).maybe_single().execute()
        if not campaign or not campaign.data:
            return error_response("Campaign not found", status_code=404)

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
@limiter.limit("3/minute")
async def generate_campaign_messages(request: Request, campaign_id: str, background_tasks: BackgroundTasks):
    """Generate personalized outreach messages for all leads in the campaign's segment."""
    if not db.client:
        return error_response("Database not connected", status_code=503)
    try:
        # maybe_single() — same reasoning as get_campaign: don't let 0-row
        # APIError get swallowed by the broad except below.
        campaign = db.client.table("campaigns").select("*").eq("id", campaign_id).maybe_single().execute()
        if not campaign or not campaign.data:
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
@limiter.limit("10/minute")
async def start_campaign(request: Request, campaign_id: str):
    """Mark campaign as active (actual sending would be handled by email_sender integration)."""
    if not db.client:
        return error_response("Database not connected", status_code=503)
    try:
        db.client.table("campaigns").update({
            "status": "active"
        }).eq("id", campaign_id).execute()
        return {"status": "active", "message": "Campaign started. Messages will be sent according to rate limits."}
    except Exception as e:
        logger.error("Error starting campaign %s: %s", campaign_id, e, exc_info=True)
        return error_response("Failed to start campaign")

@app.post("/campaigns/{campaign_id}/pause", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
async def pause_campaign(request: Request, campaign_id: str):
    """Pause a running campaign."""
    if not db.client:
        return error_response("Database not connected", status_code=503)
    try:
        db.client.table("campaigns").update({
            "status": "paused"
        }).eq("id", campaign_id).execute()
        return {"status": "paused"}
    except Exception as e:
        logger.error("Error pausing campaign %s: %s", campaign_id, e, exc_info=True)
        return error_response("Failed to pause campaign")

@app.get("/campaigns/{campaign_id}/export", dependencies=[Depends(verify_api_key)])
@limiter.limit("12/hour")
async def export_campaign_messages(request: Request, campaign_id: str):
    """Export campaign messages as CSV for import into external tools."""
    if not db.client:
        return error_response("Database not connected", status_code=503)
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
