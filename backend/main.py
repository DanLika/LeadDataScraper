import asyncio
import base64
import csv
import io
import json
import re
import zipfile
from fastapi import (
    FastAPI,
    UploadFile,
    File,
    BackgroundTasks,
    Depends,
    Query,
    Security,
    HTTPException,
    Request,
)
from fastapi.exceptions import RequestValidationError
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn
import os
import secrets
import uuid
import aiofiles
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Literal, Optional
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, conlist

# `safe_constr` is a drop-in for pydantic.constr that also rejects NUL /
# Unicode Cc / Cf (except tab/LF/CR). Closes the 500 path observed in QA
# terminal-6 sweep 2026-05-28 (POST /discovery/start + POST /campaigns).
# See src/schemas/sanitized_str.py for rationale + allowed-control list.
from src.schemas.sanitized_str import safe_constr
from postgrest.exceptions import APIError
from fastapi.security import APIKeyHeader
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

# Cold-start budget on Render's free tier is tight (process is killed after
# inactivity; first request triggers a fresh `python backend.main` import).
# Heavy chains — pandas (~300ms), google.genai via AgenticRouter (~210ms),
# playwright via TaskOrchestrator (~150ms), aiohttp via ParallelAuditor
# (~140ms) — are deferred to the first request that actually needs them.
# Liveness probe `GET /` and auth-key validation don't pull any of these,
# so the wake-up handshake returns in ~module-init-only time.
#
# `pandas` is referenced in CSV ingest + /stats build + campaign export.
# TYPE_CHECKING import keeps the type annotations meaningful for IDE /
# mypy without paying the runtime import cost; quoted annotations below
# (`"pd.DataFrame"`) ensure no eager resolution at class-definition time.
if TYPE_CHECKING:
    import pandas as pd  # noqa: F401 — used only for type hints
from src.utils.logging_config import (
    setup_logging,
    get_logger,
    bind_request_context,
)
from src.utils.stats_cache import stats_cache
from src.errors import AIQuotaExceededError
from src.utils.gemini_budget import (
    BudgetExceededError,
    get_state as _get_gemini_budget_state,
)
from src.types.providers import WebhookProvider
from src.repositories.webhook_event_repo import WebhookEventRepository

# Single source-of-truth provider tag for the Instantly webhook ingest path.
# Annotated WebhookProvider so a future widening of webhook_events_provider_allowed
# CHECK that drops 'instantly' (or a typo) trips mypy at the repo boundary.
_INSTANTLY_WEBHOOK_PROVIDER: WebhookProvider = "instantly"
from fastapi.responses import FileResponse


# ---------------------------------------------------------------------------
# Lazy module-level singletons.
#
# `db`, `router`, `auditor`, `orchestrator` used to be eager top-level
# instances:
#     db = SupabaseHelper(); router = AgenticRouter(); ...
# That fired the import chains for supabase / google.genai / playwright at
# `import backend.main` time, blocking uvicorn's bind. Now they're resolved
# lazily through module __getattr__ on first attribute access — the chain
# fires only when the matching route handler runs. Result is cached back
# into module globals so subsequent lookups hit the normal dict path with
# zero overhead.
#
# Trade-off: the FIRST request that hits a path touching these (e.g. the
# first /ask call) pays the full per-singleton import cost. All later
# requests get the cached instance. Cold-start liveness probe doesn't pay
# any of them.
# ---------------------------------------------------------------------------
def __getattr__(name: str):
    if name == "db":
        from src.utils.supabase_helper import SupabaseHelper

        instance = SupabaseHelper()
        globals()["db"] = instance
        return instance
    if name == "router":
        from src.core.agentic_router import AgenticRouter

        instance = AgenticRouter()
        globals()["router"] = instance
        return instance
    if name == "auditor":
        from src.core.parallel_auditor import ParallelAuditor

        instance = ParallelAuditor()
        globals()["auditor"] = instance
        return instance
    if name == "orchestrator":
        from src.core.task_orchestrator import TaskOrchestrator

        instance = TaskOrchestrator()
        globals()["orchestrator"] = instance
        return instance
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


logger = get_logger(__name__)

# --- Sentry init (errors @ 100%, traces @ 10%; skipped if SENTRY_DSN unset) ---
# Runs at module import time so failures during FastAPI construction +
# lifespan get captured. RELEASE_SHA is baked into the image at build
# time (Dockerfile ARG/ENV + deploy-backend.yml build-args) — required
# for source-map / commit resolution of stack traces in Sentry.
#
# `before_send` scrubs our custom auth headers (X-API-Key, X-Admin-Token)
# — Sentry's default scrubber only knows about Authorization / Cookie —
# and drops /upload bodies (CSV PII from operator file uploads).
load_dotenv()  # idempotent; makes SENTRY_DSN visible in dev (uvicorn doesn't auto-load .env)
_SENTRY_DSN = os.getenv("SENTRY_DSN", "").strip()
if _SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    _SCRUB_HEADERS = frozenset(
        {"x-api-key", "x-admin-token", "authorization", "cookie"}
    )

    def _scrub_sensitive(event, hint):  # pragma: no cover — Sentry-only path
        req = event.get("request") or {}
        headers = req.get("headers") or {}
        for k in list(headers.keys()):
            if k.lower() in _SCRUB_HEADERS:
                headers[k] = "[scrubbed]"
        # /upload body is CSV — likely contains lead PII. Drop entirely.
        if (req.get("url") or "").endswith("/upload"):
            req.pop("data", None)
        return event

    sentry_sdk.init(
        dsn=_SENTRY_DSN,
        environment=os.getenv("SENTRY_ENVIRONMENT", "production"),
        release=os.getenv("RELEASE_SHA", "unknown"),
        sample_rate=1.0,  # capture every error
        traces_sample_rate=0.1,  # 10% transaction sampling for perf
        send_default_pii=False,
        max_breadcrumbs=50,
        before_send=_scrub_sensitive,
        integrations=[StarletteIntegration(), FastApiIntegration()],
    )
    logger.info(
        "Sentry initialized (release=%s, env=%s)",
        os.getenv("RELEASE_SHA", "unknown"),
        os.getenv("SENTRY_ENVIRONMENT", "production"),
    )
# --- end Sentry init ---

# --- API Key Authentication ---
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(key: Optional[str] = Security(api_key_header)) -> str:
    expected = os.getenv("API_SECRET_KEY")
    if not expected:
        logger.warning(
            "API_SECRET_KEY not set — requests are blocked. Set it in .env for production."
        )
        raise HTTPException(
            status_code=403, detail="API Key Verification is not configured"
        )
    if not key or not secrets.compare_digest(key, expected):
        raise HTTPException(status_code=403, detail="Invalid or missing API key")
    return key


# Defence-in-depth: destructive endpoints require a second secret that must
# never be exposed to the browser (no NEXT_PUBLIC_* equivalent). Even if the
# API key leaks, an attacker cannot wipe data without ADMIN_TOKEN.
admin_token_header = APIKeyHeader(name="X-Admin-Token", auto_error=False)


async def verify_admin_token(
    token: Optional[str] = Security(admin_token_header),
) -> str:
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


class PipelineFilters(BaseModel):
    """Typed allowlist for `PipelineRequest.filters`.

    Replaces the previous `Optional[dict]` escape hatch — every other
    inbound model on this app pins `extra='forbid'` + bounded `constr`,
    and `filters` was the only field that let an authed caller smuggle
    arbitrary keys + nested objects past validation.

    The four keys here are the union of what the two actual producers
    of `orchestration_jobs.filters` JSONB emit:

      - `agentic_router._execute_massive_pipeline` -> ``{"type": <str>}``
      - `task_orchestrator.run_discovery_job`     -> ``{"query": <str>, "location": <str>}``

    `limit` is forward-compatibility only — `run_massive_pipeline`
    does not consume it today (no orchestrator-side wiring), so a value
    here is stored in JSONB and otherwise ignored. Bounded `ge=1,
    le=1000` for when it lands.

    NOTE: this is a *superset* of the two shapes that
    `src/scripts/check_jsonb_shapes.py` accepts (`{"type"}` OR
    `{"query","location"}`). The boundary is deliberately looser so
    forward-compat keys (`limit`, `query`-without-`location`) don't
    422 here at the HTTP edge; the daily JSONB shape audit remains
    the authority for what hits the column.
    """

    model_config = ConfigDict(extra="forbid")
    type: Optional[safe_constr(max_length=64)] = None
    query: Optional[safe_constr(max_length=200)] = None
    location: Optional[safe_constr(max_length=200)] = None
    limit: Optional[int] = Field(default=None, ge=1, le=1000)


class CampaignCreate(BaseModel):
    """Body for `POST /campaigns` — create a new outreach campaign.

    `channel` must be one of `CampaignChannel` (`email` / `linkedin`
    / `multi`); `segment_filter` is optional and bounds the lead
    audience by `segment` value when the campaign generates messages.
    `extra='forbid'` rejects any unknown field with a 422.
    """

    model_config = ConfigDict(extra="forbid")
    name: safe_constr(min_length=1, max_length=200)
    channel: CampaignChannel
    segment_filter: Optional[safe_constr(max_length=200)] = None


class CampaignUpdate(BaseModel):
    """Body for partial campaign updates. Only `name` and `status` are
    mutable post-creation; channel + segment_filter are pinned at
    create time to keep the audience stable across runs."""

    model_config = ConfigDict(extra="forbid")
    name: Optional[safe_constr(min_length=1, max_length=200)] = None
    status: Optional[CampaignStatus] = None


class LeadProcessRequest(BaseModel):
    """Body for per-lead processing endpoints (`/process-lead`,
    `/draft-outreach`, `/draft-linkedin`). The `unique_key` is the
    lead's primary identifier — opaque to the API consumer, derived
    by the discovery / hunt path."""

    model_config = ConfigDict(extra="forbid")
    unique_key: safe_constr(min_length=1, max_length=128)


class AskInstruction(BaseModel):
    """Inner payload for `/ask`. The 4000-char cap on `text` bounds
    per-request Gemini billing AND prevents raw prompt-injection blobs
    from being forwarded into the model context."""

    model_config = ConfigDict(extra="forbid")
    text: safe_constr(min_length=1, max_length=4000)


class AskRequest(BaseModel):
    """Outer body for `/ask` — wraps `AskInstruction` so the request
    shape mirrors the agent's `{instruction: {text: "..."}}`
    invocation contract."""

    model_config = ConfigDict(extra="forbid")
    instruction: AskInstruction


class DiscoveryRequest(BaseModel):
    """Body for `/start-discovery` — Google Maps lead discovery.
    `location` defaults to empty (means "no city filter"); `query` is
    the search term (e.g. "dentists" or "law firms")."""

    model_config = ConfigDict(extra="forbid")
    query: safe_constr(min_length=1, max_length=500)
    location: Optional[safe_constr(max_length=200)] = ""


class PipelineRequest(BaseModel):
    """Body for `/start-pipeline` — the multi-stage audit/enrich/score
    pipeline. Either pass `filters` (a typed `PipelineFilters` submodel
    with `extra='forbid'`) to select leads by attribute, or pass
    `lead_ids` for an explicit list. `tasks` selects which stages to
    run; defaults to the full pipeline when absent.

    Prior to 2026-05-23 `filters` was `Optional[dict]` — the only field
    in any inbound model without `extra='forbid'` + bounded `constr`.
    See `PipelineFilters` above for the typed allowlist."""

    model_config = ConfigDict(extra="forbid")
    filters: Optional[PipelineFilters] = None
    lead_ids: Optional[
        conlist(safe_constr(min_length=1, max_length=128), max_length=10_000)
    ] = None
    tasks: Optional[
        conlist(safe_constr(min_length=1, max_length=64), max_length=64)
    ] = None


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
    """Allowlisted parameter shape for `/execute`.

    Every key here is read by at least one `AgenticRouter.execute_task`
    handler. Untyped `params: dict` was removed deliberately so authed
    callers cannot bypass the natural-language → tool gating with a
    hand-crafted plan; the bounded `constr` per key enforces the cap
    server-side. `query_text` is the operator's natural-language
    sub-question — fenced as UNTRUSTED_DATA before reaching Gemini.
    `filters` is a free-form bucket label (e.g. "high-risk"; anything
    else is treated as "default").
    """

    model_config = ConfigDict(extra="forbid")
    unique_key: Optional[safe_constr(min_length=1, max_length=128)] = None
    query: Optional[safe_constr(min_length=1, max_length=500)] = None
    location: Optional[safe_constr(max_length=200)] = None
    query_text: Optional[safe_constr(max_length=4000)] = None
    filters: Optional[safe_constr(max_length=64)] = None
    type: Optional[safe_constr(max_length=64)] = None


class ExecutePlanRequest(BaseModel):
    """Body for `/execute` — the AI router's "execute the proposed plan"
    surface. `task` is locked to the `ExecutableTask` Literal allowlist
    (the AgenticRouter handler vocabulary); `params` is `ExecutePlanParams`."""

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
    # Resolve `db` via the module object so PEP 562 `__getattr__` fires and
    # lazy-initialises the singleton if it hasn't been primed yet. Bare-name
    # `db.client` here would `NameError` because LOAD_GLOBAL doesn't consult
    # module `__getattr__` — and that turns the explicit-skip branch below
    # into a crash that bricks the whole lifespan (fail-closed catch wraps
    # NameError as "single-tenancy check could not run"). Locked in:
    # tested in prod 2026-05-24 (Render restore session).
    import sys as _sys

    _db = getattr(_sys.modules[__name__], "db", None)
    if _db is None or not _db.client:
        logger.warning(
            "OPERATOR_EMAIL set but Supabase client missing — skipping tenancy check."
        )
        return
    try:
        users_resp = _db.client.auth.admin.list_users()
        # supabase-py returns a list directly; tolerate paginated objects too.
        users = (
            users_resp
            if isinstance(users_resp, list)
            else getattr(users_resp, "users", []) or []
        )
        emails = [(getattr(u, "email", None) or "").strip().lower() for u in users]
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
        # OPERATOR_EMAIL is explicitly opt-in. "Could not run" must not pass
        # for "passed" — an Auth API hiccup, permission error, or network
        # blip cannot silently authorise boot when the operator has asked
        # the invariant to be enforced. Fail closed.
        raise RuntimeError(
            f"Single-tenancy check could not run (OPERATOR_EMAIL set): {e}. "
            "Resolve the Auth API failure or unset OPERATOR_EMAIL to skip."
        ) from e


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger.info("Lead Data Scraper Backend Starting...")
    # asyncio debug + slow_callback_duration was considered for catching
    # blocked-loop callbacks at the kernel level. Rejected because it
    # requires loop.set_debug(True), which has measurable runtime
    # overhead in production. Use the per-handler timing middleware
    # below (`_block_logger_middleware`) — same signal at the HTTP layer
    # without the loop-wide debug penalty. Enable kernel-level debug
    # only in dev via `PYTHONASYNCIODEBUG=1`.
    # Prime the lazy `db` / `router` / `auditor` / `orchestrator` singletons
    # FIRST — must run before any function that may LOAD_GLOBAL one of these
    # names (e.g. `_assert_single_tenant_if_enforced` reaches for `db`).
    # Bare-name references use LOAD_GLOBAL bytecode, which does NOT consult
    # module-level `__getattr__` (PEP 562) — so without this priming step,
    # every route handler's bare `db.client` / `router.execute_task` /
    # etc. reference raises `NameError`, the global exception handler
    # turns that into HTTP 500, and the dashboard's eager fetch-on-mount
    # calls (leads / insights / orchestrator/active) all 5xx. Once these
    # names land in globals here, subsequent handler references resolve
    # via the normal globals lookup at zero cost. Each prime is wrapped
    # so a partially-configured env (e.g. missing GEMINI_API_KEY) only
    # disables the affected feature instead of bricking the whole API.
    import sys as _sys

    _self = _sys.modules[__name__]
    for _lazy_name in ("db", "router", "auditor", "orchestrator"):
        try:
            getattr(_self, _lazy_name)
        except Exception as exc:
            logger.warning("Lazy global %s could not initialize: %s", _lazy_name, exc)
    _assert_single_tenant_if_enforced()
    try:
        missing = _self.db.check_schema()
        if missing:
            logger.warning("DATABASE SCHEMA MISMATCH: Missing columns: %s", missing)
            logger.warning("Attempting automatic migration...")
            migrated = _self.db.auto_migrate(missing)
            if migrated:
                logger.info("Migration successful - columns added.")
            else:
                logger.warning(
                    "Auto-migration failed. Run this SQL manually in Supabase SQL Editor:"
                )
                logger.warning(
                    "   ALTER TABLE leads %s;",
                    ", ".join(
                        [f"ADD COLUMN IF NOT EXISTS {col} TEXT" for col in missing]
                    ),
                )
        await _self.orchestrator.recover_interrupted_jobs()
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
# `db`, `router`, `auditor`, `orchestrator` resolved lazily via module
# __getattr__ above. Don't re-introduce eager construction here — it
# silently re-enables the cold-start import storm.


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
    # `RecursionError` fires when an attacker posts a JSON body with
    # deeper nesting than Python's recursion limit (~1000). That's a
    # parser-level DoS, not a server fault — surface as 413 so the
    # operator can distinguish it in logs from a genuine handler crash.
    # tests/test_json_pollution.py::TestDeeplyNestedJSON locks this in.
    if isinstance(exc, RecursionError):
        logger.warning(
            "Recursion limit hit on %s %s — likely deep-JSON payload",
            request.method,
            request.url.path,
        )
        return JSONResponse(
            content={"error": "Payload nesting too deep"},
            status_code=413,
        )
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(content={"error": "Internal server error"}, status_code=500)


