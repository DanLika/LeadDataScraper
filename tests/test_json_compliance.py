"""
JSON-output compliance for every Gemini call in the codebase that expects
a structured JSON response.

The user brief says: "mapper, judge, business enrichment, score". Notes:
  - mapper       — GeminiMapper.get_column_mapping        (ai_mapper.py:39)
  - insights     — AgenticRouter._get_strategic_insights  (agentic_router.py:507)
                   (analytics over the lead DB — the 'judge'-style call)
  - hooks        — LeadHunter.generate_outreach_hooks_async (leadhunter.py:618)
                   (business enrichment copy generation)
  - enrich       — LeadHunter.enrich_business_data_async  (leadhunter.py:689)
                   (structured business-data extraction)
  - score        — calculate_outreach_score is pure Python (leadhunter.py:411).
                   No Gemini call → nothing to validate here. Covered by
                   tests/test_outreach_score_properties.py.

Per-endpoint protocol:
  - N runs (default 50; override via JSON_COMPLIANCE_RUNS env var)
  - Capture every raw Gemini response.text via a wrapper installed on
    client.models.generate_content / client.aio.models.generate_content
    BEFORE any helper parses it. Lets us measure the model's compliance
    independent of the production code's fallback paths.
  - Parse each raw text and validate against the endpoint's schema.
  - 100% parse + 100% schema-conformant required, else fail.
  - On failure: surface first 3 failure samples with raw text excerpt
    so the fix path is obvious. If pass rate < 100%, the docstring
    points the operator at response_mime_type='application/json' +
    response_schema as the canonical fix.

Live test — requires GEMINI_API_KEY. Skipped otherwise. Supabase mocked.
"""
import asyncio
import json
import os
import re
import sys
import unittest
from typing import Any, Callable
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

GEMINI_KEY = os.getenv("GEMINI_API_KEY")
N_RUNS = int(os.getenv("JSON_COMPLIANCE_RUNS", "50"))
CONCURRENCY = 8

# Mapper allowlist mirrors ai_mapper.py:47-54 — kept in sync intentionally
# (this list is small and worth duplicating to surface schema drift).
STANDARD_COLUMNS = {
    "name", "company_name", "website", "email", "phone", "address",
    "facebook", "instagram", "linkedin", "tiktok", "pinterest",
    "company_size", "leadership_team", "key_offerings", "business_details",
    "target_clients", "pain_points", "segment",
    "rating", "reviews", "seo_score", "outreach_score",
    "email_hook", "linkedin_hook",
}


# ---- Response capture --------------------------------------------------------

class _ResponseCapture:
    """
    Wraps client.models.generate_content / aio.models.generate_content. Each
    call records (label, response.text) before the production code parses it.
    """
    def __init__(self):
        self.records: list[tuple[str, str]] = []
        self._label = "unlabelled"
        self._orig_sync: Callable | None = None
        self._orig_async: Callable | None = None
        self._target_sync = None
        self._target_async = None

    def install(self, client, *, has_aio: bool = True):
        self._target_sync = client.models
        self._orig_sync = self._target_sync.generate_content
        outer = self

        def _wrap_sync(*args, **kwargs):
            resp = outer._orig_sync(*args, **kwargs)
            outer.records.append((outer._label, getattr(resp, "text", "") or ""))
            return resp
        self._target_sync.generate_content = _wrap_sync

        if has_aio:
            self._target_async = client.aio.models
            self._orig_async = self._target_async.generate_content

            async def _wrap_async(*args, **kwargs):
                resp = await outer._orig_async(*args, **kwargs)
                outer.records.append((outer._label, getattr(resp, "text", "") or ""))
                return resp
            self._target_async.generate_content = _wrap_async

    def restore(self):
        if self._target_sync is not None and self._orig_sync is not None:
            self._target_sync.generate_content = self._orig_sync
        if self._target_async is not None and self._orig_async is not None:
            self._target_async.generate_content = self._orig_async

    def set_label(self, label: str):
        self._label = label

    def by_label(self, label: str) -> list[str]:
        return [t for lbl, t in self.records if lbl == label]


# ---- Schema validators -------------------------------------------------------

