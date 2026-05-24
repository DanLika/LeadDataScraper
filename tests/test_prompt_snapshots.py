"""
"Prompts are code" guardrail.

For every Gemini call site, snapshot the EXACT final prompt sent to the
model (after `fenced_json` data injection, after lead-index injection,
after env-var substitution). Hash the prompt and pin the hash in
`tests/fixtures/prompt_snapshots.json`. If any prompt drifts, the test
fails and forces an intentional review — either the change was deliberate
(regenerate with `UPDATE_PROMPT_SNAPSHOTS=1`) or it's an unintended
regression.

Call sites covered (one per Gemini prompt builder in the codebase):
  - mapper        ai_mapper.py:60     GeminiMapper.get_column_mapping
  - route         agentic_router.py:171 route_instruction
  - insights      agentic_router.py:523 _get_strategic_insights
  - outreach      agentic_router.py:405 _generate_outreach_draft
  - linkedin      agentic_router.py:472 _generate_linkedin_draft
  - pain_points   leadhunter.py:578     analyze_pain_points_async
  - hooks         leadhunter.py:638     generate_outreach_hooks_async
  - enrich        leadhunter.py:705     enrich_business_data_async

Design notes:
  - NO live Gemini call is made. The test stubs out
    client.models.generate_content / aio.models.generate_content to capture
    the `contents=` and `config.system_instruction` arguments and return
    a per-call-site minimal response that doesn't crash the production
    parser. This means the test is offline + free + needs no API key.
  - Fixtures are fully deterministic — fixed env vars, fixed Supabase
    return rows, no time/random/uuid inputs.
  - We hash both `contents` AND `system_instruction` so a refactor of
    _UNTRUSTED_DATA_SYSTEM_INSTRUCTION also trips the guardrail.

Regenerate snapshots: `UPDATE_PROMPT_SNAPSHOTS=1 pytest tests/test_prompt_snapshots.py`
"""
import asyncio
import hashlib
import json
import os
import sys
import unittest
from pathlib import Path
from typing import Any, Awaitable, Callable
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SNAPSHOT_FILE = Path(__file__).parent / "fixtures" / "prompt_snapshots.json"
UPDATE_MODE = os.getenv("UPDATE_PROMPT_SNAPSHOTS") == "1"

OPERATOR_NAME_FIXTURE = "Test Operator"


def _sha256(s: str | None) -> str:
    return hashlib.sha256(("" if s is None else s).encode("utf-8")).hexdigest()


# ---- Stub response ----------------------------------------------------------

class _StubResponse:
    """
    Minimal response object satisfying everything the production parsers
    touch. Per-call-site `.text` payload is set by the test driver so
    JSON-parsing branches don't raise mid-function (we already captured
    the prompt before that point).
    """
    def __init__(self, text: str):
        self.text = text
        self.candidates = []          # route_instruction's UNKNOWN branch
        self.usage_metadata = None


# ---- Capture ---------------------------------------------------------------

class _PromptCapture:
    """
    Replaces client.models.generate_content (and optionally aio's) with a
    wrapper that records (contents, system_instruction) and returns a
    pre-configured _StubResponse.
    """
    def __init__(self):
        self.contents: Any = None
        self.system_instruction: Any = None
        self.stub_text: str = ""
        self._orig_sync = None
        self._orig_async = None
        self._sync_holder = None
        self._async_holder = None

    def install(self, client, *, has_aio: bool):
        outer = self
        self._sync_holder = client.models
        self._orig_sync = self._sync_holder.generate_content

        def _sync(*args, **kwargs):
            outer.contents = kwargs.get("contents", args[1] if len(args) > 1 else None)
            cfg = kwargs.get("config")
            outer.system_instruction = (
                getattr(cfg, "system_instruction", None) if cfg else None
            )
            return _StubResponse(outer.stub_text)
        self._sync_holder.generate_content = _sync

        if has_aio:
            self._async_holder = client.aio.models
            self._orig_async = self._async_holder.generate_content

            async def _async(*args, **kwargs):
                outer.contents = kwargs.get("contents", args[1] if len(args) > 1 else None)
                cfg = kwargs.get("config")
                outer.system_instruction = (
                    getattr(cfg, "system_instruction", None) if cfg else None
                )
                return _StubResponse(outer.stub_text)
            self._async_holder.generate_content = _async

    def restore(self):
        if self._sync_holder and self._orig_sync:
            self._sync_holder.generate_content = self._orig_sync
        if self._async_holder and self._orig_async:
            self._async_holder.generate_content = self._orig_async


# ---- Fake Supabase (for lead_index, insights, linkedin lookups) ------------