@app.exception_handler(BudgetExceededError)
async def _budget_exceeded_handler(request: Request, exc: BudgetExceededError):
    """Map daily-Gemini-budget breaches to HTTP 503.

    503 (service unavailable, retry later) is the right code: the
    breaker resets at UTC midnight, so the call IS expected to
    succeed eventually.  ``used_today`` + ``ceiling`` are logged for
    operator triage but never surfaced in the response body — the
    explicit ``/admin/gemini-budget`` endpoint is the gated observability
    surface for those numbers.
    """
    logger.warning(
        "Gemini daily budget exceeded on %s %s — used=%s ceiling=%s",
        request.method,
        request.url.path,
        exc.used_today,
        exc.ceiling,
    )
    return JSONResponse(
        content={"error": "AI daily budget exhausted"},
        status_code=503,
    )


@app.exception_handler(AIQuotaExceededError)
async def _ai_quota_exceeded_handler(request: Request, exc: AIQuotaExceededError):
    """Map upstream Gemini 429 to HTTP 503 with a structured friendly body.

    Distinct from `_budget_exceeded_handler` (our own daily SQLite cap).
    This fires when the upstream `google-genai` SDK raised `ClientError`
    with `code=429` — operator did not exceed our cap; Google did.
    `retry_after: "tomorrow"` matches Gemini's daily quota window;
    the breaker resets at UTC midnight Pacific.
    """
    logger.warning(
        "Gemini upstream 429 on %s %s — surfacing ai_quota_exceeded",
        request.method,
        request.url.path,
    )
    return JSONResponse(
        content={"error": "ai_quota_exceeded", "retry_after": "tomorrow"},
        status_code=503,
    )


@app.exception_handler(RequestValidationError)
async def _validation_with_authz_check(request: Request, exc: RequestValidationError):
    """FastAPI's default 422 response embeds the Pydantic `detail[]`
    array (`type`, `loc`, `msg`, `input`, `ctx`) — leaking the endpoint's
    expected body shape to anyone who can hit the route, even without a
    valid `X-API-Key`. An unauthenticated attacker could iterate
    `/process-lead`, `/execute`, `/orchestrator/start`, etc. with bogus
    JSON to map the schema. Gate the 422 behind the API-key check: if
    the caller is unauthenticated, return the same generic 403 that the
    `verify_api_key` dependency would have returned. Authenticated
    callers still get the full Pydantic detail array so the frontend's
    `detail[].msg` join keeps working (`AIChat.handleSubmit` relies on
    it for the "String should have at most 4000 characters" surface)."""
    expected = os.getenv("API_SECRET_KEY") or ""
    provided = request.headers.get("x-api-key") or ""
    if not expected or not provided or not secrets.compare_digest(provided, expected):
        return JSONResponse({"detail": "Invalid or missing API key"}, status_code=403)
    # `exc.errors()` embeds the offending value under `input`. Two
    # failure modes the test suite catches:
    #   (a) `NaN` / `Infinity` floats — stdlib `json.loads` accepts them
    #       but `json.dumps` rejects, crashing the 422 handler → 500.
    #   (b) Deep nested payloads — a recursive walk to scrub bad floats
    #       blows Python's recursion limit on the same input that
    #       triggered the validation error.
    # Stringify `input` (with length cap) instead of walking it.
    # `default=str` covers NaN/Inf; `allow_nan=False` keeps strict JSON.
    safe_errors = []
    for err in exc.errors():
        out = dict(err)
        if "input" in out:
            try:
                rendered = json.dumps(
                    out["input"],
                    default=str,
                    allow_nan=False,
                )
            except (ValueError, TypeError, RecursionError):
                rendered = "<unserializable>"
            # Bound the echo so a 1000-deep payload doesn't roundtrip
            # back to the client in the error response.
            out["input"] = rendered[:512]
        safe_errors.append(out)
    return JSONResponse({"detail": safe_errors}, status_code=422)


# Configure CORS — explicit origins only. Wildcards are rejected.
allowed_origins_env = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000")
allowed_origins = [
    origin.strip() for origin in allowed_origins_env.split(",") if origin.strip()
]
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
assert "*" not in allowed_origins, (
    "CORS misconfiguration: '*' is incompatible with allow_credentials=True"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key", "X-Admin-Token"],
)


# Per-request context middleware: generates (or honours) X-Request-ID
# for every request, binds it (plus user + route) to the logging
# contextvars so every log line within the handler carries the same
# ID, and propagates the ID on the response for downstream correlation.
#
# Inbound `X-Request-ID` is HONOURED when alphanumeric + `-` / `_`,
# 1-64 chars. Anything else (missing, oversized, with control chars)
# is replaced with a fresh `uuid.uuid4().hex` — 32 hex chars, no
# dashes for shorter grep lines.
#
# `X-Operator-Email` is read if the proxy ever populates it. The Next.js
# proxy validates the Supabase Auth session and could forward the
# operator email; today it doesn't, so `user_id` in the log envelope
# stays null. Wiring the forward is a future patch — when it lands,
# this middleware picks it up automatically.
#
# Sentry tag: when Sentry is initialized (`_SENTRY_DSN` truthy),
# `request_id` and `user.email` are pinned on the per-request Sentry
# scope. Events captured during the request are filterable in Sentry's
# UI by `tag:request_id:<rid>`.
#
# Declared BEFORE `_block_logger_middleware` so it runs FIRST on inbound
# (Starlette's middleware stack: first-registered = outermost). The
# block logger's slow-handler log line then includes request_id +
# duration_ms as structured fields.
#
# ContextVar lifetime note: we DO NOT call clear_request_context() in a
# finally here. Each request runs in its own asyncio Task (uvicorn spawns
# one per HTTP connection), and ContextVar bindings are scoped to the
# task's Context — they're GC'd cleanly when the task ends. Clearing
# eagerly would break StreamingResponse: `call_next` returns when the
# response *object* is built; the body iterator runs *later* in the same
# task, so any log line emitted inside `_stream_leads_csv` would lose
# request_id if we'd already reset. The bind_request_context /
# clear_request_context pair remains exported for background tasks where
# the caller controls lifetime (e.g. orchestrator chunks rotating
# through synthetic job_id-derived IDs).
@app.middleware("http")
async def _request_context_middleware(request: Request, call_next):
    incoming = request.headers.get("x-request-id", "")
    if 1 <= len(incoming) <= 64 and all(c.isalnum() or c in "-_" for c in incoming):
        rid = incoming
    else:
        rid = uuid.uuid4().hex  # 32 hex chars; no dashes for tighter grep
    operator_email = request.headers.get("x-operator-email") or None
    route_path = request.url.path

    bind_request_context(rid, operator_email, route_path)
    # Also stash on request.state so middlewares that run in a child task
    # (Starlette's BaseHTTPMiddleware spawns the inner chain via anyio
    # task_group; ContextVars set in the outer scope only propagate to
    # the child at spawn time — log lines emitted in the inner finally
    # were observed losing request_id / route). request.state is bound
    # to the request object, not the task, and survives the hop.
    request.state.request_id = rid
    request.state.route = route_path
    request.state.operator_email = operator_email
    if _SENTRY_DSN:
        # `sentry_sdk` is imported at module load inside the
        # `if _SENTRY_DSN:` init block, so the name is bound here.
        # FastApiIntegration creates a per-request scope; set_tag /
        # set_user attach to it for any event captured this request.
        sentry_sdk.set_tag("request_id", rid)
        if operator_email:
            sentry_sdk.set_user({"email": operator_email})
    response = await call_next(request)
    response.headers["x-request-id"] = rid
    return response


# Block-detector middleware: times every request and logs WARN when a
# single handler holds the loop for > SLOW_HANDLER_THRESHOLD_MS. Catches
# sync calls sneaking into async paths — same class of bug Locust 4.1
# surfaced (sync supabase-py underneath an async def stalls the loop).
# Threshold defaults to 100 ms; override per-deployment via env var.
#
# `time.perf_counter()` is monotonic and not subject to NTP correction;
# safe for short-interval wall-clock measurement. The middleware runs
# inside the same task as the handler — total includes the handler's
# `await` time, NOT just the synchronous CPU it spent. That's the right
# signal for "did this request block the loop": if a coroutine spends
# 500 ms awaiting Gemini, that doesn't block the loop (other tasks
# interleave) and the log is informational; if it spends 500 ms on a
# sync .execute(), the loop is wedged and the log is the bug report.
#
# Storage: log only. A future iteration could collect into an in-process
# top-K (e.g. heapq of slowest path:duration) so /metrics can emit a
# rolling summary; out of scope here.
import time as _time

SLOW_HANDLER_THRESHOLD_MS = float(os.getenv("SLOW_HANDLER_THRESHOLD_MS", "100"))


@app.middleware("http")
async def _block_logger_middleware(request: Request, call_next):
    start = _time.perf_counter()
    try:
        response = await call_next(request)
        return response
    finally:
        elapsed_ms = (_time.perf_counter() - start) * 1000.0
        if elapsed_ms >= SLOW_HANDLER_THRESHOLD_MS:
            # Structured extras so JsonFormatter writes duration_ms +
            # method + path as top-level envelope fields — greppable
            # and queryable in Logtail/Loki. Path only — query string
            # may contain user input we don't want in logs (filter
            # values, cursor tokens, etc.). request_id + route are
            # read off `request.state` (set by
            # `_request_context_middleware`) rather than via ContextVar
            # — BaseHTTPMiddleware spawns this middleware in a child
            # task, so ContextVars only carry values that were bound
            # at spawn time and are then read-only from this scope's
            # perspective. `request.state` is request-scoped and not
            # subject to that race.
            logger.warning(
                "slow handler",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round(elapsed_ms, 2),
                    "threshold_ms": SLOW_HANDLER_THRESHOLD_MS,
                    "request_id": getattr(request.state, "request_id", None),
                    "route": getattr(request.state, "route", None),
                    "user_id": getattr(request.state, "operator_email", None),
                },
            )


# Browser security headers — defense in depth for the case where FastAPI
# is reached directly (bypassing the Next.js proxy that already stamps
# these on the HTML routes). CSP intentionally omitted: backend never
# serves HTML. HSTS intentionally omitted: the Render edge already adds
# it on the frontend hostname, and stamping it on a JSON-only API host
# pollutes the preload list with a host that has no HTML route to serve.
@app.middleware("http")
async def _security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response


@app.get("/")
async def root():
    """Unauthenticated liveness probe. Intentionally returns no product /
    version metadata — anything richer is a free fingerprint for attackers."""
    return {"status": "ok"}


# --- Sentry verification endpoint ---
# Hidden behind SENTRY_TEST_ENABLED=1; returns 404 otherwise so the path
# isn't a useful DoS surface in normal operation. Operator workflow:
#   1. Set SENTRY_TEST_ENABLED=1 in Render env, redeploy (or set locally
#      and restart uvicorn).
#   2. Curl `POST /_sentry/test` with X-API-Key.
#   3. Confirm the error lands in Sentry within ~60s.
#   4. Unset SENTRY_TEST_ENABLED and redeploy.
@app.post("/_sentry/test", dependencies=[Depends(verify_api_key)])
@limiter.limit("5/minute")
async def sentry_test(request: Request):
    if os.getenv("SENTRY_TEST_ENABLED", "").lower() not in ("1", "true", "yes"):
        raise HTTPException(status_code=404, detail="Not Found")
    raise RuntimeError(
        "Sentry verification test triggered — if you see this in Sentry, "
        "the integration works."
    )


# Real User Monitoring (web-vitals) ingestion. The browser ships per-page
# CLS / INP / LCP / TTFB via navigator.sendBeacon to /api/proxy/metrics →
# (proxy) → this endpoint. Logged as structured WARN/INFO lines so a
# downstream log aggregator can compute p50/p75/p95 without us building
# a TSDB. No DB write — keeps the request path cheap and avoids polluting
# the leads schema. Rate-limited heavily because beacons are public-ish
# (the only auth gate is the in-app session, but the proxy validates that).
class WebVitalsMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # web-vitals v3 reports name in {CLS, INP, LCP, FID, FCP, TTFB}.
    name: Literal["CLS", "INP", "LCP", "FID", "FCP", "TTFB"]
    # Value is unitless in CLS, ms in others. Bound generously to reject
    # clearly-garbage submissions while still catching 30s LCP outliers.
    value: float = Field(ge=0.0, le=600_000.0)
    # Rating is web-vitals' own qualitative bucket.
    rating: Literal["good", "needs-improvement", "poor"]
    # Page route the measurement was taken on. Bounded so a hostile beacon
    # can't dump 1 MB into the log. Stripped of query/hash on the client.
    path: safe_constr(min_length=1, max_length=200)
    # Client-generated unique id per page-load — useful for stitching
    # multiple beacons from the same nav into one trace. 64 chars max.
    id: safe_constr(min_length=1, max_length=64)


@app.post("/metrics", dependencies=[Depends(verify_api_key)])
@limiter.limit("60/minute")
async def submit_web_vitals(request: Request, metric: WebVitalsMetric):
    """Web-vitals beacon sink. Logs structured per-metric lines.

    Logged at INFO for "good" ratings, WARN for "needs-improvement" /
    "poor" so a grep over the log file (or any log aggregator) surfaces
    real user regressions without per-metric thresholds in code. Hourly
    p50/p75/p95 lives in the log aggregator, not here.
    """
    level = logger.info if metric.rating == "good" else logger.warning
    level(
        "web-vital %s=%.1f rating=%s path=%s id=%s",
        metric.name,
        metric.value,
        metric.rating,
        metric.path,
        metric.id,
    )
    return {"ok": True}