def _try_parse(raw: str) -> tuple[bool, Any, str]:
    """
    Strip common markdown fences then json.loads. Returns (ok, parsed, reason).
    Reason names the failure mode so the test failure pinpoints WHY.
    """
    text = (raw or "").strip()
    if not text:
        return False, None, "empty response"

    # Strip ```json ... ``` fences
    fence = re.match(r"^```(?:json)?\s*\n(.*?)\n```\s*$", text, flags=re.DOTALL)
    if fence:
        text = fence.group(1).strip()
        # If the fence-stripped body is empty, that's still a parse fail.
        if not text:
            return False, None, "empty fence body"

    # Strip leading "json" label some models prefix
    if text.lower().startswith("json"):
        text = text[4:].strip()
    text = text.strip("`").strip()

    try:
        return True, json.loads(text), ""
    except json.JSONDecodeError as e:
        # Common cause: trailing prose after the JSON. Try to slice the first
        # top-level object out before declaring failure.
        try:
            obj_start = text.index("{")
            depth = 0
            for i in range(obj_start, len(text)):
                if text[i] == "{": depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        return True, json.loads(text[obj_start:i + 1]), ""
        except (ValueError, json.JSONDecodeError):
            pass
        return False, None, f"json.JSONDecodeError: {e.msg}"


def _validate_mapper(parsed: Any, input_headers: list[str]) -> list[str]:
    errs: list[str] = []
    if not isinstance(parsed, dict):
        return [f"top-level type {type(parsed).__name__}, expected dict"]
    input_set = set(input_headers)
    for k, v in parsed.items():
        if not isinstance(k, str):
            errs.append(f"key {k!r} is {type(k).__name__}, expected str")
            continue
        if k not in input_set:
            errs.append(f"key {k!r} not in input headers (extra)")
        if not isinstance(v, str):
            errs.append(f"value for {k!r} is {type(v).__name__}, expected str")
            continue
        if v not in STANDARD_COLUMNS:
            errs.append(f"value {v!r} for {k!r} not in standard_columns")
    return errs


def _validate_insights(parsed: Any) -> list[str]:
    errs: list[str] = []
    if not isinstance(parsed, dict):
        return [f"top-level type {type(parsed).__name__}, expected dict"]
    required = {"summary", "insights", "top_priorities"}
    keys = set(parsed.keys())
    missing = required - keys
    if missing:
        errs.append(f"missing required: {sorted(missing)}")
    extra = keys - required
    if extra:
        errs.append(f"extra fields: {sorted(extra)}")
    # Type checks (only on present keys to avoid follow-on noise)
    if "summary" in parsed and not isinstance(parsed["summary"], str):
        errs.append(f"summary is {type(parsed['summary']).__name__}, expected str")
    if "insights" in parsed:
        ins = parsed["insights"]
        if not isinstance(ins, list):
            errs.append(f"insights is {type(ins).__name__}, expected list")
        else:
            for i, x in enumerate(ins):
                if not isinstance(x, str):
                    errs.append(f"insights[{i}] is {type(x).__name__}, expected str")
    if "top_priorities" in parsed:
        tp = parsed["top_priorities"]
        if not isinstance(tp, list):
            errs.append(f"top_priorities is {type(tp).__name__}, expected list")
        else:
            for i, p in enumerate(tp):
                if not isinstance(p, dict):
                    errs.append(f"top_priorities[{i}] is {type(p).__name__}, expected dict")
                    continue
                tp_required = {"name", "reason"}
                tp_keys = set(p.keys())
                if tp_required - tp_keys:
                    errs.append(f"top_priorities[{i}] missing: {sorted(tp_required - tp_keys)}")
                if tp_keys - tp_required:
                    errs.append(f"top_priorities[{i}] extra: {sorted(tp_keys - tp_required)}")
                for k in ("name", "reason"):
                    if k in p and not isinstance(p[k], str):
                        errs.append(f"top_priorities[{i}].{k} is {type(p[k]).__name__}, expected str")
    return errs


def _validate_hooks(parsed: Any) -> list[str]:
    errs: list[str] = []
    if not isinstance(parsed, dict):
        return [f"top-level type {type(parsed).__name__}, expected dict"]
    required = {"linkedin_hook", "email_hook"}
    keys = set(parsed.keys())
    if required - keys:
        errs.append(f"missing required: {sorted(required - keys)}")
    if keys - required:
        errs.append(f"extra fields: {sorted(keys - required)}")
    for k in ("linkedin_hook", "email_hook"):
        if k in parsed and not isinstance(parsed[k], str):
            errs.append(f"{k} is {type(parsed[k]).__name__}, expected str")
    return errs