INSIGHT_LEADS = [
    {"name": "Acme Co", "company_name": "Acme Co.", "audit_status": "Completed",
     "seo_score": 40, "lead_source": "google_maps"},
    {"name": "Beta LLC", "company_name": "Beta LLC", "audit_status": "Pending",
     "seo_score": 0, "lead_source": "csv"},
]

LEAD_INDEX_ROWS = [
    {"unique_key": "snap-1", "name": "Snapshot Lead 1", "company_name": "Snapshot Co 1"},
    {"unique_key": "snap-2", "name": "Snapshot Lead 2", "company_name": "Snapshot Co 2"},
]

LINKEDIN_LEAD = {
    "unique_key": "snap-linkedin",
    "leadership_team": "Jane Snapshot",
    "company_name": "Snapshot Linkedin Co",
    "business_details": "A boutique service in the test snapshot fixture.",
    "target_clients": "Other test fixtures and CI runners.",
}


class _FakeExec:
    def __init__(self, rows): self.data = rows


class _FakeQuery:
    def __init__(self, payload):
        self._payload = payload  # callable(eq_value) -> rows
        self._eq_val = None
    def select(self, *_a, **_k): return self
    def eq(self, _col, val):
        self._eq_val = val
        return self
    def limit(self, _n): return self
    def execute(self):
        if callable(self._payload):
            return _FakeExec(self._payload(self._eq_val))
        return _FakeExec(self._payload)


class _FakeSB:
    """Routes by .table() name. Each table returns deterministic rows."""
    def __init__(self):
        self._tables = {}
    def add(self, name, payload):
        self._tables[name] = payload
    def table(self, name):
        return _FakeQuery(self._tables.get(name, []))


# ---- Call-site definitions --------------------------------------------------

# Stub responses tuned so each production function reaches generate_content
# but doesn't raise afterwards (irrelevant — we've captured the prompt by then,
# but cleaner test logs without tracebacks).
STUB_TEXTS = {
    "mapper": "{}",
    "route": "",  # falls into UNKNOWN branch since candidates=[]
    "insights": '{"summary":"x","insights":[],"top_priorities":[]}',
    "outreach": "Subject: x\n\nbody",
    "linkedin": "ok",
    "pain_points": "two-sentence pain-point output.",
    "hooks": '{"linkedin_hook":"x","email_hook":"y"}',
    "enrich": '{"company_size":"x","leadership_team":"x","business_details":"x","target_clients":"x"}',
}


# ---- Test driver ------------------------------------------------------------