# ---------------------------------------------------------------------------
# RFC 8058 List-Unsubscribe-Post handler (Phase 14.2 PR β)
# ---------------------------------------------------------------------------
# Gmail (2024-02) + Yahoo (2024-02) + Microsoft (2025-04) require:
#   List-Unsubscribe: <https://lds.../unsubscribe/{token}>, <mailto:...>
#   List-Unsubscribe-Post: List-Unsubscribe=One-Click
#
# The dispatcher (Phase 14.1) sets the header per-message; this route
# is the receiver. Token format + HMAC verify: see
# src/utils/unsubscribe_tokens.py.
#
# Public (NO X-API-Key dependency) — the recipient is not an LDS user.
# Slowapi-throttled to 10/minute/IP to bound token-enumeration cost.
# All failure paths return 410 Gone with a generic body — never leaks
# which verification stage failed (signature vs expiry vs unknown token).
#
# Two HTTP methods:
#   * POST /unsubscribe/{token}  — RFC 8058 one-click. No body required.
#                                   Verifies, suppresses, returns 200.
#   * GET  /unsubscribe/{token}  — Human-clicked fallback. Renders a
#                                   minimal HTML confirmation page that
#                                   POSTs the same token. Stays valid for
#                                   the same 90-day TTL.
# ---------------------------------------------------------------------------

# Shared style block for the three unsubscribe pages. Inline `<style>` is
# explicitly allowed by `_UNSUB_HTML_HEADERS` (`style-src 'unsafe-inline'`);
# no external resources, no JS — matches the tight `default-src 'none'` floor.
# Recipients land here from a transactional email link, often on phones;
# fontSize ≥ 16 px (root) avoids iOS Safari auto-zoom-on-focus, button
# min-height 48 px ≥ WCAG 2.5.5 + Apple HIG 44 px floor, viewport meta turns
# the page from "1995 raw HTML" into a responsive shell. Honours
# `prefers-color-scheme: dark` and `prefers-reduced-motion`.
_UNSUB_BASE_STYLE = (
    "*,*::before,*::after{box-sizing:border-box}"
    "html,body{margin:0;padding:0}"
    "body{min-height:100dvh;display:grid;place-items:center;"
    "padding:clamp(1rem,4vw,2rem);"
    "background:#f5f4ef;color:#1a1a22;"
    "font:16px/1.55 -apple-system,BlinkMacSystemFont,\"Segoe UI\","
    "Roboto,Helvetica,Arial,sans-serif}"
    "main{width:min(440px,100%);background:#fff;border:1px solid #e6e4dc;"
    "border-left:4px solid hsl(234,89%,64%);border-radius:14px;"
    "padding:clamp(1.5rem,4vw,2.5rem);"
    "box-shadow:0 1px 2px rgba(20,20,30,.04),0 8px 24px -16px rgba(20,20,30,.12)}"
    "h1{margin:0;letter-spacing:-.01em;"
    "font:600 clamp(1.5rem,4vw,1.875rem)/1.15 Georgia,Cambria,"
    "\"Liberation Serif\",serif}"
    "p{margin:.875rem 0 0;color:#52525e;font-size:.9375rem;line-height:1.6}"
    "form{margin-top:1.5rem}"
    "button{width:100%;min-height:48px;padding:.75rem 1.25rem;"
    "font:600 16px/1.2 inherit;color:#fff;"
    "background:hsl(234,89%,64%);border:0;border-radius:10px;"
    "cursor:pointer;-webkit-tap-highlight-color:transparent;"
    "transition:background-color .15s ease}"
    "button:hover{background:hsl(234,89%,58%)}"
    "button:focus-visible{outline:2px solid hsl(234,89%,64%);outline-offset:3px}"
    "@media (prefers-color-scheme:dark){"
    "body{background:#0e0e16;color:#e8e8ee}"
    "main{background:#16161f;border-color:#23232d;"
    "box-shadow:0 1px 2px rgba(0,0,0,.4),0 12px 32px -16px rgba(0,0,0,.6)}"
    "p{color:#a8a8b3}}"
    "@media (prefers-reduced-motion:reduce){"
    "*,*::before,*::after{transition:none!important;animation:none!important}}"
)


def _unsub_page(title: str, h1: str, body_html: str) -> str:
    return (
        '<!doctype html><html lang="en"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{title}</title>"
        f"<style>{_UNSUB_BASE_STYLE}</style>"
        "</head>"
        f"<body><main><h1>{h1}</h1>{body_html}</main></body></html>"
    )


# Generic body for every failure — operator can grep logs for the specific
# stage that failed without leaking it to attackers via response text.
_UNSUB_FAILURE_HTML = _unsub_page(
    "Unsubscribe",
    "Link expired",
    "<p>This unsubscribe link is no longer valid. If you continue to receive "
    "messages, reply STOP to the next one or contact the sender directly.</p>",
)

# Confirmation page rendered on GET. POSTs back to the same URL.
_UNSUB_CONFIRM_HTML = _unsub_page(
    "Unsubscribe",
    "Confirm unsubscribe",
    "<p>Click the button below to stop receiving messages from this sender. "
    "You can close this tab afterwards.</p>"
    '<form method="post" action="">'
    '<button type="submit">Unsubscribe me</button>'
    "</form>",
)

_UNSUB_SUCCESS_HTML = _unsub_page(
    "Unsubscribed",
    "You have been unsubscribed",
    "<p>You will not receive further messages from this sender.</p>",
)

# Tight CSP for the only HTML route the backend serves. XFO=DENY (stamped
# by _security_headers_middleware) handles clickjack; this layer defends
# against future drift (e.g. if someone later adds inline JS / external
# resources). form-action 'self' keeps the POST same-origin even if the
# action attribute is ever rewritten.
_UNSUB_HTML_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; form-action 'self'; "
        "style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"
    ),
}


async def _suppress_from_unsubscribe_token(token: str) -> bool:
    """Verify ``token`` and insert the matching suppression row.

    Returns True on a clean unsubscribe, False on any verification or
    dereference failure. Never raises — every error path collapses to
    False so the handler can serve a uniform 410.

    Idempotency: re-POSTing the same token after success returns True
    (the row already exists; PostgREST 23505 → SuppressionRepository.add
    returns None which we treat as already-suppressed).
    """
    from src.utils.unsubscribe_tokens import (
        BadSignature,
        ExpiredToken,
        InvalidToken,
        verify,
    )

    try:
        payload = verify(token)
    except (InvalidToken, BadSignature, ExpiredToken) as exc:
        logger.info(
            "unsubscribe rejected: %s",
            type(exc).__name__,
            extra={"reason": type(exc).__name__},
        )
        return False
    except RuntimeError:
        # UNSUBSCRIBE_TOKEN_SECRET missing — operator misconfig. Don't
        # leak the cause; log loud so it shows up in Sentry.
        logger.exception("unsubscribe handler misconfigured (no signing secret)")
        return False

    if not db.client:
        logger.warning("unsubscribe: DB unavailable, deferring write")
        return False

    # Dereference tracking_id → campaign_messages → lead.email.
    try:
        msg_rows = await asyncio.to_thread(
            lambda: (
                db.client.table("campaign_messages")
                .select("campaign_id, lead_unique_key")
                .eq("tracking_id", payload.tracking_id)
                .limit(1)
                .execute()
            )
        )
    except Exception:
        logger.exception("unsubscribe: tracking_id lookup failed")
        return False

    rows = getattr(msg_rows, "data", None) or []
    if not rows:
        # Token references a non-existent message — likely tampering or
        # the operator data-wiped this campaign. Treat as success from the
        # recipient's perspective (they'll see "you have been
        # unsubscribed") but log the anomaly.
        logger.info("unsubscribe: tracking_id %s not found", payload.tracking_id)
        return True

    campaign_id = rows[0].get("campaign_id")
    lead_unique_key = rows[0].get("lead_unique_key")

    lead_email: Optional[str] = None
    if lead_unique_key:
        try:
            lead_rows = await asyncio.to_thread(
                lambda: (
                    db.client.table("leads")
                    .select("email")
                    .eq("unique_key", lead_unique_key)
                    .limit(1)
                    .execute()
                )
            )
            lead_data = getattr(lead_rows, "data", None) or []
            if lead_data:
                lead_email = lead_data[0].get("email")
        except Exception:
            logger.exception(
                "unsubscribe: lead lookup failed for %s",
                lead_unique_key,
            )

    if not lead_email:
        # Lead deleted (FK ON DELETE SET NULL) or email blanked. We've
        # honoured the unsubscribe at the campaign level by virtue of the
        # tracking_id reference; nothing more we can do.
        logger.info(
            "unsubscribe: no email recoverable for tracking_id %s",
            payload.tracking_id,
        )
        return True

    # Channel='all' — recipient said "stop everything", not just email.
    # Webhook handler in PR γ uses channel='email' for bounce events.
    from src.repositories.suppression_repo import SuppressionRepository

    repo = SuppressionRepository(db.client)
    try:
        await repo.add(
            "email",
            lead_email,
            "unsubscribe",
            channel="all",
            source_campaign_id=campaign_id,
            notes=f"RFC 8058 List-Unsubscribe-Post from tracking_id={payload.tracking_id}",
        )
    except Exception:
        logger.exception("unsubscribe: suppression insert failed")
        return False

    logger.info(
        "unsubscribe accepted",
        extra={
            "campaign_id": campaign_id,
            "lead_email_hash": _redact_email(lead_email),
        },
    )
    return True


def _redact_email(email: str) -> str:
    """Log redaction — keep the domain, hash the local part."""
    if "@" not in email:
        return "***"
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}"


@app.get("/unsubscribe/{token}", response_class=HTMLResponse)
@limiter.limit("10/minute")
async def unsubscribe_confirm(request: Request, token: str):
    """Render a minimal HTML form that POSTs the same token.

    Token presence isn't verified at GET time — the page is rendered for
    any string-shaped token. The POST does the real work + logging. This
    keeps the GET cache-friendly + cheap for crawlers.
    """
    # Length-bound the token before doing anything else.
    if not token or len(token) > 200:
        return HTMLResponse(
            content=_UNSUB_FAILURE_HTML,
            status_code=410,
            headers=_UNSUB_HTML_HEADERS,
        )
    return HTMLResponse(
        content=_UNSUB_CONFIRM_HTML,
        status_code=200,
        headers=_UNSUB_HTML_HEADERS,
    )


@app.post("/unsubscribe/{token}", response_class=HTMLResponse)
@limiter.limit("10/minute")
async def unsubscribe_submit(request: Request, token: str):
    """RFC 8058 one-click unsubscribe handler.

    Verifies the token, dereferences tracking_id → lead.email, inserts a
    suppression row. Always returns HTML (success or generic failure);
    mail providers parse 200 OK as "unsubscribed" regardless of body.
    """
    if not token or len(token) > 200:
        return HTMLResponse(
            content=_UNSUB_FAILURE_HTML,
            status_code=410,
            headers=_UNSUB_HTML_HEADERS,
        )
    ok = await _suppress_from_unsubscribe_token(token)
    if not ok:
        return HTMLResponse(
            content=_UNSUB_FAILURE_HTML,
            status_code=410,
            headers=_UNSUB_HTML_HEADERS,
        )
    return HTMLResponse(
        content=_UNSUB_SUCCESS_HTML,
        status_code=200,
        headers=_UNSUB_HTML_HEADERS,
    )


# ---------------------------------------------------------------------------
# Instantly webhook handler (Phase 14.2 PR γ)
# ---------------------------------------------------------------------------
# Inbound surface for Instantly's delivery events: email_sent /
# email_bounced / email_unsubscribed / email_replied. Each event triggers
# a state-transition on campaign_messages (status) and, for the
# bounce/unsubscribe paths, a suppression INSERT so the dispatcher
# precheck (PR α) can skip the address on subsequent sends.
#
# Idempotency:
#   * HMAC verify gates every request — body bytes + signature must
#     match. Replay attacker without the secret gets BadSignature.
#   * Timestamp window (±5 min) blocks replay of a leaked signed body.
#   * (provider, event_id) UNIQUE on webhook_events: a duplicate INSERT
#     collides on 23505; the handler returns 200 OK without re-running
#     the side effects.
#
# Performance:
#   * Instantly retries on any timeout >2s — keep the synchronous path
#     to: HMAC verify → timestamp check → INSERT event → kick a
#     BackgroundTask. The state-transition runs after the response is
#     flushed.
#   * If background-task execution fails, processed_at stays NULL and
#     processing_error captures the cause; a follow-up sweeper (PR δ)
#     can retry from idx_webhook_events_unprocessed.
# ---------------------------------------------------------------------------


# Allowlisted event-type vocabulary. Anything else lands in
# webhook_events but does NOT trigger a state transition (logged as
# "unhandled event type"). New types extend this set + the
# _process_instantly_event dispatcher.
_INSTANTLY_HANDLED_EVENTS: frozenset[str] = frozenset(
    {
        "email_sent",
        "email_bounced",
        "email_unsubscribed",
        "email_replied",
    }
)


def _generic_webhook_error(detail: str, status_code: int = 401):
    """Webhook failure response — body is intentionally terse so a
    malicious caller can't reconstruct which check failed."""
    return JSONResponse(
        {"detail": "webhook verification failed"},
        status_code=status_code,
    )


_TRANSPORT_ERROR_TYPES: tuple[type[BaseException], ...] = ()


def _is_transport_error(exc: BaseException) -> bool:
    """True iff ``exc`` is an httpx/httpcore transport-layer error.

    Important: these errors can land AFTER PostgREST has already
    committed the INSERT. Under burst load against Supabase from
    Render's single uvicorn worker, ``httpcore.RemoteProtocolError:
    Server disconnected`` is observed at ~8-23% rate on the webhook
    handler — the row is in webhook_events but the response body
    never reaches supabase-py, which raises. Caller MUST verify
    row existence before deciding 200 vs 500.
    """
    global _TRANSPORT_ERROR_TYPES
    if not _TRANSPORT_ERROR_TYPES:
        try:
            import httpx
        except ImportError:
            return False
        types: list[type[BaseException]] = [
            httpx.RemoteProtocolError,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.PoolTimeout,
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.NetworkError,
        ]
        try:
            import httpcore

            types.append(httpcore.RemoteProtocolError)
            types.append(httpcore.NetworkError)
        except ImportError:
            pass
        _TRANSPORT_ERROR_TYPES = tuple(types)
    return isinstance(exc, _TRANSPORT_ERROR_TYPES)


async def _webhook_event_exists(provider: str, event_id: str) -> bool:
    """Best-effort lookup against ``webhook_events``. Bounded 1.5s.

    Returns True iff a ``(provider, event_id)`` row already exists.
    Used to recover from transport-class errors where the INSERT
    may have committed despite the dropped response. Any error
    (including timeout) returns False — caller falls through to
    the 500 path and Instantly's retry + the sweeper recover.
    """
    if not db.client:
        return False
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                lambda: (
                    db.client.table("webhook_events")
                    .select("id")
                    .eq("provider", provider)
                    .eq("event_id", event_id)
                    .limit(1)
                    .execute()
                )
            ),
            timeout=1.5,
        )
    except Exception:
        return False
    return bool(getattr(result, "data", None))