def _validate_enrich(parsed: Any) -> list[str]:
    errs: list[str] = []
    if not isinstance(parsed, dict):
        return [f"top-level type {type(parsed).__name__}, expected dict"]
    required = {"company_size", "leadership_team", "business_details", "target_clients"}
    keys = set(parsed.keys())
    if required - keys:
        errs.append(f"missing required: {sorted(required - keys)}")
    if keys - required:
        errs.append(f"extra fields: {sorted(keys - required)}")
    for k in required & keys:
        if not isinstance(parsed[k], str):
            errs.append(f"{k} is {type(parsed[k]).__name__}, expected str")
    return errs


# ---- Per-endpoint runners ----------------------------------------------------

def _aggregate_failures(label: str, raws: list[str], validator) -> tuple[int, list[str]]:
    """Return (failure_count, sample_messages). Samples capped at 3 for readability."""
    failures: list[str] = []
    samples: list[str] = []
    for i, raw in enumerate(raws):
        ok, parsed, reason = _try_parse(raw)
        if not ok:
            failures.append(f"run {i}: parse — {reason}")
            if len(samples) < 3:
                samples.append(f"  parse fail #{i}: {reason}\n     RAW: {raw[:240]!r}")
            continue
        errs = validator(parsed)
        if errs:
            failures.append(f"run {i}: schema — {errs}")
            if len(samples) < 3:
                samples.append(f"  schema fail #{i}: {errs}\n     RAW: {raw[:240]!r}")
    return len(failures), samples


# ---- Fixtures ----------------------------------------------------------------

MAPPER_HEADERS = ["Business Name", "Web Address", "Mail", "Tel"]

HOOKS_PAIN_POINTS = (
    "Your website lacks SSL certificate and Google Analytics is not configured, "
    "which means visitors see security warnings and you have no visibility into "
    "traffic sources."
)
HOOKS_BUSINESS_NAME = "Acme Dental Clinic"

ENRICH_PAGE_TEXT = (
    "Acme Dental Clinic is a family dentistry practice founded by Dr. Sarah "
    "Bennett in 2012. Our team of 4 dentists serves over 2,000 patients in "
    "the Mostar area. We offer cleanings, fillings, whitening, and braces "
    "consultations. Most clients are working families and parents booking "
    "pediatric checkups."
)
ENRICH_BUSINESS_NAME = "Acme Dental Clinic"

INSIGHTS_LEADS = [
    {"name": "Acme Dental", "company_name": "Acme Dental Clinic",
     "audit_status": "Completed", "seo_score": 38, "lead_source": "google_maps"},
    {"name": "BlueWave Plumbing", "company_name": "BlueWave Plumbing",
     "audit_status": "Completed", "seo_score": 22, "lead_source": "csv"},
    {"name": "Studio Vesta", "company_name": "Studio Vesta Architects",
     "audit_status": "Completed", "seo_score": 41, "lead_source": "google_maps"},
    {"name": "NorthStar", "company_name": "NorthStar Logistics",
     "audit_status": "Completed", "seo_score": 19, "lead_source": "csv"},
    {"name": "Petit Café", "company_name": "Petit Café Mostar",
     "audit_status": "Completed", "seo_score": 47, "lead_source": "google_maps"},
]


# ---- Fake Supabase (for insights only) --------------------------------------

class _FakeExecResult:
    def __init__(self, rows): self.data = rows


class _FakeQuery:
    def __init__(self, leads): self._leads = leads
    def select(self, *_a, **_k): return self
    def limit(self, _n): return self
    def execute(self): return _FakeExecResult(self._leads)


class _FakeSupabase:
    def __init__(self, leads): self._leads = leads
    def table(self, _name): return _FakeQuery(self._leads)


# ---- Test --------------------------------------------------------------------