@unittest.skipIf(False, "Pure-offline snapshot test — no GEMINI_API_KEY needed")
class TestPromptSnapshots(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        # Pretend a Gemini API key exists so clients initialize. We never
        # actually call Gemini — `install()` replaces the methods first.
        self.env_patcher = patch.dict(os.environ, {
            "GEMINI_API_KEY": "snapshot-fake-key",
            "OPERATOR_NAME": OPERATOR_NAME_FIXTURE,
        })
        self.env_patcher.start()

        # No-op the guarded_generate_content_async budget gate. The test
        # already stubs `client.aio.models.generate_content` via
        # `_PromptCapture.install()`, but the surrounding wrapper
        # `guarded_generate_content_async` *also* calls `check_budget` +
        # `record_usage` against the live SQLite budget DB before the
        # mocked call ever runs. Without these patches the test fails
        # with `BudgetExceededError` once the operator's local budget
        # DB accumulates near the daily ceiling (verified 2026-05-24
        # smoke run: 4999246 / 5000000 tokens). Patching the symbols at
        # `gemini_call` (where they're imported + invoked) keeps the
        # production code path identical except for the gate, so the
        # SHA256 of the captured prompt remains the prod prompt.
        self.budget_check_patcher = patch(
            "src.utils.gemini_call.check_budget", lambda *_a, **_kw: None
        )
        self.budget_check_patcher.start()
        self.budget_record_patcher = patch(
            "src.utils.gemini_call.record_usage", lambda *_a, **_kw: None
        )
        self.budget_record_patcher.start()

        # Fake Supabase with the three tables/queries our call sites trigger.
        # The linkedin path uses .eq("unique_key", val) — we return the fixture
        # only when the val matches snap-linkedin.
        sb = _FakeSB()
        sb.add("leads", lambda eq_val: (
            [LINKEDIN_LEAD] if eq_val == LINKEDIN_LEAD["unique_key"] else
            INSIGHT_LEADS if eq_val is None else
            []
        ))

        # The mapper module instantiates the genai.Client at __init__ — let it.
        # We'll swap generate_content out before invoking get_column_mapping.
        # Also patch SupabaseHelper for the router so lead_index + insights lookups
        # use our deterministic data.
        sb_patcher = patch("src.core.agentic_router.SupabaseHelper")
        sb_mock = sb_patcher.start()
        # NB: the router does TWO different queries via the same client —
        # one for lead_index (.select(...).limit(200)) and one for the
        # linkedin lookup (.select("*").eq("unique_key", ...)). Our _FakeQuery
        # discriminates on whether eq() was called. lead_index calls .limit
        # without .eq → eq_val is None → return INSIGHT_LEADS (used by insights)
        # OR LEAD_INDEX_ROWS (used by route_instruction). They overlap shape;
        # we use LEAD_INDEX_ROWS-shaped data for both to keep snapshots stable.
        # To keep concerns clean: lead_index just needs (unique_key,name,company_name);
        # insights just needs (name,company_name,audit_status,seo_score,lead_source).
        # We feed UNION-of-fields rows so both selects work.
        merged_rows = [
            {**lr, **{"audit_status": "Completed", "seo_score": 40, "lead_source": "google_maps"}}
            for lr in LEAD_INDEX_ROWS
        ]

        def _resolve(eq_val):
            if eq_val == LINKEDIN_LEAD["unique_key"]:
                return [LINKEDIN_LEAD]
            return merged_rows
        sb.add("leads", _resolve)
        sb_mock.return_value.client = sb
        self.sb_patcher = sb_patcher

        # Import after the env patch so any module-level genai.Client() calls
        # see GEMINI_API_KEY.
        from src.processors.ai_mapper import GeminiMapper
        from src.processors.leadhunter import LeadHunter
        from src.core.agentic_router import AgenticRouter

        self.mapper = GeminiMapper()
        self.router = AgenticRouter()
        self.hunter = LeadHunter()

        # Sanity: clients must be initialised
        for name, c in (("mapper", self.mapper.client),
                        ("router", self.router.client),
                        ("hunter", self.hunter.client)):
            self.assertIsNotNone(c, f"{name} Gemini client must initialize")

    async def asyncTearDown(self):
        self.budget_record_patcher.stop()
        self.budget_check_patcher.stop()
        self.sb_patcher.stop()
        self.env_patcher.stop()
        if self.hunter._session and not self.hunter._session.closed:
            await self.hunter.close()

    async def _capture(self, *, client, has_aio: bool, stub_text: str,
                       runner: Callable[[], Awaitable[None]]):
        cap = _PromptCapture()
        cap.stub_text = stub_text
        cap.install(client, has_aio=has_aio)
        try:
            await runner()
        finally:
            cap.restore()
        return cap.contents, cap.system_instruction

    async def _gather_all(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}

        # mapper — sync method; wrap in to_thread to call from async context
        contents, sys_ins = await self._capture(
            client=self.mapper.client, has_aio=False,
            stub_text=STUB_TEXTS["mapper"],
            runner=lambda: asyncio.to_thread(
                self.mapper.get_column_mapping,
                ["Business Name", "Web Address", "Mail", "Tel"],
            ),
        )
        out["mapper"] = self._render(contents, sys_ins,
                                     "headers=['Business Name','Web Address','Mail','Tel']")

        # route_instruction — async wrapper, sync gen call under the hood
        contents, sys_ins = await self._capture(
            client=self.router.client, has_aio=False,
            stub_text=STUB_TEXTS["route"],
            runner=lambda: self.router.route_instruction("find 5 dentists in Sarajevo"),
        )
        out["route"] = self._render(contents, sys_ins,
                                    "instruction='find 5 dentists in Sarajevo', lead_index=fixed-2-rows")

        # insights
        contents, sys_ins = await self._capture(
            client=self.router.client, has_aio=False,
            stub_text=STUB_TEXTS["insights"],
            runner=lambda: self.router._get_strategic_insights(),
        )
        out["insights"] = self._render(contents, sys_ins, "leads=fixed-2-rows")

        # outreach_draft — uses lead_data bypass, no DB
        contents, sys_ins = await self._capture(
            client=self.router.client, has_aio=False,
            stub_text=STUB_TEXTS["outreach"],
            runner=lambda: self.router._generate_outreach_draft({
                "unique_key": "snap-outreach",
                "lead_data": {
                    "name": "Snapshot Outreach Lead",
                    "company_name": "Snapshot Outreach Co",
                    "website": "https://snap.example",
                    "email": "x@snap.example",
                    "audit_results": {
                        "score": 42, "missing_title": False,
                        "missing_description": True, "no_h1": True,
                        "ssl_valid": True,
                        "pain_points": "Missing H1 and meta description on homepage.",
                    },
                },
            }),
        )
        out["outreach"] = self._render(contents, sys_ins,
                                       "lead=Snapshot Outreach Co, OPERATOR_NAME='Test Operator'")

        # linkedin_draft — reads lead from fake DB by unique_key
        contents, sys_ins = await self._capture(
            client=self.router.client, has_aio=False,
            stub_text=STUB_TEXTS["linkedin"],
            runner=lambda: self.router._generate_linkedin_draft({
                "unique_key": LINKEDIN_LEAD["unique_key"],
            }),
        )
        out["linkedin"] = self._render(contents, sys_ins,
                                       f"unique_key={LINKEDIN_LEAD['unique_key']}")

        # pain_points — async, hunter
        contents, sys_ins = await self._capture(
            client=self.hunter.client, has_aio=True,
            stub_text=STUB_TEXTS["pain_points"],
            runner=lambda: self.hunter.analyze_pain_points_async(
                page_text="Acme Co is a dental clinic in Sarajevo with no SSL.",
                business_name="Acme Dental",
                audit_results={
                    "tech_flags": {
                        "has_viewport": True, "has_google_analytics": False,
                        "has_gtm": False, "has_facebook_pixel": False,
                        "has_portal": False, "has_robots_txt": True,
                        "has_sitemap": True,
                    },
                    "red_flags": ["no_ssl"],
                    "cms": "Shopify",
                    "response_time": 1.5,
                },
            ),
        )
        out["pain_points"] = self._render(contents, sys_ins, "Acme Dental, Shopify no-ssl fixture")

        # hooks
        contents, sys_ins = await self._capture(
            client=self.hunter.client, has_aio=True,
            stub_text=STUB_TEXTS["hooks"],
            runner=lambda: self.hunter.generate_outreach_hooks_async(
                pain_points="No SSL and no GA installed.",
                business_name="Acme Dental",
                audit_results={"cms": "Shopify"},
            ),
        )
        out["hooks"] = self._render(contents, sys_ins, "Acme Dental, Shopify hooks fixture")

        # enrich
        contents, sys_ins = await self._capture(
            client=self.hunter.client, has_aio=True,
            stub_text=STUB_TEXTS["enrich"],
            runner=lambda: self.hunter.enrich_business_data_async(
                page_text="Acme Dental is a family-owned clinic since 1998.",
                business_name="Acme Dental",
            ),
        )
        out["enrich"] = self._render(contents, sys_ins, "Acme Dental fixture")

        return out

    @staticmethod
    def _render(contents, system_instruction, description) -> dict[str, Any]:
        contents_str = contents if isinstance(contents, str) else json.dumps(
            contents, sort_keys=True, ensure_ascii=False, default=str
        )
        sys_str = system_instruction if isinstance(system_instruction, str) else str(system_instruction)
        return {
            "contents_hash": _sha256(contents_str),
            "system_instruction_hash": _sha256(sys_str),
            "contents_preview": contents_str[:300],
            "fixture_description": description,
        }

    async def test_prompts_match_committed_snapshots(self):
        current = await self._gather_all()

        if UPDATE_MODE:
            SNAPSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
            SNAPSHOT_FILE.write_text(
                json.dumps(current, indent=2, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            print(f"\n[prompt_snapshots] UPDATE_PROMPT_SNAPSHOTS=1 — wrote {SNAPSHOT_FILE}")
            return  # pass

        if not SNAPSHOT_FILE.exists():
            SNAPSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
            SNAPSHOT_FILE.write_text(
                json.dumps(current, indent=2, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            self.fail(
                f"No baseline snapshot at {SNAPSHOT_FILE}. Wrote initial baseline. "
                f"Inspect the file, commit it, then re-run."
            )

        committed = json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))
        diffs: list[str] = []

        # Detect added / removed call sites
        added = set(current) - set(committed)
        removed = set(committed) - set(current)
        if added:
            diffs.append(f"NEW call sites not in snapshot: {sorted(added)}")
        if removed:
            diffs.append(f"REMOVED call sites no longer rendered: {sorted(removed)}")

        # Per-site hash compare
        for name in sorted(set(current) & set(committed)):
            cur = current[name]
            base = committed[name]
            for field in ("contents_hash", "system_instruction_hash"):
                if cur[field] != base[field]:
                    diffs.append(
                        f"[{name}] {field} drifted\n"
                        f"     baseline:  {base[field]}\n"
                        f"     current :  {cur[field]}\n"
                        f"     preview now: {cur['contents_preview']!r}"
                    )

        if diffs:
            self.fail(
                "Prompt drift detected. Treat as intentional ONLY after review.\n"
                + "\n".join(diffs)
                + f"\n\nTo accept: UPDATE_PROMPT_SNAPSHOTS=1 pytest {Path(__file__).name}"
            )


if __name__ == "__main__":
    unittest.main()