@app.post("/webhooks/instantly", response_class=JSONResponse)
@limiter.limit("120/minute")
async def webhook_instantly(request: Request, background_tasks: BackgroundTasks):
    """Inbound webhook from Instantly.

    Public endpoint (no X-API-Key — Instantly doesn't carry one). HMAC
    on the raw body is the entire auth surface; ``X-Timestamp``
    bounds replay.

    Returns:
        * 200 + ``{ok: true}`` on a fresh accepted event
        * 200 + ``{ok: true, duplicate: true}`` on a duplicate event_id
          (idempotent — Instantly will stop retrying)
        * 401 + generic body on any verification failure
        * 503 if the DB is unreachable
    """
    from src.utils.webhook_security import (
        BadSignature,
        MissingSignature,
        MissingTimestamp,
        StaleTimestamp,
        verify_hmac_sha256,
        verify_timestamp_window,
    )

    secret = os.environ.get("INSTANTLY_WEBHOOK_SIGNING_SECRET", "")
    if not secret:
        # Operator misconfig — log + reject. Don't 500 (would advertise
        # the problem to the public endpoint); 401 is opaque enough.
        logger.error("INSTANTLY_WEBHOOK_SIGNING_SECRET not set; rejecting")
        return _generic_webhook_error("misconfigured")

    raw_body = await request.body()
    if len(raw_body) > 256 * 1024:
        # Instantly events are small (<10 KB typically); 256 KB is generous.
        return _generic_webhook_error("payload too large", status_code=413)

    signature = request.headers.get("X-Signature", "")
    timestamp_header = request.headers.get("X-Timestamp", "")

    try:
        verify_hmac_sha256(raw_body, signature, secret)
        verify_timestamp_window(timestamp_header)
    except (MissingSignature, BadSignature, MissingTimestamp, StaleTimestamp) as exc:
        logger.info(
            "instantly webhook rejected: %s",
            type(exc).__name__,
            extra={"reason": type(exc).__name__},
        )
        return _generic_webhook_error("rejected")

    # Body verified. Parse JSON.
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.info("instantly webhook bad JSON: %s", exc)
        return _generic_webhook_error("bad JSON")

    if not isinstance(payload, dict):
        return _generic_webhook_error("bad JSON shape")

    event_id = str(payload.get("event_id") or "")[:128]
    event_type = str(payload.get("event_type") or "")[:64]
    if not event_id or not event_type:
        return _generic_webhook_error("missing event_id/event_type")

    if not db.client:
        return JSONResponse({"detail": "database unavailable"}, status_code=503)

    # Idempotent insert via repository. Duplicate (23505) → 200 with
    # flag. Transport-class errors can land AFTER the row commits to
    # Postgres (httpcore RemoteProtocolError on the response read);
    # re-read the table on those before deciding 500.
    try:
        result = await WebhookEventRepository(db.client).insert_event(
            provider=_INSTANTLY_WEBHOOK_PROVIDER,
            event_id=event_id,
            event_type=event_type,
            payload=payload,
        )
    except Exception as exc:
        if _is_transport_error(exc) and await _webhook_event_exists(
            _INSTANTLY_WEBHOOK_PROVIDER,
            event_id,
        ):
            # Row landed despite the dropped response. Treat as a
            # fresh accepted event (idempotency lock guarantees we
            # are the original writer; a concurrent retry would not
            # reach this branch because its INSERT would 23505).
            logger.warning(
                "instantly webhook transport error post-commit; row exists, scheduling bg task",
                extra={
                    "event_id": event_id,
                    "event_type": event_type,
                    "exc_type": type(exc).__name__,
                },
            )
            if event_type in _INSTANTLY_HANDLED_EVENTS:
                background_tasks.add_task(
                    _process_instantly_event,
                    event_id=event_id,
                    payload=payload,
                )
            return JSONResponse({"ok": True, "recovered": True}, status_code=200)
        logger.exception("instantly webhook INSERT failed")
        return JSONResponse({"detail": "internal error"}, status_code=500)

    if result.duplicate:
        # Instantly is replaying — typically because an earlier
        # delivery returned 500 (transport error after row commit;
        # see _is_transport_error). Schedule the background task so
        # the duplicate path is the recovery path: handlers are
        # idempotent (mark_sent gates on .is_("provider_message_id",
        # "null"); suppressions use upsert ignore_duplicates), so
        # re-firing is safe even when the original delivery DID
        # complete cleanly.
        logger.info(
            "instantly webhook duplicate event_id=%s",
            event_id,
            extra={"event_id": event_id, "event_type": event_type},
        )
        if event_type in _INSTANTLY_HANDLED_EVENTS:
            background_tasks.add_task(
                _process_instantly_event,
                event_id=event_id,
                payload=payload,
            )
        return JSONResponse({"ok": True, "duplicate": True}, status_code=200)

    # Process the event off the request path so Instantly's 2s timeout
    # is never the bottleneck. The background task updates
    # campaign_messages.status + inserts a suppression row if needed.
    if event_type in _INSTANTLY_HANDLED_EVENTS:
        background_tasks.add_task(
            _process_instantly_event,
            event_id=event_id,
            payload=payload,
        )
    else:
        logger.info(
            "instantly webhook unhandled event_type=%s",
            event_type,
            extra={"event_type": event_type},
        )

    return JSONResponse({"ok": True}, status_code=200)


async def _process_instantly_event(event_id: str, payload: dict) -> None:
    """Translate an Instantly event into LDS state transitions.

    Runs in a FastAPI BackgroundTask after the request returns. Any
    exception lands in processing_error on the webhook_events row (best
    effort — if the UPDATE itself fails, the next sweeper run will pick
    the event back up via idx_webhook_events_unprocessed).

    Phase 14.3 wired the dispatcher → webhook round-trip:
    ``custom_variables.lds_message_id`` (set by the dispatcher per
    ``InstantlyLeadPayload.from_lds_lead``) is echoed back in every
    event for the same message, letting ``email_sent`` perform a
    targeted, first-hit-wins UPDATE against
    ``campaign_messages.id = lds_message_id``.
    """

    # PEP-562 cron-path guard. When ``scripts/webhook_sweeper.py`` runs
    # this handler the FastAPI lifespan never fires, so the lazy-imported
    # ``db`` / ``router`` / ``auditor`` / ``orchestrator`` singletons in
    # this module's globals() are still unset. Bare-name ``db.client`` /
    # ``router.execute_task`` inside any nested function or lambda would
    # then raise ``NameError: name 'db' is not defined`` (LOAD_GLOBAL on
    # a missing name skips PEP-562 ``__getattr__``). Observed in prod
    # 2026-05-28 — every 2-minute cron tick produced two NameError
    # tracebacks at the webhook_events checkpoint UPDATE site, leaving
    # processed_at NULL and letting the same rows replay forever.
    # One attribute access via sys.modules triggers ``__getattr__``,
    # which populates ``globals()['db']`` etc; subsequent bare-name uses
    # in this handler + the ``_instantly_handle_*`` chain resolve at
    # zero overhead. Cheap (~µs) when already primed.
    import sys as _sys

    _self_mod = _sys.modules[__name__]
    _self_mod.db  # noqa — side-effect: primes globals()["db"] via PEP-562 __getattr__

    # Defense-in-depth: provider id + email round-trip into outbound
    # SMTP-adjacent payloads (In-Reply-To, To:). HMAC gates forgery, so
    # this is belt-and-braces against a future compromised-provider
    # threat model — strip CR/LF/VT/FF before length cap.
    def _scrub(s: str, cap: int) -> str:
        return _STRIP_CTRL_PATTERN.sub("", s)[:cap]

    event_type = _scrub(str(payload.get("event_type") or ""), 64)
    provider_msg_id = _scrub(
        str(
            payload.get("lds_provider_message_id")
            or payload.get("provider_message_id")
            or payload.get("message_id")
            or ""
        ),
        200,
    )
    recipient_email = _scrub(
        str(payload.get("recipient_email") or payload.get("email") or ""),
        320,
    )
    campaign_id_hint = payload.get("lds_campaign_id") or payload.get("campaign_id")

    # `lds_message_id` lives either at the top level OR nested in
    # `custom_variables` — Instantly echoes custom vars under both
    # shapes depending on event type. Read both.
    custom_vars = payload.get("custom_variables") or {}
    if not isinstance(custom_vars, dict):
        custom_vars = {}
    lds_message_id = _scrub(
        str(payload.get("lds_message_id") or custom_vars.get("lds_message_id") or ""),
        64,
    )

    # Instantly's sent_at timestamp on the email_sent event, if any.
    # We pass it through as-is (ISO string) to mark_sent; the repo
    # stamps it into campaign_messages.sent_at on first hit.
    sent_at_iso = (
        str(payload.get("sent_at") or payload.get("timestamp") or "")[:64]
        or datetime.now(timezone.utc).isoformat()
    )

    error_message: Optional[str] = None
    transport_error = False
    try:
        if event_type == "email_sent":
            await _instantly_handle_sent(
                lds_message_id,
                provider_msg_id,
                sent_at_iso,
                recipient_email,
            )
        elif event_type == "email_bounced":
            await _instantly_handle_bounced(
                provider_msg_id,
                recipient_email,
                campaign_id_hint,
                payload,
            )
        elif event_type == "email_unsubscribed":
            await _instantly_handle_unsubscribed(
                provider_msg_id,
                recipient_email,
                campaign_id_hint,
            )
        elif event_type == "email_replied":
            await _instantly_handle_replied(provider_msg_id)
        # Else: unhandled type — already logged at handler entry.
    except Exception as exc:  # noqa: BLE001 — record + skip; sweeper retries
        if _is_transport_error(exc):
            # Issue #368: side-effect (campaign_messages UPDATE,
            # suppression INSERT) may or may not have committed before
            # the response was dropped. Leaving processed_at NULL lets
            # the sweeper re-claim the row; handlers are predicate-
            # idempotent (mark_sent gates on provider_message_id IS
            # NULL; suppression upsert ignore_duplicates), so re-firing
            # is safe even when the original write DID commit.
            transport_error = True
            logger.warning(
                "instantly event %s transport error mid-processing; "
                "leaving processed_at NULL for sweeper retry",
                event_type,
                extra={
                    "event_id": event_id,
                    "event_type": event_type,
                    "exc_type": type(exc).__name__,
                },
            )
        else:
            logger.exception("instantly event %s processing failed", event_type)
            error_message = f"{type(exc).__name__}: {exc!s}"[:1024]

    if transport_error:
        # Skip the checkpoint UPDATE entirely so the sweeper's
        # idx_webhook_events_unprocessed scan picks the row back up.
        # Genuine handler-logic errors still fall through to stamp
        # (processed_at + processing_error) — poison messages should
        # NOT retry indefinitely.
        return

    # Checkpoint processing on the event row. On success: processed_at
    # set + error_message NULL. On non-transport handler error:
    # processed_at set + processing_error populated (poison-pill gate).
    try:
        await asyncio.to_thread(
            lambda: (
                db.client.table("webhook_events")
                .update(
                    {
                        "processed_at": datetime.now(timezone.utc).isoformat(),
                        "processing_error": error_message,
                    }
                )
                .eq("provider", "instantly")
                .eq("event_id", event_id)
                .execute()
            )
        )
    except Exception:
        # Sweeper retries via idx_webhook_events_unprocessed.
        logger.exception("instantly webhook checkpoint UPDATE failed")


async def _instantly_handle_sent(
    lds_message_id: str,
    provider_msg_id: str,
    sent_at_iso: str,
    recipient_email: str,
) -> None:
    """email_sent: stamp provider_message_id + status='sent' + sent_at.

    Phase 14.3 wiring — the dispatcher passes
    ``custom_variables.lds_message_id`` (= campaign_messages.id) on
    push; Instantly echoes it back in every event for the same
    message. We use it here to do a targeted first-hit-wins UPDATE.

    The repo enforces ``.is_("provider_message_id", "null")`` so a
    duplicate ``email_sent`` event (Instantly retries; ~rare 2xx
    redeliveries) is a clean no-op — the predicate matches zero rows
    after the first apply. ``sent_at`` is preserved on subsequent
    replays because the UPDATE doesn't fire at all.

    Out-of-order edge case: if the row's ``lds_message_id`` doesn't
    exist (Phase 15 may decouple row creation from dispatch), the
    UPDATE matches zero rows and we log + return. ``webhook_events``
    has already captured the payload; the next sweeper run picks it
    back up once the row appears.

    Without ``lds_message_id`` (e.g. legacy webhook event from before
    Phase 14.3 wiring), we cannot identify the row and fall back to a
    log-only path (matches Phase 14.2 PR γ semantics).
    """
    if not lds_message_id:
        logger.info(
            "email_sent without lds_message_id (legacy / pre-14.3 event); skipping mark_sent",
            extra={
                "provider_message_id": provider_msg_id or None,
                "recipient_hash": _redact_email(recipient_email)
                if recipient_email
                else None,
            },
        )
        return
    if not provider_msg_id:
        # Webhook fired but no Instantly id to stamp — degenerate event.
        logger.info(
            "email_sent missing provider_message_id; skipping mark_sent",
            extra={"lds_message_id": lds_message_id},
        )
        return
    if not db.client:
        logger.warning("email_sent: DB unavailable, deferring")
        return

    from src.repositories.campaign_message_repo import CampaignMessageRepository

    repo = CampaignMessageRepository(db.client)
    result = await repo.mark_sent(
        lds_message_id,
        provider_msg_id,
        sent_at_iso=sent_at_iso,
    )
    logger.info(
        "email_sent processed",
        extra={
            "lds_message_id": lds_message_id,
            "matched": result.matched,
            "error": result.error,
        },
    )

    # Phase 15.4 — schedule the next step on the sequence. The advancer
    # is idempotent via the (lead_unique_key, sequence_id, step_id)
    # partial UNIQUE index, so a duplicate _sent webhook replay swallows
    # the 23505 collision and returns advanced=False. Schedule-on-advance
    # design (vs gate-on-advance) — see sequence_advancer module docstring.
    msg_row = await _lookup_message_by_id(lds_message_id)
    if msg_row and msg_row.get("sequence_id") and msg_row.get("step_id"):
        from src.repositories.sequence_step_repo import SequenceStepRepository
        from src.services.sequence_advancer import advance_to_next_step

        step_repo = SequenceStepRepository(db.client)
        try:
            parsed_sent = (
                datetime.fromisoformat(sent_at_iso.replace("Z", "+00:00"))
                if sent_at_iso
                else None
            )
        except ValueError:
            parsed_sent = None
        advance_result = await advance_to_next_step(
            current_message=msg_row,
            step_repo=step_repo,
            message_repo=repo,
            event_type="sent",
            sent_at=parsed_sent,
        )
        logger.info(
            "email_sent advance",
            extra={
                "lds_message_id": lds_message_id,
                "advanced": advance_result.advanced,
                "reason": advance_result.reason,
                "next_step_id": advance_result.next_step_id,
                "scheduled_at": advance_result.scheduled_at,
            },
        )