@unittest.skipUnless(GEMINI_KEY, "Requires GEMINI_API_KEY for live Gemini calls")
class TestJSONCompliance(unittest.IsolatedAsyncioTestCase):
    """100% JSON parse + schema conformance over N runs per endpoint."""

    async def asyncSetUp(self):
        self.env_patcher = patch.dict(os.environ, {"GEMINI_API_KEY": GEMINI_KEY or ""})
        self.env_patcher.start()

        # Suppress crawlbase warning during imports — informational only.
        from src.processors.ai_mapper import GeminiMapper
        from src.processors.leadhunter import LeadHunter
        from src.core.agentic_router import AgenticRouter

        self.mapper = GeminiMapper()
        self.hunter = LeadHunter()

        # Router needs a Supabase that returns the fixture leads for the
        # insights call's pre-fetch.
        sb_patcher = patch("src.core.agentic_router.SupabaseHelper")
        sb_mock = sb_patcher.start()
        sb_mock.return_value.client = _FakeSupabase(INSIGHTS_LEADS)
        self.sb_patcher = sb_patcher

        self.router = AgenticRouter()
        for name, c in (("mapper", self.mapper.client),
                        ("hunter", self.hunter.client),
                        ("router", self.router.client)):
            self.assertIsNotNone(c, f"{name} Gemini client must initialize")

        # Install capture on each client. mapper + router use sync only;
        # hunter uses aio (its calls are async).
        self.cap_mapper = _ResponseCapture(); self.cap_mapper.install(self.mapper.client, has_aio=False)
        self.cap_hunter = _ResponseCapture(); self.cap_hunter.install(self.hunter.client, has_aio=True)
        self.cap_router = _ResponseCapture(); self.cap_router.install(self.router.client, has_aio=False)

        sem = asyncio.Semaphore(CONCURRENCY)

        async def gated(coro_factory):
            async with sem:
                return await coro_factory()

        # MAPPER — sync; bridge via to_thread.
        self.cap_mapper.set_label("mapper")
        await asyncio.gather(*[
            gated(lambda: asyncio.to_thread(self.mapper.get_column_mapping, list(MAPPER_HEADERS)))
            for _ in range(N_RUNS)
        ])

        # INSIGHTS — already an async coroutine; await directly.
        self.cap_router.set_label("insights")
        await asyncio.gather(*[
            gated(lambda: self.router._get_strategic_insights())
            for _ in range(N_RUNS)
        ])

        # HOOKS — already async; semaphore protects concurrency.
        self.cap_hunter.set_label("hooks")
        await asyncio.gather(*[
            gated(lambda: self.hunter.generate_outreach_hooks_async(
                HOOKS_PAIN_POINTS, HOOKS_BUSINESS_NAME))
            for _ in range(N_RUNS)
        ])

        # ENRICH — also async.
        self.cap_hunter.set_label("enrich")
        await asyncio.gather(*[
            gated(lambda: self.hunter.enrich_business_data_async(
                ENRICH_PAGE_TEXT, ENRICH_BUSINESS_NAME))
            for _ in range(N_RUNS)
        ])

    async def asyncTearDown(self):
        self.cap_mapper.restore()
        self.cap_hunter.restore()
        self.cap_router.restore()
        self.sb_patcher.stop()
        self.env_patcher.stop()
        if self.hunter._session and not self.hunter._session.closed:
            await self.hunter.close()

    def _assert_endpoint(self, label: str, raws: list[str], validator):
        self.assertEqual(
            len(raws), N_RUNS,
            f"{label}: captured {len(raws)} responses, expected {N_RUNS}"
        )
        fail_count, samples = _aggregate_failures(label, raws, validator)
        if fail_count > 0:
            pass_rate = (N_RUNS - fail_count) / N_RUNS * 100
            self.fail(
                f"{label}: {fail_count}/{N_RUNS} failures (pass rate {pass_rate:.1f}%). "
                f"Fix: add response_mime_type='application/json' + response_schema "
                f"to this call (Gemini types.GenerateContentConfig).\n"
                + "\n".join(samples)
            )

    def test_mapper_100_percent_compliant(self):
        raws = self.cap_mapper.by_label("mapper")
        self._assert_endpoint(
            "mapper", raws,
            lambda parsed: _validate_mapper(parsed, MAPPER_HEADERS),
        )

    def test_insights_100_percent_compliant(self):
        raws = self.cap_router.by_label("insights")
        self._assert_endpoint("insights", raws, _validate_insights)

    def test_hooks_100_percent_compliant(self):
        raws = self.cap_hunter.by_label("hooks")
        self._assert_endpoint("hooks", raws, _validate_hooks)

    def test_enrich_100_percent_compliant(self):
        raws = self.cap_hunter.by_label("enrich")
        self._assert_endpoint("enrich", raws, _validate_enrich)


if __name__ == "__main__":
    unittest.main()