async def _instantly_handle_bounced(
    provider_msg_id: str,
    recipient_email: str,
    campaign_id_hint: Optional[str],
    payload: dict,
) -> None:
    """email_bounced: state-machine UPDATE + bounce_type-aware suppression.

    PR #359 added soft-vs-hard discrimination. Decision is delegated to
    ``src.integrations.instantly_webhook_handler.decide_bounce_action``
    which returns one of ``suppress_hard`` / ``suppress_soft_3x`` /
    ``noop_soft``. The per-message ``mark_bounced`` UPDATE still fires
    unconditionally — a single send attempt being marked bounced is a
    message-level fact independent of whether the *address* should be
    permanently suppressed.

    The repo enforces ``.in_("status", ["pending", "sent"])`` so a
    bounce after an unsubscribe / replied terminal state is a no-op.
    Suppression INSERT happens via recipient_email; the dispatcher
    precheck gates future sends on the suppression row, which is the
    load-bearing defense.

    Per-sequence cancel fires only on ``suppress_*`` outcomes — on
    ``noop_soft`` we *want* the next sequence step to retry the
    recoverable address, that's the whole point of the soft path.

    Out-of-order edge case: if email_bounced arrives before email_sent
    (Instantly's background workers don't guarantee event order), the
    row's provider_message_id is still NULL and the bounce UPDATE
    matches zero rows. Documented; acceptable degraded state. The
    suppression row still lands, so the address is protected on the
    next send cycle.
    """
    from src.integrations.instantly_webhook_handler import (
        SOFT_COUNTER_WINDOW_DAYS,
        decide_bounce_action,
    )

    bounce_reason = _STRIP_CTRL_PATTERN.sub(
        "",
        str(payload.get("bounce_reason") or payload.get("reason") or ""),
    )[:200]
    bounce_type = _STRIP_CTRL_PATTERN.sub(
        "",
        str(payload.get("bounce_type") or ""),
    )[:32]

    msg_row: Optional[dict] = None
    if provider_msg_id and db.client:
        from src.repositories.campaign_message_repo import CampaignMessageRepository

        repo = CampaignMessageRepository(db.client)
        await repo.mark_bounced(provider_msg_id, bounce_reason=bounce_reason)
        msg_row = await _lookup_message_by_provider_id(provider_msg_id)

    # Soft-bounce strike count (includes current event since webhook_events
    # INSERT already ran in _process_instantly_event). On counter failure
    # we override the policy decision to suppress_hard so a DB hiccup
    # never silently shrinks the strike count to zero (which would mean
    # soft bounces never escalate).
    soft_count = 0
    counter_failed = False
    if recipient_email and db.client and bounce_type:
        from src.repositories.webhook_event_repo import WebhookEventRepository

        we_repo = WebhookEventRepository(db.client)
        try:
            soft_count = await we_repo.count_soft_bounces_for_recipient(
                recipient_email,
                window_days=SOFT_COUNTER_WINDOW_DAYS,
            )
        except Exception:
            counter_failed = True
            logger.exception(
                "soft-bounce counter failed; falling back to suppress_hard",
            )

    action = (
        "suppress_hard"
        if counter_failed
        else decide_bounce_action(
            bounce_type,
            soft_count,
        )
    )

    if action == "noop_soft":
        logger.info(
            "soft bounce under threshold — no suppression",
            extra={
                "bounce_type": bounce_type or None,
                "soft_count": soft_count,
                "provider_msg_id": provider_msg_id or None,
            },
        )
        return

    if not recipient_email:
        # Even without recipient, attempt the per-sequence cancel if
        # we have the row context.
        if msg_row and msg_row.get("lead_unique_key") and msg_row.get("sequence_id"):
            from src.repositories.campaign_message_repo import CampaignMessageRepository

            cancel_repo = CampaignMessageRepository(db.client)
            await cancel_repo.cancel_pending_steps_for_lead(
                msg_row["lead_unique_key"],
                sequence_id=msg_row["sequence_id"],
                reason="bounce",
            )
        return

    from src.repositories.suppression_repo import SuppressionRepository

    suppression_reason = (
        "bounce_soft_3x" if action == "suppress_soft_3x" else "bounce_hard"
    )
    suppression_repo = SuppressionRepository(db.client)
    await suppression_repo.add(
        "email",
        recipient_email,
        suppression_reason,
        channel="email",
        source_provider="instantly",
        source_campaign_id=campaign_id_hint
        if _looks_like_uuid(campaign_id_hint)
        else None,
        notes=bounce_reason or None,
    )

    # Phase 15.4 — per-sequence cancel. A bounce on this lead in this
    # sequence kills downstream pending steps in the same sequence.
    # Other sequences for the same lead stay alive (different
    # campaign / context). PR #359: cancel applies only on suppress_*
    # outcomes — noop_soft returned above before reaching this point.
    if msg_row and msg_row.get("lead_unique_key") and msg_row.get("sequence_id"):
        from src.repositories.campaign_message_repo import CampaignMessageRepository

        cancel_repo = CampaignMessageRepository(db.client)
        await cancel_repo.cancel_pending_steps_for_lead(
            msg_row["lead_unique_key"],
            sequence_id=msg_row["sequence_id"],
            reason="bounce",
        )


async def _instantly_handle_unsubscribed(
    provider_msg_id: str,
    recipient_email: str,
    campaign_id_hint: Optional[str],
) -> None:
    """email_unsubscribed: state-machine UPDATE → unsubscribed + suppression(all).

    Repo allows ``pending|sent|replied → unsubscribed`` (recipient
    might have replied positively then later opted out; legitimate).
    Bounced → unsubscribed is excluded — a bounced address can't
    legitimately opt out, that event would be spurious.

    Channel='all' on the suppression because unsubscribe = "stop
    everything from this sender". Bounces stay scoped to channel='email'.
    """
    msg_row: Optional[dict] = None
    if provider_msg_id and db.client:
        from src.repositories.campaign_message_repo import CampaignMessageRepository

        repo = CampaignMessageRepository(db.client)
        await repo.mark_unsubscribed(provider_msg_id)
        msg_row = await _lookup_message_by_provider_id(provider_msg_id)

    if recipient_email:
        from src.repositories.suppression_repo import SuppressionRepository

        suppression_repo = SuppressionRepository(db.client)
        await suppression_repo.add(
            "email",
            recipient_email,
            "unsubscribe",
            channel="all",
            source_provider="instantly",
            source_campaign_id=campaign_id_hint
            if _looks_like_uuid(campaign_id_hint)
            else None,
        )

    # Phase 15.4 — CROSS-SEQUENCE cancel. Unlike bounce/reply
    # (per-sequence), an unsubscribe expresses "stop everything from
    # this sender" — kills every pending touch for this lead across
    # EVERY sequence. The channel='all' suppression above already
    # blocks redelivery to the same address, but explicit
    # cancel-pending saves dispatch cycles + makes the campaign
    # accounting honest.
    lead_uk = None
    if msg_row:
        lead_uk = msg_row.get("lead_unique_key")
    # Fall back to a lead lookup by recipient_email if no row context.
    if not lead_uk and recipient_email and db.client:
        try:
            lead_rows = await asyncio.to_thread(
                lambda: (
                    db.client.table("leads")
                    .select("unique_key")
                    .eq("email", recipient_email)
                    .limit(1)
                    .execute()
                )
            )
            data = getattr(lead_rows, "data", None) or []
            if data:
                lead_uk = data[0].get("unique_key")
        except Exception:
            logger.exception("unsubscribe cross-sequence cancel lead lookup failed")

    if lead_uk and db.client:
        from src.repositories.campaign_message_repo import CampaignMessageRepository

        cancel_repo = CampaignMessageRepository(db.client)
        await cancel_repo.cancel_pending_steps_for_lead(
            lead_uk,
            sequence_id=None,  # cross-sequence
            reason="unsubscribed_cross_channel",
        )


async def _instantly_handle_replied(provider_msg_id: str) -> None:
    """email_replied: state-machine UPDATE sent → replied.

    Repo gates on ``status='sent'`` only — a reply arriving on a
    bounced / unsubscribed / pending row is excluded as inconsistent
    (a pending row reply would mean the send never went out yet
    Instantly heard back; impossible without bypass paths we don't
    have).

    Full reply-classifier (Phase 16) reads the body off the webhook
    payload and writes pos/neg/ooo/objection labels. Until then we
    just flag the message so the operator's reply-inbox view can
    pivot off it.
    """
    if not provider_msg_id or not db.client:
        return
    from src.repositories.campaign_message_repo import CampaignMessageRepository

    repo = CampaignMessageRepository(db.client)
    await repo.mark_replied(provider_msg_id)

    # Phase 15.4 — per-sequence cancel + replied-branch advance.
    # Cancel ANY pending step in this sequence (kills the
    # always/no_reply continuation). Then check whether the next
    # step is the inverted 'replied' branch — if so, advance into it.
    msg_row = await _lookup_message_by_provider_id(provider_msg_id)
    if not (msg_row and msg_row.get("lead_unique_key") and msg_row.get("sequence_id")):
        return
    await repo.cancel_pending_steps_for_lead(
        msg_row["lead_unique_key"],
        sequence_id=msg_row["sequence_id"],
        reason="reply_received",
    )
    # Replied-branch advance — only fires when the next step is
    # marked branch_condition='replied'.
    if msg_row.get("step_id"):
        from src.repositories.sequence_step_repo import SequenceStepRepository
        from src.services.sequence_advancer import advance_to_next_step

        step_repo = SequenceStepRepository(db.client)
        advance_result = await advance_to_next_step(
            current_message=msg_row,
            step_repo=step_repo,
            message_repo=repo,
            event_type="replied",
        )
        logger.info(
            "email_replied advance",
            extra={
                "provider_message_id": provider_msg_id,
                "advanced": advance_result.advanced,
                "reason": advance_result.reason,
            },
        )


async def _lookup_message_by_id(message_id: str) -> Optional[dict]:
    """One-shot SELECT to enrich the row context after a mark_* call.

    Phase 15.4's sequence_advancer needs the full row dict (with
    sequence_id, step_id, lead_unique_key, provider_message_id) to
    compute the next-step schedule. Repo's mark_* methods don't
    return the row; this helper fetches it via the existing
    PostgREST chain. Returns None on miss / error.
    """
    if not message_id or not db.client:
        return None
    try:
        rows = await asyncio.to_thread(
            lambda: (
                db.client.table("campaign_messages")
                .select("*")
                .eq("id", message_id)
                .limit(1)
                .execute()
            )
        )
    except Exception:
        logger.exception("_lookup_message_by_id failed for %s", message_id)
        return None
    data = getattr(rows, "data", None) or []
    return data[0] if data else None


async def _lookup_message_by_provider_id(provider_msg_id: str) -> Optional[dict]:
    """Mirror of :func:`_lookup_message_by_id` keyed on Instantly's
    ``provider_message_id``. Used by bounce / unsub / reply
    handlers (which receive provider id from the webhook payload,
    not the lds_message_id custom variable)."""
    if not provider_msg_id or not db.client:
        return None
    try:
        rows = await asyncio.to_thread(
            lambda: (
                db.client.table("campaign_messages")
                .select("*")
                .eq("provider_message_id", provider_msg_id)
                .limit(1)
                .execute()
            )
        )
    except Exception:
        logger.exception(
            "_lookup_message_by_provider_id failed for %s",
            provider_msg_id,
        )
        return None
    data = getattr(rows, "data", None) or []
    return data[0] if data else None


def _looks_like_uuid(s) -> bool:
    if not isinstance(s, str) or len(s) != 36:
        return False
    parts = s.split("-")
    return [len(p) for p in parts] == [8, 4, 4, 4, 12] and all(
        c in "0123456789abcdefABCDEF" for p in parts for c in p
    )


# Opaque cursor for /leads keyset pagination. Encodes the page-boundary
# (created_at, unique_key) tuple so successive pages keyset-scan rather
# than OFFSET — required to keep p95 flat as the table grows. Tie-break on
# unique_key is rare in practice (microsecond created_at) but eliminates
# the off-by-one on identical timestamps that pure created_at cursors
# silently lose.
_CURSOR_KEY_MAX = 128  # match leads.unique_key column bound

# Charset gate for the cursor `k` field. Producers are
# `discovery_engine._extract_lead_data` (Google Maps `!1s<id>!` segments)
# + the MD5-hex fallback — both restrict to base64url/hex alphabet. The
# decoder interpolates `k` raw into a PostgREST `.or_()` predicate
# (src/utils/supabase_helper.py), so a permissive cursor with `,` `)` or
# `(` would escape the intended tie-break clause. service_role bypasses
# RLS + single-tenant so this is pagination-scope escape, not
# cross-tenant — but the dashboard contract still relies on the bound.
_CURSOR_KEY_PATTERN = re.compile(r"\A[A-Za-z0-9_-]{1,128}\Z")

# CR/LF/VT/FF strip for webhook-supplied identifiers. Matches the same
# control-char set _CRLFScrubFilter uses for log-line forgery defense.
_STRIP_CTRL_PATTERN = re.compile(r"[\r\n\v\f\x00]")


def _encode_lead_cursor(created_at: str, unique_key: str) -> str:
    payload = json.dumps({"c": created_at, "k": unique_key}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def _decode_lead_cursor(cursor: str) -> Optional[dict]:
    """Decode an opaque cursor. Returns None on any malformed input —
    callers MUST treat a None decode as 'start from first page' and not
    leak the parse error to clients. Bounds the decoded payload so a
    hostile cursor can't feed a 10 MB string into a PostgREST filter."""
    try:
        # Reject obviously huge cursors before base64 decoding to bound CPU.
        if not cursor or len(cursor) > 512:
            return None
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        if len(raw) > 512:
            return None
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        c = data.get("c")
        k = data.get("k")
        if not isinstance(c, str) or not isinstance(k, str):
            return None
        if len(c) > 64 or len(k) > _CURSOR_KEY_MAX:
            return None
        # Charset gate — `k` interpolates raw into a PostgREST .or_()
        # predicate downstream. Reject anything outside the producer
        # alphabet so `,` `)` `(` etc cannot escape the tie-break clause.
        if not _CURSOR_KEY_PATTERN.fullmatch(k):
            return None
        # ISO timestamp sanity check — parse will reject garbage.
        datetime.fromisoformat(c.replace("Z", "+00:00"))
        return {"c": c, "k": k}
    except Exception:  # noqa: BLE001 — any failure means malformed input
        return None


@app.get("/leads", dependencies=[Depends(verify_api_key)])
@limiter.limit("30/minute")
async def list_leads(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200, description="Page size, 1..200"),
    cursor: Optional[str] = Query(
        default=None,
        max_length=512,
        description="Opaque cursor from previous page's next_cursor",
    ),
    include_demo: bool = Query(
        default=False,
        description="Include is_demo=true rows. Default false hides Phase 13.3 demo seed.",
    ),
):
    """Retrieve a page of leads ordered by created_at DESC.

    Backwards-compatible default: no params → first page (50 rows) of
    real (non-demo) leads. `?include_demo=true` returns the union of
    real + demo rows — used by the "Show demo data" toggle.

    Returns {leads, next_cursor, has_more}; next_cursor is null on the
    final page. has_more is the authoritative end-of-stream signal —
    next_cursor==null is sufficient but the dedicated boolean avoids
    clients re-querying just to confirm.
    """
    try:
        if not db.client:
            return error_response("Database not connected", status_code=503)
        decoded = _decode_lead_cursor(cursor) if cursor else None
        # Fetch limit+1 to detect has_more without an extra round-trip.
        rows = await db.list_leads_recent(
            limit=limit + 1, cursor=decoded, include_demo=include_demo
        )
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = None
        if has_more and page:
            tail = page[-1]
            ts = tail.get("created_at")
            uk = tail.get("unique_key")
            if isinstance(ts, str) and isinstance(uk, str):
                next_cursor = _encode_lead_cursor(ts, uk)
        return {"leads": page, "next_cursor": next_cursor, "has_more": has_more}
    except APIError as e:
        logger.error("Database API Error fetching leads: %s", e, exc_info=True)
        return error_response("Failed to fetch leads from database", status_code=502)
    except Exception as e:
        logger.error("Unexpected error fetching leads: %s", e, exc_info=True)
        return error_response("An unexpected error occurred while fetching leads")


MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50MB


def validate_csv_metadata(file: UploadFile) -> Optional[JSONResponse]:
    """Validate filename + content-type before reading body."""
    if not file.filename or not file.filename.lower().endswith(".csv"):
        return error_response("Only CSV files are allowed.", status_code=400)

    if file.content_type and file.content_type not in (
        "text/csv",
        "application/vnd.ms-excel",
    ):
        return error_response(
            f"Invalid content type: {file.content_type}. Expected text/csv.",
            status_code=400,
        )
    return None


async def read_capped(
    file: UploadFile, max_bytes: int
) -> tuple[Optional[bytes], Optional[JSONResponse]]:
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
                f"File too large. Maximum size is {max_bytes // (1024 * 1024)}MB.",
                status_code=413,
            )
        chunks.append(chunk)
    return b"".join(chunks), None


def validate_csv_content(contents: bytes) -> Optional[JSONResponse]:
    """Defense-in-depth magic-byte check after Content-Type allowlist.

    Why: validate_csv_metadata trusts the client-supplied Content-Type; an
    attacker can send any header. Pandas tolerates malformed input so
    binary blobs don't reach a parser RCE, but rejecting at the boundary
    keeps obvious abuse out of the background task queue + temp dir.

    Null bytes don't appear in valid UTF-8 CSV. Common binary magic
    bytes (ZIP/xlsx `PK\\x03\\x04`, PDF, PNG, GIF, ELF, Mach-O) all
    fail the null-byte check OR are explicitly rejected.
    """
    head = contents[:1024]
    if b"\x00" in head:
        return error_response("File appears to be binary, not CSV.", status_code=400)
    if head.startswith((b"PK\x03\x04", b"%PDF", b"\x89PNG", b"GIF8", b"\x7fELF")):
        return error_response("File appears to be binary, not CSV.", status_code=400)
    return None


@app.post("/upload", dependencies=[Depends(verify_api_key)])
@limiter.limit("5/minute")
async def upload_leads(
    request: Request, background_tasks: BackgroundTasks, file: UploadFile = File(...)
):
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
    content_error = validate_csv_content(contents)
    if content_error:
        return content_error

    # Save uploaded file temporarily — UUID name under system tempdir to
    # prevent path traversal and keep uploads out of the cwd.
    import tempfile

    temp_path = os.path.join(
        tempfile.gettempdir(), f"leadscraper_{uuid.uuid4().hex}.csv"
    )
    async with aiofiles.open(temp_path, "wb") as buffer:
        await buffer.write(contents)

    background_tasks.add_task(process_csv_background, temp_path)
    return {
        "filename": file.filename,
        "status": "processing",
        "message": "Leads are being imported in the background.",
    }


def _load_and_standardize_csv(temp_path: str) -> "pd.DataFrame":
    from src.utils.csv_helper import load_csv_with_unique_key

    df = load_csv_with_unique_key(temp_path)
    df.columns = [col.lower().replace(" ", "_") for col in df.columns]
    return df


def _apply_ai_mapping(df: "pd.DataFrame") -> "pd.DataFrame":
    """Rename CSV columns to the canonical lead schema using GeminiMapper.

    Guards against two failure modes that the BUGS.md Round 4 E2E surfaced:
    1. The mapper sometimes returns identity self-maps (`name → name`,
       `website → website`) for columns the CSV doesn't carry — purely
       hallucinated. `df.rename` would then create duplicate column
       names alongside the empty placeholders `csv_helper` pre-creates.
    2. After `rename`, two columns can share a target name (e.g. an empty
       `email` placeholder and the freshly-renamed `mail → email`).
       Pandas' `to_dict('records')` then silently drops one — usually the
       populated source. Coalesce non-null values across duplicates so
       the populated column wins.
    """
    from src.processors.ai_mapper import GeminiMapper

    mapper = GeminiMapper()
    mapping = mapper.get_column_mapping(df.columns.tolist())
    if not mapping:
        return df
    existing_cols = set(df.columns)
    filtered = {
        src: tgt for src, tgt in mapping.items() if src in existing_cols and src != tgt
    }
    if not filtered:
        logger.info(
            "AI mapping returned no actionable renames (all identity self-maps or unknown sources): %s",
            mapping,
        )
        return df
    logger.info("AI suggested mapping (filtered): %s", filtered)
    df = df.rename(columns=filtered)
    if df.columns.duplicated().any():
        df = _coalesce_duplicate_columns(df)
    return df


def _coalesce_duplicate_columns(df: "pd.DataFrame") -> "pd.DataFrame":
    """For each duplicate column name, merge the columns by taking the
    first non-null value per row (left-to-right via `bfill` across the
    duplicate group). Returns a frame with unique column names. Logs the
    coalesced names for observability."""
    import pandas as pd

    if not df.columns.duplicated().any():
        return df
    dup_names = df.columns[df.columns.duplicated()].unique().tolist()
    logger.warning(
        "Coalescing %d duplicate column group(s) after AI mapping: %s",
        len(dup_names),
        dup_names,
    )
    seen = set()
    result = {}
    for name in df.columns:
        if name in seen:
            continue
        seen.add(name)
        positions = [i for i, c in enumerate(df.columns) if c == name]
        if len(positions) == 1:
            result[name] = df.iloc[:, positions[0]]
        else:
            block = df.iloc[:, positions]
            result[name] = block.bfill(axis=1).iloc[:, 0]
    return pd.DataFrame(result)


def _filter_valid_columns(df: "pd.DataFrame") -> "pd.DataFrame":
    valid_cols = [
        "unique_key",
        "name",
        "company_name",
        "website",
        "email",
        "phone",
        "address",
        "rating",
        "reviews",
        "lead_source",
        "audit_status",
        "audit_results",
        "enrichment_status",
        "high_risk_flag",
        "seo_score",
        "outreach_score",
        "company_size",
        "leadership_team",
        "key_offerings",
        "contact_details",
        "business_details",
        "target_clients",
        "pain_points",
        "segment",
        "email_hook",
        "linkedin_hook",
        "facebook",
        "instagram",
        "linkedin",
        "tiktok",
        "pinterest",
    ]
    return df[[col for col in df.columns if col in valid_cols]]


def _upsert_leads_to_db(df: "pd.DataFrame") -> tuple[int, int, int]:
    """Upsert the dataframe; returns `(submitted, deduped, inserted)`.

    The previous implementation returned `len(leads_dict)` regardless of
    whether `db.upsert_leads` succeeded — so a schema-mismatch APIError
    swallowed inside `upsert_leads` looked identical to a successful insert
    in the upload-handler log. Inspect the return value here instead.

    Same-`unique_key`-in-batch dedupe: PostgREST upsert into a column with
    a unique constraint fails the WHOLE batch with Postgres error 21000
    (`ON CONFLICT DO UPDATE command cannot affect row a second time`) if
    the same `unique_key` appears more than once in the payload. A CSV
    with three rows sharing the same Name + Website + email derives the
    same `unique_key` three times via `load_csv_with_unique_key` →
    without this dedupe the upload silently inserts 0 rows. Verified
    prod repro 2026-05-30 against `kbtkxpvchmunwjykbeht`; recipe pinned
    by `tests/unit/test_upload_dedupe.py`. `keep='last'` matches the
    "operator is reuploading newer data" intent.
    """
    import pandas as pd

    submitted = len(df)
    deduped = 0
    if submitted and "unique_key" in df.columns:
        df = df.drop_duplicates(subset=["unique_key"], keep="last")
        deduped = submitted - len(df)
        if deduped:
            logger.warning(
                "csv_upload_dedup_collapse",
                extra={
                    "rows_submitted": submitted,
                    "rows_deduped": deduped,
                    "rows_remaining": len(df),
                },
            )

    leads_dict = df.to_dict("records")
    # Clean up NaN for JSON serialization
    leads_dict = [
        {k: (None if pd.isna(v) else v) for k, v in lead.items()} for lead in leads_dict
    ]
    if not leads_dict:
        # Upstream parser fell back to an empty frame (see BUGS.md Round 4 B).
        # Short-circuit so we don't hit a misleading PGRST100 from supabase-py
        # complaining about an empty columns parameter — surface the real
        # cause (no rows survived parsing) instead.
        logger.error(
            "Upsert called with 0 leads — upstream parse likely failed; see prior csv_helper / mapping logs."
        )
        return submitted, deduped, 0
    logger.info(
        "Upserting %d leads with columns: %s", len(leads_dict), df.columns.tolist()
    )
    result = db.upsert_leads(leads_dict)
    if result is None:
        return submitted, deduped, 0
    inserted = len(getattr(result, "data", None) or [])
    return submitted, deduped, inserted


def process_csv_background(temp_path: str):
    """Background task to process the uploaded CSV."""
    from pathlib import Path

    submitted = deduped = inserted = 0
    try:
        df = _load_and_standardize_csv(temp_path)
        df = _apply_ai_mapping(df)
        final_df = _filter_valid_columns(df)
        submitted, deduped, inserted = _upsert_leads_to_db(final_df)
        if inserted == 0:
            logger.error(
                "Upload completed but 0 rows landed in Supabase. "
                "Check the supabase_helper upsert error above for the cause "
                "(typical: schema mismatch / missing column)."
            )
        else:
            logger.info("Successfully processed and upserted %d leads.", inserted)
            # Lead counts and source mix just changed — drop the cached
            # /stats payload so the next request sees the new totals.
            stats_cache.invalidate()
    except Exception as e:
        logger.error("Error processing upload: %s", e, exc_info=True)
    finally:
        # Single structured summary line — operator greps Render logs for
        # `csv_upload_complete` and spots silent zero-insert when
        # rows_submitted > 0 but rows_inserted == 0.
        logger.info(
            "csv_upload_complete",
            extra={
                "rows_submitted": submitted,
                "rows_deduped": deduped,
                "rows_inserted": inserted,
            },
        )
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
    response = (
        db.client.table("orchestration_jobs")
        .select("*")
        .eq("status", "running")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if response.data:
        job = response.data[0]
        return {
            "active": True,
            "processed": job["processed_count"],
            "total": job["total_count"],
            "current_chunk": 0,
        }
    return {"active": False, "processed": 0, "total": 0}


@app.post("/audit/stop", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
async def stop_audit(request: Request):
    """Signal the orchestrator to stop all running jobs."""
    if not db.client:
        return error_response("Database not connected", status_code=503)
    db.client.table("orchestration_jobs").update(
        {"status": "stopped", "current_phase": "Stopped by user"}
    ).eq("status", "running").execute()

    auditor.stop()
    return {"status": "stopped"}


@app.get("/health/schema", dependencies=[Depends(verify_api_key)])
@limiter.limit("12/minute")
async def health_schema(request: Request):
    missing = db.check_schema()
    return {
        "status": "healthy" if not missing else "degraded",
        "drift": bool(missing),
        "missing_columns_count": len(missing),
    }


@app.post("/ask", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
async def ask_ai(
    request: Request, payload: AskRequest, background_tasks: BackgroundTasks
):
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
            text = (
                plan.get("raw")
                or "I'm not sure what you'd like me to do. Try asking about your leads, scores, or audits."
            )
            return {"response": text}

        # 4. Process-heavy tasks: return the plan for UI confirmation.
        return {
            "plan": plan,
            "response": "I've analyzed your request. Should I proceed with the task: "
            + plan.get("task", "Unknown")
            + "?",
        }
    except (BudgetExceededError, AIQuotaExceededError):
        # Daily Gemini token budget tripped OR upstream Gemini 429 —
        # let the registered exception handlers map to 503 with the
        # canonical body.  Without the re-raise the generic catch below
        # would surface a 500 "Failed to process instruction" instead.
        raise
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
    except (BudgetExceededError, AIQuotaExceededError):
        # See /ask handler — re-raise so the global budget / AI-quota
        # handlers map to 503 instead of the generic 500.
        raise
    except Exception as e:
        logger.error("Error getting insights: %s", e, exc_info=True)
        return error_response("Insights currently unavailable")


async def _compute_stats(include_demo: bool = False) -> dict:
    """Build the /stats response from scratch.

    Pulled out of the handler so `stats_cache.get(_compute_stats)` can
    memoize the result and skip the pandas DataFrame allocation on
    cache-hit requests. Returns the same dict shape /stats returns;
    callers must NOT mutate the result (it may be shared across
    in-flight requests via the cache).

    `include_demo` defaults False — only the default path is cached
    (operator default view). The "Show demo data" toggle invokes
    `include_demo=True` directly, bypassing the cache.
    """
    import pandas as pd

    leads = await db.get_stats_rows(include_demo=include_demo)

    if not leads:
        return {
            "total_leads": 0,
            "audit_status_distribution": [],
            "seo_score_ranges": [],
            "source_distribution": [],
        }

    df = pd.DataFrame(leads)

    # 1. Audit Status Distribution
    status_dist = df["audit_status"].value_counts().to_dict()
    status_list = [{"name": k, "value": int(v)} for k, v in status_dist.items()]

    # 2. SEO Score Ranges (None/NaN coerced + dropped)
    scores = pd.to_numeric(df["seo_score"], errors="coerce").dropna()
    score_bins = [0, 20, 40, 60, 80, 100]
    score_labels = ["0-20", "21-40", "41-60", "61-80", "81-100"]
    score_ranges = (
        pd.cut(scores, bins=score_bins, labels=score_labels).value_counts().to_dict()
    )
    score_list = [{"range": k, "count": int(v)} for k, v in score_ranges.items()]

    # 3. Source Distribution (top 5)
    source_dist = df["lead_source"].fillna("Unknown").value_counts().head(5).to_dict()
    source_list = [{"name": k, "value": int(v)} for k, v in source_dist.items()]

    return {
        "total_leads": len(df),
        "audit_status_distribution": status_list,
        "seo_score_ranges": score_list,
        "source_distribution": source_list,
    }


@app.get("/stats", dependencies=[Depends(verify_api_key)])
@limiter.limit("30/minute")
async def get_stats(
    request: Request,
    include_demo: bool = Query(
        default=False,
        description="Include is_demo=true rows. Bypasses the cache when true.",
    ),
):
    """Retrieve structured statistics about leads for charting.

    Default path (`include_demo=false`) is memoized for
    `stats_cache.ttl_seconds` (default 60s). Write paths (/upload
    completion, orchestrator job finish, /leads/demo wipe) call
    `stats_cache.invalidate()` so the next /stats sees fresh numbers.
    A stampede lock inside `stats_cache.get` guarantees N concurrent
    /stats at expiry only run the DataFrame build once.

    `include_demo=true` bypasses the cache — it's an operator-toggle
    code path (the "Show demo data" pill), called rarely, and caching
    a second variant adds invalidation complexity without a real
    throughput win.
    """
    try:
        if not db.client:
            return error_response("Database not connected", status_code=503)
        if include_demo:
            return await _compute_stats(include_demo=True)
        payload = await stats_cache.get(_compute_stats)
        return payload
    except Exception as e:
        logger.error("Error fetching stats: %s", e, exc_info=True)
        return error_response("Failed to fetch stats")


@app.post("/draft-outreach", dependencies=[Depends(verify_api_key)])
@limiter.limit("20/minute")
async def draft_outreach(request: Request, payload: LeadProcessRequest):
    plan = {"task": "OUTREACH_DRAFT", "params": {"unique_key": payload.unique_key}}

    result = await router.execute_task(plan)
    return result


@app.post("/draft-linkedin", dependencies=[Depends(verify_api_key)])
@limiter.limit("20/minute")
async def draft_linkedin(request: Request, payload: LeadProcessRequest):
    plan = {"task": "LINKEDIN_DRAFT", "params": {"unique_key": payload.unique_key}}

    result = await router.execute_task(plan)
    return result


@app.post("/execute", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
async def execute_plan(
    request: Request, plan: ExecutePlanRequest, background_tasks: BackgroundTasks
):
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
    job_id = await orchestrator.run_massive_pipeline(
        lead_ids=[payload.unique_key], tasks=["hunt"]
    )
    return {
        "status": "hunting_started",
        "unique_key": payload.unique_key,
        "job_id": job_id,
    }


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
    return {
        "status": "discovery_started",
        "job_id": job_id,
        "query": payload.query,
        "location": payload.location,
    }


@app.post("/enrich/start", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
async def start_enrichment(request: Request, payload: LeadProcessRequest):
    """Trigger the enrichment engine to find missing digital footprints via orchestrator."""
    job_id = await orchestrator.run_massive_pipeline(
        lead_ids=[payload.unique_key], tasks=["enrich"]
    )
    return {
        "status": "enrichment_started",
        "unique_key": payload.unique_key,
        "job_id": job_id,
    }


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
    stats_cache.invalidate()
    return {"status": "cleared", "message": "All leads and jobs have been deleted."}


class DemoLeadsDeletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Literal pins the exact confirmation phrase — wrong value = 422 via
    # Pydantic before the handler runs. Narrower than /leads/clear (which
    # has no body) because the operator-facing Settings UI surfaces this
    # behind a type-to-confirm modal, and the typed phrase is the
    # cheap proof-of-intent that survives a misclick.
    confirmation: Literal["REMOVE DEMO"]


@app.delete(
    "/leads/demo",
    dependencies=[Depends(verify_api_key), Depends(verify_admin_token)],
)
@limiter.limit("3/hour")
async def delete_demo_leads(request: Request, payload: DemoLeadsDeletionRequest):
    """Wipe Phase 13.3 demo seed rows (`is_demo=true`) and any
    `campaign_messages` referencing them. Bound by the same admin-token
    + API-key + rate-limit gates as `/leads/clear`, plus a Pydantic
    Literal body to prevent accidental wipes from a stray DELETE.

    Returns the row counts so the operator-facing toast can show
    `"Removed N demo leads (M messages)"`. Both counts may be 0 — the
    operator may have already cleared them via SQL Studio.
    """
    if not db.client:
        return error_response("Database not connected", status_code=503)
    try:
        counts = await asyncio.to_thread(db.delete_demo_leads)
    except Exception as exc:
        logger.exception("Demo-data wipe failed: %s", exc)
        return error_response("Failed to remove demo leads")
    logger.warning(
        "DESTRUCTIVE: /leads/demo invoked — %d demo leads + %d messages wiped.",
        counts.get("leads_deleted", 0),
        counts.get("messages_deleted", 0),
    )
    stats_cache.invalidate()
    return {"status": "cleared", **counts}


# -----------------------------------------------------------------------------
# GDPR Article 17 (right to erasure).
#
# DELETE /operator/account wipes the entire dataset tied to the operator's
# account. Defense gating, top to bottom:
#   1. X-API-Key                 (every authed endpoint)
#   2. X-Admin-Token             (same gate as /leads/clear)
#   3. Pydantic Literal confirm  (exact "DELETE MY ACCOUNT" phrase — wrong
#                                  value = 422 BEFORE the handler runs)
#   4. Rate limit                (1/hour, peer-IP keyed; XFF spoof can't
#                                  unlock unlimited deletes)
#
# Audit row is written to `account_deletions` BEFORE the destructive
# operation — partial-failure paths still leave a trace. 30-day retention
# for fraud / contested-deletion windows; purged by
# `src/scripts/purge_expired_audit_log.py` (wired in security.yml daily).
#
# Documented in docs/legal/privacy-policy.md §retention.
# -----------------------------------------------------------------------------
class AccountDeletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Literal pins the exact confirmation phrase. Any other value (typo,
    # different casing, attempted bypass) returns 422 via Pydantic
    # before the handler executes — the destructive step never runs.
    confirmation: Literal["DELETE MY ACCOUNT"]


# Sentinels used to filter "delete every row" — PostgREST requires a
# WHERE clause on DELETE for safety. A UUID that never appears in
# practice + a string that never appears in `unique_key` keep the
# filter trivially true for every real row.
#
# Footgun caveat: a row whose `id` IS the all-zero UUID (or a lead whose
# `unique_key` matches the sentinel string) would escape the wipe.
# `gen_random_uuid()` produces all-zero with probability ~2^-122 — so
# astronomically unlikely in normal operation, but a test fixture that
# explicitly seeds the sentinel value could trip it. If we ever need
# a stronger guarantee (e.g. compliance attestation that DELETE truly
# touches every row), swap to `.gte("created_at", "1970-01-01")` — a
# predicate that matches every row whose timestamp is sane.
_NEVER_UUID = "00000000-0000-0000-0000-000000000000"
_NEVER_UNIQUE_KEY = "__sentinel_never_a_real_unique_key__"


@app.delete(
    "/operator/account",
    dependencies=[Depends(verify_api_key), Depends(verify_admin_token)],
)
@limiter.limit("1/hour", key_func=get_remote_address)
async def delete_operator_account(request: Request, payload: AccountDeletionRequest):
    """GDPR Article 17 (right to erasure). Hard-deletes every row tied to
    the operator's account: `leads`, `campaigns`, `campaign_messages`,
    `orchestration_jobs`. Single-operator semantics (ADR-001) — the
    entire dataset belongs to the operator, so the wipe is unconditional.

    Pre-deletion row counts are snapshotted into an `account_deletions`
    audit row (30-day retention, then purged) so a contested deletion
    can be traced to: when, by whom (`OPERATOR_EMAIL` env), from where
    (`remote_ip`), and what was wiped.

    Response payload includes the `audit_id` + `audit_expires_at` so the
    operator can reference the audit row during the 30-day window.
    """
    if not db.client:
        return error_response("Database unavailable.", 503)

    operator_email = (os.getenv("OPERATOR_EMAIL", "") or "").strip() or None
    now = datetime.now(timezone.utc)

    def _iso(dt: datetime) -> str:
        return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def _count(table_name: str, key_col: str) -> int:
        try:
            r = db.client.table(table_name).select(key_col, count="exact").execute()
            return int(getattr(r, "count", None) or 0)
        except Exception as e:
            # Counts are best-effort context for the audit log; a partial
            # count is better than failing the whole flow on a transient
            # error. The actual deletion still runs.
            logger.warning("Could not count %s: %s", table_name, e)
            return 0

    row_counts = {
        "leads": await asyncio.to_thread(_count, "leads", "unique_key"),
        "campaigns": await asyncio.to_thread(_count, "campaigns", "id"),
        "campaign_messages": await asyncio.to_thread(_count, "campaign_messages", "id"),
        "orchestration_jobs": await asyncio.to_thread(
            _count, "orchestration_jobs", "id"
        ),
    }

    audit_id = str(uuid.uuid4())
    audit_entry = {
        "id": audit_id,
        "deleted_at": _iso(now),
        "operator_email": operator_email,
        "remote_ip": get_remote_address(request),
        "row_counts": row_counts,
        "expires_at": _iso(now + timedelta(days=30)),
    }

    def _write_audit() -> None:
        db.client.table("account_deletions").insert(audit_entry).execute()

    try:
        await asyncio.to_thread(_write_audit)
    except Exception as e:
        logger.error("Account deletion ABORTED — audit log write failed: %s", e)
        return error_response(
            "Audit log write failed; deletion aborted (no rows removed).",
            503,
        )

    # Delete in FK dependency order. campaign_messages → campaigns avoids
    # CASCADE rollback surprises; orchestration_jobs has no FK out;
    # leads is last (lead_unique_key in campaign_messages already gone
    # by the time we touch leads).
    def _delete_all(table_name: str, key_col: str, sentinel: str) -> None:
        db.client.table(table_name).delete().neq(key_col, sentinel).execute()

    await asyncio.to_thread(_delete_all, "campaign_messages", "id", _NEVER_UUID)
    await asyncio.to_thread(_delete_all, "campaigns", "id", _NEVER_UUID)
    await asyncio.to_thread(_delete_all, "orchestration_jobs", "id", _NEVER_UUID)
    await asyncio.to_thread(_delete_all, "leads", "unique_key", _NEVER_UNIQUE_KEY)

    logger.warning(
        "DESTRUCTIVE: /operator/account invoked — full wipe. "
        "audit_id=%s row_counts=%s remote_ip=%s operator=%s",
        audit_id,
        row_counts,
        audit_entry["remote_ip"],
        operator_email,
    )

    return {
        "status": "deleted",
        "audit_id": audit_id,
        "row_counts_deleted": row_counts,
        "audit_retention_days": 30,
        "audit_expires_at": audit_entry["expires_at"],
    }


@app.post("/orchestrator/start", dependencies=[Depends(verify_api_key)])
@limiter.limit("3/minute")
async def start_massive_pipeline(request: Request, payload: PipelineRequest):
    if not db.client:
        return error_response("Database not connected", status_code=503)
    # Orchestrator's `run_massive_pipeline(filters=...)` expects a plain
    # dict (or None) — `model_dump(exclude_none=True)` keeps the typed
    # boundary at the HTTP edge without changing the downstream signature.
    filters_dict = (
        payload.filters.model_dump(exclude_none=True) if payload.filters else None
    )
    job_id = await orchestrator.run_massive_pipeline(
        filters=filters_dict, lead_ids=payload.lead_ids, tasks=payload.tasks
    )
    return {"status": "job_started", "job_id": job_id}


@app.get("/orchestrator/status/{job_id}", dependencies=[Depends(verify_api_key)])
@limiter.limit("60/minute")
async def get_job_status(request: Request, job_id: str):
    if not db.client:
        return error_response("Database not connected", status_code=503)
    status = await orchestrator.get_job_status(job_id)
    return status


@app.get("/orchestrator/active", dependencies=[Depends(verify_api_key)])
@limiter.limit("60/minute")
async def get_active_job(request: Request):
    """Return the most recent running/starting orchestration job, or null.
    Lets a second tab see a job started by another tab — process-local
    state in ParallelAuditor is not shared across workers, so the
    authoritative signal is the orchestration_jobs row."""
    if not db.client:
        return error_response("Database not connected", status_code=503)
    resp = (
        db.client.table("orchestration_jobs")
        .select("*")
        .in_("status", ["starting", "running"])
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return {"job": resp.data[0] if resp.data else None}


def _rows_to_sanitized_csv(rows: list) -> str:
    """Convert a list of dict rows to a CSV string. Uses csv.DictWriter
    with QUOTE_MINIMAL (auto-quotes cells with delimiter / quote / CR /
    LF so embedded newlines in a lead name don't corrupt the row).
    Applies the same CSV-injection guard as every other export site —
    `sanitize_csv_cell` prefixes formula-trigger chars (`= @ + - \\t \\r`)
    with `'`. JSON / dict cells are flattened via `json.dumps` first so
    they don't end up as Python `repr` blobs in the CSV.

    Column order is the union of all keys in insertion order of first
    occurrence — but downstream tooling SHOULD consume by header name,
    not column index, because schema drift across rows is possible and
    the column ordering is an implementation detail, not a contract.

    Empty rows list → empty string (zero-byte CSV file in the ZIP).
    The authoritative "table was empty vs export was corrupted" marker
    is ``audit_log.json``'s ``row_counts`` field — a zero-byte CSV
    paired with ``row_counts.leads == 0`` is a healthy empty-table
    export; a zero-byte CSV paired with a non-zero row count is a
    corruption signal."""
    if not rows:
        return ""
    from src.utils.csv_helper import sanitize_csv_cell

    columns: list = []
    seen: set = set()
    for r in rows:
        for k in r.keys():
            if k not in seen:
                columns.append(k)
                seen.add(k)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        out_row = {}
        for col in columns:
            v = r.get(col)
            if isinstance(v, (dict, list)):
                v = json.dumps(v, default=str, ensure_ascii=False)
            out_row[col] = sanitize_csv_cell(v)
        writer.writerow(out_row)
    return buf.getvalue()


@app.get("/operator/data-export", dependencies=[Depends(verify_api_key)])
# 1/day is the operator's data-loss-prevention budget (GDPR DSAR is a
# per-day event). Pinning the rate-limit key to the TCP peer IP (NOT
# the XFF-honoring `_rate_limit_key` used elsewhere) closes a
# theoretical bypass: a caller with the API key could otherwise rotate
# `X-Forwarded-For` per request to unlock unlimited exports if the
# Render backend's public URL is reachable directly. `get_remote_address`
# returns the immediate-peer IP, which only the proxy (or whoever is
# directly hitting Render) can vary by establishing a new TCP
# connection from a different source — much harder than rotating a
# header on the same socket.
@limiter.limit("1/day", key_func=get_remote_address)
async def operator_data_export(request: Request):
    """GDPR Article 20 (data portability) + Article 15 (right of access).

    Returns a ZIP archive of every row tied to the operator's account:

      - ``leads.csv``           — full `leads` table dump
      - ``campaigns.csv``       — full `campaigns` table dump
      - ``messages.csv``        — full `campaign_messages` table dump
      - ``audit_log.json``      — `orchestration_jobs` history wrapped
                                  with export metadata (timestamp,
                                  operator email from ``OPERATOR_EMAIL``,
                                  schema version, row counts)

    Single-operator semantics (`ADR-001 <docs/adr/001-single-tenant-by-design.md>`):
    the entire dataset belongs to the operator, so the export is
    unconditional — no ``owner_user_id`` filter applies. The
    OPERATOR_EMAIL env var (when set) is stamped into the audit_log
    metadata as the data subject identifier.

    Every string cell across the 3 CSV files goes through
    ``sanitize_csv_cell`` (formula-injection guard) — defense in depth
    even though this export is the operator opening their own data, not
    sharing with attackers.

    Rate limit: **1 per day per rate-limit-key** (the proxy's trusted
    X-Forwarded-For, falling back to peer IP when X-API-Key absent).
    Crossed because a GDPR DSAR is a per-day action, not a per-hour one;
    1/day prevents accidental loop-script abuse while staying generous
    enough that a stuck download can be retried within minutes (the
    `_reset_rate_limiter` test fixture clears state between tests so
    the suite isn't artificially throttled).

    Memory: in-process BytesIO ZIP. Bounded by total DB size; the
    single-operator scale (1000s of leads, 100s of campaigns) fits
    comfortably. If volume crosses ~50 MB, swap to ``zipstream-ng`` for
    streaming-mode ZIP generation — same endpoint shape, different
    body iterator.
    """
    if not db.client:
        return error_response("Database unavailable.", 503)

    # Local import matches existing /export/download + /export/outreach
    # pattern — keeps StreamingResponse out of the module-init cost.
    from fastapi.responses import StreamingResponse

    operator_email = (os.getenv("OPERATOR_EMAIL", "") or "").strip() or None

    # Fetch all four tables. Sync supabase-py calls hop to a thread so
    # the event loop stays unblocked while ZIP construction runs.
    def _fetch_all(table_name: str) -> list:
        result = db.client.table(table_name).select("*").order("created_at").execute()
        return list(getattr(result, "data", None) or [])

    leads_rows = await asyncio.to_thread(_fetch_all, "leads")
    campaigns_rows = await asyncio.to_thread(_fetch_all, "campaigns")
    messages_rows = await asyncio.to_thread(_fetch_all, "campaign_messages")
    jobs_rows = await asyncio.to_thread(_fetch_all, "orchestration_jobs")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("leads.csv", _rows_to_sanitized_csv(leads_rows))
        zf.writestr("campaigns.csv", _rows_to_sanitized_csv(campaigns_rows))
        zf.writestr("messages.csv", _rows_to_sanitized_csv(messages_rows))

        # `orchestration_jobs` IS the operator-action audit trail — every
        # audit / hunt / discovery / enrich / pipeline run wrote a row.
        # Wrap with metadata so the exported ZIP is self-describing
        # (operator email, schema version for forward-compat, row
        # counts so an import-side tool can sanity-check the parse).
        audit_payload = {
            "export_timestamp": datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "operator_email": operator_email,
            "schema_version": "1.0",
            "row_counts": {
                "leads": len(leads_rows),
                "campaigns": len(campaigns_rows),
                "campaign_messages": len(messages_rows),
                "orchestration_jobs": len(jobs_rows),
            },
            "orchestration_jobs": jobs_rows,
        }
        zf.writestr(
            "audit_log.json",
            json.dumps(audit_payload, default=str, ensure_ascii=False, indent=2),
        )

    buffer.seek(0)
    payload_bytes = buffer.getvalue()
    filename = (
        "leadscraper-export-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + ".zip"
    )
    return StreamingResponse(
        iter([payload_bytes]),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "Content-Length": str(len(payload_bytes)),
        },
    )


@app.post("/orchestrator/stop/{job_id}", dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
async def stop_job(request: Request, job_id: str):
    if not db.client:
        return error_response("Database not connected", status_code=503)
    result = await orchestrator.stop_job(job_id)
    return result


# ============================================================
# Streaming CSV exports — bounded memory regardless of lead count.
#
# Previous flow loaded all leads into a pandas DataFrame, wrote 4 CSV
# files to disk, then FileResponse'd one of them. The DataFrame is
# O(rows × cols) RAM. At 10k+ leads on a 512 MB Render dyno that's an
# OOM. Streaming pages through the DB via the existing keyset cursor
# and yields each row as it's serialized — backend RSS stays flat
# regardless of result size. The csv module's QUOTE_MINIMAL with the
# csv_helper sanitiser still applies (Excel formula-injection guard).
# ============================================================

# Column order locked in code (not derived from the first row) so a row
# missing a key doesn't shift downstream columns mid-export.
_EXPORT_FULL_COLUMNS = (
    "unique_key",
    "name",
    "company_name",
    "first_name",
    "email",
    "phone",
    "website",
    "address",
    "lead_source",
    "audit_status",
    "seo_score",
    "high_risk_flag",
    "outreach_score",
    "segment",
    "enrichment_status",
    "company_size",
    "leadership_team",
    "key_offerings",
    "contact_details",
    "business_details",
    "target_clients",
    "pain_points",
    "email_hook",
    "linkedin_hook",
    "facebook",
    "instagram",
    "linkedin",
    "tiktok",
    "pinterest",
    "created_at",
    "updated_at",
)

# Outreach export schema mirrors Instantly's expected fields + custom vars.
_EXPORT_OUTREACH_COLUMNS = (
    "email",
    "first_name",
    "last_name",
    "company_name",
    "website",
    "phone",
    "email_hook",
    "linkedin_hook",
    "pain_points",
    "linkedin",
    "segment",
    "business_details",
    "company_size",
)


def _csv_cell(value) -> str:
    """Cell-level CSV-injection guard mirroring csv_helper.sanitize_dataframe_for_csv.

    Prefixes a leading `'` when the value starts with =, @, +, -, \\t, \\r.
    Excel/Numbers/Sheets render the cell as literal text instead of executing
    =HYPERLINK() / =SUM() on operator open. Identical to the existing batch
    sanitiser; duplicated here because the streaming path doesn't have a
    DataFrame to feed into the helper.
    """
    if value is None:
        return ""
    s = str(value)
    if s and s[0] in ("=", "@", "+", "-", "\t", "\r"):
        return "'" + s
    return s


def _outreach_extract_names(leadership_team: Optional[str]):
    if leadership_team and leadership_team != "Unknown":
        parts = leadership_team.replace(",", " ").split()
        if len(parts) >= 2:
            return parts[0], " ".join(parts[1:])
        if len(parts) == 1:
            return parts[0], ""
    return "Business", "Owner"


async def _stream_leads_csv(
    filter_fn=None, columns=_EXPORT_FULL_COLUMNS, row_transform=None
):
    """Page leads via the keyset helper, yield CSV bytes per chunk.

    `filter_fn(row) -> bool` — optional in-Python row filter (used by
    /export/outreach for the has-contact + score threshold). The DB-level
    filter could be a future optimization; for now we paginate over all
    rows and skip in-Python because the existing logic depends on
    `audit_results` JSONB key parsing.

    `row_transform(row) -> dict` — optional projection. Outreach mode
    uses this to split leadership_team into first/last name fields.
    """
    import csv
    import io

    # Header row first.
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(columns)
    yield buf.getvalue().encode("utf-8")

    # Page through with the existing cursor helper — same query path
    # as the dashboard's /leads pagination. PAGE_SIZE balances DB
    # round-trips against per-batch memory. 200 rows × ~30 cols ≈ 60 KB
    # peak per batch.
    PAGE_SIZE = 200
    cursor = None
    while True:
        rows = await db.list_leads_recent(limit=PAGE_SIZE + 1, cursor=cursor)
        if not rows:
            break
        has_more = len(rows) > PAGE_SIZE
        page = rows[:PAGE_SIZE]
        cursor = None
        if has_more and page:
            tail = page[-1]
            cursor = {"c": tail.get("created_at"), "k": tail.get("unique_key")}

        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        for row in page:
            if filter_fn and not filter_fn(row):
                continue
            data = row_transform(row) if row_transform else row
            writer.writerow([_csv_cell(data.get(c, "")) for c in columns])
        chunk = buf.getvalue()
        if chunk:
            yield chunk.encode("utf-8")

        if not has_more:
            break


def _outreach_filter(row: dict) -> bool:
    """Match src/scripts/export_leads.is_outreach_ready exactly:
    has_contact AND outreach_score > 30 AND a real email string."""
    has_contact = bool(row.get("email")) or bool(row.get("phone"))
    score = row.get("outreach_score") or 0
    try:
        score = float(score)
    except (TypeError, ValueError):
        score = 0
    if not (has_contact and score > 30):
        return False
    email = (row.get("email") or "").strip()
    return bool(email)


def _outreach_transform(row: dict) -> dict:
    first, last = _outreach_extract_names(row.get("leadership_team"))
    return {
        "email": row.get("email", ""),
        "first_name": first,
        "last_name": last,
        "company_name": row.get("company_name") or row.get("name") or "",
        "website": row.get("website", ""),
        "phone": row.get("phone", ""),
        "email_hook": row.get("email_hook", ""),
        "linkedin_hook": row.get("linkedin_hook", ""),
        "pain_points": row.get("pain_points", ""),
        "linkedin": row.get("linkedin", ""),
        "segment": row.get("segment", ""),
        "business_details": row.get("business_details", ""),
        "company_size": row.get("company_size", ""),
    }


@app.get("/export", dependencies=[Depends(verify_api_key)])
@limiter.limit("6/hour")
async def trigger_export(request: Request):
    """Legacy disk-write entry point — kept for CRM workflows that scrape
    files off the `exports/` directory directly. Memory-bound by design;
    do NOT call from automation that doesn't already tolerate the bound.
    Prefer the streaming /export/download or /export/outreach endpoints."""
    try:
        from src.scripts.export_leads import export_leads

        export_leads()
        return {"message": "Exports generated successfully in the 'exports' directory."}
    except Exception as e:
        logger.error("Export error: %s", e, exc_info=True)
        return error_response("Export failed")


@app.get("/export/download", dependencies=[Depends(verify_api_key)])
@limiter.limit("6/hour")
async def download_full_export(request: Request):
    """Stream all leads as CSV. Memory bound is O(PAGE_SIZE) per chunk."""
    from fastapi.responses import StreamingResponse

    if not db.client:
        return error_response("Database not connected", status_code=503)
    filename = f"leads_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        _stream_leads_csv(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            # Transfer-Encoding: chunked is implied by absence of Content-Length,
            # but stating Cache-Control prevents the proxy/edge from buffering.
            "Cache-Control": "no-store",
        },
    )


@app.get("/export/outreach", dependencies=[Depends(verify_api_key)])
@limiter.limit("6/hour")
async def download_outreach_export(request: Request):
    """Stream the outreach-ready subset (has email/phone AND score > 30)."""
    from fastapi.responses import StreamingResponse

    if not db.client:
        return error_response("Database not connected", status_code=503)
    filename = f"crm_outreach_ready_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        _stream_leads_csv(
            filter_fn=_outreach_filter,
            columns=_EXPORT_OUTREACH_COLUMNS,
            row_transform=_outreach_transform,
        ),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


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
            logger.warning(
                "Campaigns table not found. Run the SQL from supabase_schema.sql to create it."
            )
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
        result = (
            db.client.table("campaigns")
            .select("*")
            .order("created_at", desc=True)
            .execute()
        )
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
        campaign = (
            db.client.table("campaigns")
            .select("*")
            .eq("id", campaign_id)
            .maybe_single()
            .execute()
        )
        if not campaign or not campaign.data:
            return error_response("Campaign not found", status_code=404)

        # Performance optimization: Count stats at the database level instead of fetching all rows into memory
        stats = {"pending": 0, "sent": 0, "delivered": 0, "replied": 0, "bounced": 0}

        # We perform individual exact count queries for each status. This is much faster
        # and memory-efficient than returning potentially hundreds of thousands of full rows.
        for status in stats.keys():
            res = (
                db.client.table("campaign_messages")
                .select("id", count="exact")
                .eq("campaign_id", campaign_id)
                .eq("status", status)
                .limit(1)
                .execute()
            )
            stats[status] = res.count or 0

        # We limit the payload to 50 messages to reduce network transfer time and API response size.
        # The frontend only displays the first 50 messages.
        messages = (
            db.client.table("campaign_messages")
            .select("*")
            .eq("campaign_id", campaign_id)
            .limit(50)
            .execute()
        )

        return {
            "campaign": campaign.data,
            "messages": messages.data or [],
            "stats": stats,
            "total_messages": sum(stats.values()),
        }
    except Exception as e:
        logger.error("Error getting campaign %s: %s", campaign_id, e, exc_info=True)
        return error_response("Failed to get campaign")


@app.post("/campaigns/{campaign_id}/generate", dependencies=[Depends(verify_api_key)])
@limiter.limit("3/minute")
async def generate_campaign_messages(
    request: Request, campaign_id: str, background_tasks: BackgroundTasks
):
    """Generate personalized outreach messages for all leads in the campaign's segment."""
    if not db.client:
        return error_response("Database not connected", status_code=503)
    try:
        # maybe_single() — same reasoning as get_campaign: don't let 0-row
        # APIError get swallowed by the broad except below.
        campaign = (
            db.client.table("campaigns")
            .select("*")
            .eq("id", campaign_id)
            .maybe_single()
            .execute()
        )
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
            return error_response(
                "No matching leads found for this segment and channel.", status_code=404
            )

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

                    messages_to_insert.append(
                        {
                            "campaign_id": campaign_id,
                            "lead_unique_key": lead["unique_key"],
                            "channel": "email",
                            "subject": subject,
                            "body": body,
                            "status": "pending",
                        }
                    )

                if camp["channel"] in ["linkedin", "multi"]:
                    hook = lead.get("linkedin_hook") or ""
                    company = lead.get("company_name") or lead_name
                    body = (
                        hook
                        if hook
                        else f"Hi, I came across {company} and was impressed by what you're building. I work in a similar space and would love to connect."
                    )

                    messages_to_insert.append(
                        {
                            "campaign_id": campaign_id,
                            "lead_unique_key": lead["unique_key"],
                            "channel": "linkedin",
                            "subject": None,
                            "body": body,
                            "status": "pending",
                        }
                    )

            if messages_to_insert:
                db.client.table("campaign_messages").insert(
                    messages_to_insert
                ).execute()
                db.client.table("campaigns").update(
                    {"total_leads": len(leads), "status": "draft"}
                ).eq("id", campaign_id).execute()

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
        db.client.table("campaigns").update({"status": "active"}).eq(
            "id", campaign_id
        ).execute()
        return {
            "status": "active",
            "message": "Campaign started. Messages will be sent according to rate limits.",
        }
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
        db.client.table("campaigns").update({"status": "paused"}).eq(
            "id", campaign_id
        ).execute()
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
        import pandas as pd

        messages = (
            db.client.table("campaign_messages")
            .select("lead_unique_key, channel, subject, body, status")
            .eq("campaign_id", campaign_id)
            .execute()
        )

        if not messages.data:
            return error_response(
                "No messages found for this campaign.", status_code=404
            )

        df = pd.DataFrame(messages.data)

        # Enrich with lead data
        unique_keys = df["lead_unique_key"].unique().tolist()
        leads_resp = (
            db.client.table("leads")
            .select("unique_key, name, email, linkedin, company_name, first_name")
            .in_("unique_key", unique_keys)
            .execute()
        )

        leads_df = pd.DataFrame(leads_resp.data) if leads_resp.data else pd.DataFrame()
        if not leads_df.empty:
            df = df.merge(
                leads_df, left_on="lead_unique_key", right_on="unique_key", how="left"
            )

        export_path = f"exports/campaign_{campaign_id[:8]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        os.makedirs("exports", exist_ok=True)
        from src.utils.csv_helper import sanitize_dataframe_for_csv

        sanitize_dataframe_for_csv(df).to_csv(export_path, index=False)

        return FileResponse(
            path=export_path,
            filename=f"campaign_export_{datetime.now().strftime('%Y%m%d')}.csv",
            media_type="text/csv",
        )
    except Exception as e:
        logger.error("Error exporting campaign messages: %s", e, exc_info=True)
        return error_response("Failed to export campaign messages")


# -----------------------------------------------------------------------------
# Admin observability — Gemini daily budget snapshot
#
# Read-only window into the runtime circuit breaker that bounds Gemini
# spend.  Two factors required (same gate posture as `/leads/clear`):
# X-API-Key for the authed surface + X-Admin-Token for the admin
# subspace.  The handler never mutates the counter — the SQLite-level
# UPSERT inside `get_state` for today's row is idempotent.
# -----------------------------------------------------------------------------
@app.get(
    "/admin/gemini-budget",
    dependencies=[Depends(verify_api_key), Depends(verify_admin_token)],
)
@limiter.limit("60/minute")
async def admin_gemini_budget(request: Request):
    """Return today's Gemini-token usage snapshot.

    Body shape: ``{date, used_today, input_today, output_today,
    ceiling, remaining, reset_at_utc}``.  No PII.  Two-factor gated
    (X-API-Key + X-Admin-Token) so a leaked API key alone can't
    observe the runtime budget.
    """
    return _get_gemini_budget_state()


if __name__ == "__main__":
    debug = os.getenv("DEBUG", "False").lower() == "true"
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=debug)
