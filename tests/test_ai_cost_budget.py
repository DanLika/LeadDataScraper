"""
Token + cost budget enforcement for the full Gemini pipeline.

Pipeline on 20 leads exercises every Gemini-backed step:

  - pain_points  (LeadHunter.analyze_pain_points_async)
  - hooks        (LeadHunter.generate_outreach_hooks_async)
  - enrich       (LeadHunter.enrich_business_data_async)
  - outreach     (AgenticRouter._generate_outreach_draft)
  - linkedin     (AgenticRouter._generate_linkedin_draft)

Discovery is NOT a Gemini call — discovery_engine.py uses Playwright +
Google Maps. SEO audit isn't a Gemini call either — seo_audit.py is HTTP
scraping. Both are intentionally excluded from the budget tracking; the
test docstring documents this so a future reviewer doesn't go hunting for
missing labels.

Budget (per the brief):
  - Total input tokens   < 200,000
  - Total output tokens  <  50,000
  - Single-call input    <=  8,000  (catches prompt bloat per call)
  - Estimated cost       <    $0.50 per 20-lead pipeline

Pricing constants are at the top of the file — keep in sync with
https://ai.google.dev/pricing (gemini-flash-latest as of model release).

Live test — requires GEMINI_API_KEY. Skipped otherwise. Supabase mocked.
"""
import asyncio
import json
import os
import sys
import unittest
from collections import defaultdict
from typing import Callable
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

GEMINI_KEY = os.getenv("GEMINI_API_KEY")
N_LEADS = 20
CONCURRENCY = 8

# --- Pricing (gemini-flash-latest, $/million tokens) ---
# Update when Google adjusts pricing. The budget assertion is denominated
# in dollars so the test catches a pricing-change regression too.
PRICE_INPUT_PER_MTOK = 0.075
PRICE_OUTPUT_PER_MTOK = 0.30

# --- Budgets ---
BUDGET_TOTAL_INPUT = 200_000
BUDGET_TOTAL_OUTPUT = 50_000
BUDGET_SINGLE_CALL_INPUT = 8_000
BUDGET_TOTAL_COST_USD = 0.50


# ---- Usage capture -----------------------------------------------------------

class UsageTracker:
    """
    Wraps client.models.generate_content (sync) and client.aio.models
    .generate_content (async). Each call records (label, input_tokens,
    output_tokens). The current `label` is mutable so a single client can
    cover multiple pipeline stages.
    """
    def __init__(self):
        self.records: list[tuple[str, int, int]] = []
        self._label = "unlabelled"
        self._orig_sync: Callable | None = None
        self._orig_async: Callable | None = None
        self._target_sync = None
        self._target_async = None

    @staticmethod
    def _extract_usage(resp) -> tuple[int, int]:
        um = getattr(resp, "usage_metadata", None)
        if um is None:
            return 0, 0
        return (
            int(getattr(um, "prompt_token_count", 0) or 0),
            int(getattr(um, "candidates_token_count", 0) or 0),
        )

    def install(self, client, *, has_aio: bool):
        self._target_sync = client.models
        self._orig_sync = self._target_sync.generate_content
        outer = self

        def _wrap_sync(*args, **kwargs):
            resp = outer._orig_sync(*args, **kwargs)
            ti, to = outer._extract_usage(resp)
            outer.records.append((outer._label, ti, to))
            return resp
        self._target_sync.generate_content = _wrap_sync

        if has_aio:
            self._target_async = client.aio.models
            self._orig_async = self._target_async.generate_content

            async def _wrap_async(*args, **kwargs):
                resp = await outer._orig_async(*args, **kwargs)
                ti, to = outer._extract_usage(resp)
                outer.records.append((outer._label, ti, to))
                return resp
            self._target_async.generate_content = _wrap_async

    def restore(self):
        if self._target_sync is not None and self._orig_sync is not None:
            self._target_sync.generate_content = self._orig_sync
        if self._target_async is not None and self._orig_async is not None:
            self._target_async.generate_content = self._orig_async

    def set_label(self, label: str):
        self._label = label


# ---- Pipeline fixture --------------------------------------------------------

def _fixture_leads() -> list[dict]:
    """20 leads spanning industries — diversity widens token-distribution coverage."""
    industries = [
        ("Dental Clinic", "Family dentistry, cleanings and braces consultations."),
        ("Plumbing Service", "24/7 emergency plumbing for residential and commercial."),
        ("Architecture Studio", "Boutique design firm focused on adaptive reuse."),
        ("Logistics Co.", "Regional freight forwarding with customs handling."),
        ("Specialty Café", "Independent coffee shop roasting beans on-site."),
        ("Software Studio", "Custom Laravel + Vue apps for SMBs."),
        ("Vet Clinic", "Small-animal veterinary with boarding."),
        ("Fitness Studio", "Boutique HIIT, mobility, and personal training."),
        ("Tour Operator", "Rafting and kayaking expeditions."),
        ("Skincare Brand", "Handmade organic skincare with lavender base."),
    ]
    leads = []
    for i in range(N_LEADS):
        industry, blurb = industries[i % len(industries)]
        leads.append({
            "unique_key": f"budget_{i:02d}",
            "name": f"Test {industry} #{i + 1}",
            "company_name": f"Test {industry} #{i + 1}",
            "website": f"https://lead-{i:02d}.example",
            "email": f"contact{i}@lead-{i:02d}.example",
            "business_details": blurb,
            "leadership_team": f"Owner-Manager {i + 1}",
            "target_clients": "Local SMBs and residential customers.",
            "audit_results": {
                "score": 25 + (i * 3) % 50,
                "no_h1": (i % 3 == 0),
                "missing_description": (i % 4 == 0),
                "ssl_valid": (i % 2 == 0),
                "pain_points": f"Common SEO and tracking gaps observed on site {i}.",
                "tech_flags": {
                    "has_viewport": True,
                    "has_google_analytics": False,
                    "has_facebook_pixel": False,
                    "has_robots_txt": True,
                    "has_sitemap": True,
                },
                "red_flags": [],
                "response_time": 1.5,
                "cms": "WordPress" if i % 2 else None,
            },
            "page_text": (
                f"{industry} #{i + 1} is a small business in our region. {blurb} "
                "Founded by a local owner. Operates from a primary location with "
                "online presence growing year over year. Common services include "
                "consultations, regular maintenance, and seasonal promotions."
            ),
        })
    return leads


# ---- Fake Supabase (LinkedIn draft needs DB; route_instruction lead_index) ---

class _FakeExec:
    def __init__(self, rows): self.data = rows


class _FakeQuery:
    def __init__(self, leads_by_key):
        self._lbk = leads_by_key
        self._eq = None
    def select(self, *_a, **_k): return self
    def eq(self, col, val):
        if col == "unique_key":
            self._eq = val
        return self
    def limit(self, _n): return self
    def execute(self):
        if self._eq is not None:
            lead = self._lbk.get(self._eq)
            return _FakeExec([lead] if lead else [])
        return _FakeExec([])


class _FakeSB:
    def __init__(self, leads_by_key): self._lbk = leads_by_key
    def table(self, _name): return _FakeQuery(self._lbk)


# ---- Test --------------------------------------------------------------------

@unittest.skipUnless(GEMINI_KEY, "Requires GEMINI_API_KEY for live Gemini calls")
class TestAICostBudget(unittest.IsolatedAsyncioTestCase):
    """End-to-end token/cost budget over 20 fixture leads."""

    async def asyncSetUp(self):
        self.env_patcher = patch.dict(os.environ, {
            "GEMINI_API_KEY": GEMINI_KEY or "",
        })
        self.env_patcher.start()

        self.leads = _fixture_leads()
        leads_by_key = {l["unique_key"]: l for l in self.leads}

        self.sb_patcher = patch("src.core.agentic_router.SupabaseHelper")
        sb_mock = self.sb_patcher.start()
        sb_mock.return_value.client = _FakeSB(leads_by_key)

        from src.processors.leadhunter import LeadHunter
        from src.core.agentic_router import AgenticRouter

        self.hunter = LeadHunter()
        self.router = AgenticRouter()
        for name, c in (("hunter", self.hunter.client),
                        ("router", self.router.client)):
            self.assertIsNotNone(c, f"{name} Gemini client must initialize")

        self.tracker_hunter = UsageTracker()
        self.tracker_hunter.install(self.hunter.client, has_aio=True)
        self.tracker_router = UsageTracker()
        self.tracker_router.install(self.router.client, has_aio=False)

        sem = asyncio.Semaphore(CONCURRENCY)

        async def _gated(coro_factory):
            async with sem:
                return await coro_factory()

        # --- pain_points (async) ---
        self.tracker_hunter.set_label("pain_points")
        await asyncio.gather(*[
            _gated(lambda l=l: self.hunter.analyze_pain_points_async(
                l["page_text"], l["name"], l["audit_results"]))
            for l in self.leads
        ])

        # --- hooks (async) ---
        self.tracker_hunter.set_label("hooks")
        await asyncio.gather(*[
            _gated(lambda l=l: self.hunter.generate_outreach_hooks_async(
                l["audit_results"]["pain_points"], l["name"], l["audit_results"]))
            for l in self.leads
        ])

        # --- enrich (async) ---
        self.tracker_hunter.set_label("enrich")
        await asyncio.gather(*[
            _gated(lambda l=l: self.hunter.enrich_business_data_async(
                l["page_text"], l["name"]))
            for l in self.leads
        ])

        # --- outreach (sync inside router; bridge via to_thread for parallelism) ---
        self.tracker_router.set_label("outreach")
        await asyncio.gather(*[
            _gated(lambda l=l: self.router._generate_outreach_draft({
                "unique_key": l["unique_key"], "lead_data": l,
            }))
            for l in self.leads
        ])

        # --- linkedin (sync inside router; fake DB lookup wired in asyncSetUp) ---
        self.tracker_router.set_label("linkedin")
        await asyncio.gather(*[
            _gated(lambda l=l: self.router._generate_linkedin_draft({
                "unique_key": l["unique_key"],
            }))
            for l in self.leads
        ])

        # Combined record list for downstream assertions
        self.records: list[tuple[str, int, int]] = (
            self.tracker_hunter.records + self.tracker_router.records
        )
        self._print_breakdown()

    async def asyncTearDown(self):
        self.tracker_hunter.restore()
        self.tracker_router.restore()
        self.sb_patcher.stop()
        self.env_patcher.stop()
        if self.hunter._session and not self.hunter._session.closed:
            await self.hunter.close()

    # ---- Helpers ----

    def _per_label_totals(self) -> dict[str, dict[str, int]]:
        agg: dict[str, dict[str, int]] = defaultdict(
            lambda: {"calls": 0, "in": 0, "out": 0, "max_in": 0}
        )
        for label, ti, to in self.records:
            row = agg[label]
            row["calls"] += 1
            row["in"] += ti
            row["out"] += to
            row["max_in"] = max(row["max_in"], ti)
        return dict(agg)

    def _totals(self) -> tuple[int, int]:
        return (
            sum(ti for _, ti, _ in self.records),
            sum(to for _, _, to in self.records),
        )

    @staticmethod
    def _cost_usd(input_tok: int, output_tok: int) -> float:
        return (input_tok / 1_000_000) * PRICE_INPUT_PER_MTOK + \
               (output_tok / 1_000_000) * PRICE_OUTPUT_PER_MTOK

    def _print_breakdown(self):
        """Always print — surfaces telemetry on success AND failure."""
        agg = self._per_label_totals()
        total_in, total_out = self._totals()
        cost = self._cost_usd(total_in, total_out)
        lines = [
            "",
            f"[ai_cost_budget] Token usage breakdown — {N_LEADS} leads, "
            f"{len(self.records)} Gemini calls",
            f"{'task':<13} {'calls':>5} {'in_tok':>10} {'out_tok':>10} {'max_in':>8} {'cost_usd':>10}",
            "-" * 60,
        ]
        order = ["pain_points", "hooks", "enrich", "outreach", "linkedin"]
        for label in order:
            row = agg.get(label, {"calls": 0, "in": 0, "out": 0, "max_in": 0})
            row_cost = self._cost_usd(row["in"], row["out"])
            lines.append(
                f"{label:<13} {row['calls']:>5} {row['in']:>10,} {row['out']:>10,} "
                f"{row['max_in']:>8,} ${row_cost:>8.4f}"
            )
        lines.append("-" * 60)
        lines.append(
            f"{'TOTAL':<13} {len(self.records):>5} {total_in:>10,} {total_out:>10,} "
            f"{'':>8} ${cost:>8.4f}"
        )
        lines.append(
            f"Budgets: in<{BUDGET_TOTAL_INPUT:,}  out<{BUDGET_TOTAL_OUTPUT:,}  "
            f"single<{BUDGET_SINGLE_CALL_INPUT:,}  cost<${BUDGET_TOTAL_COST_USD}"
        )
        lines.append("")
        print("\n".join(lines))

    # ---- Assertions ----

    def test_total_input_tokens_under_budget(self):
        total_in, _ = self._totals()
        self.assertLess(
            total_in, BUDGET_TOTAL_INPUT,
            f"Total input tokens {total_in:,} >= budget {BUDGET_TOTAL_INPUT:,}. "
            f"Inspect prompt bloat (likely culprit: large fenced_json payloads)."
        )

    def test_total_output_tokens_under_budget(self):
        _, total_out = self._totals()
        self.assertLess(
            total_out, BUDGET_TOTAL_OUTPUT,
            f"Total output tokens {total_out:,} >= budget {BUDGET_TOTAL_OUTPUT:,}. "
            f"A draft or insights call may be returning much longer text than spec."
        )

    def test_no_single_call_exceeds_input_cap(self):
        """Per-call input ceiling — catches one task with a runaway prompt."""
        violations = [
            (lbl, ti) for lbl, ti, _ in self.records if ti > BUDGET_SINGLE_CALL_INPUT
        ]
        if violations:
            # Group offenders for a cleaner failure message
            agg: dict[str, list[int]] = defaultdict(list)
            for lbl, ti in violations:
                agg[lbl].append(ti)
            lines = [
                f"{lbl}: max={max(v):,}, count_over_cap={len(v)}, samples={sorted(v, reverse=True)[:3]}"
                for lbl, v in agg.items()
            ]
            self.fail(
                f"Single-call input cap ({BUDGET_SINGLE_CALL_INPUT:,} tok) breached:\n"
                + "\n".join(lines)
            )

    def test_estimated_cost_under_50_cents(self):
        total_in, total_out = self._totals()
        cost = self._cost_usd(total_in, total_out)
        self.assertLess(
            cost, BUDGET_TOTAL_COST_USD,
            f"Pipeline cost ${cost:.4f} >= budget ${BUDGET_TOTAL_COST_USD}. "
            f"Input={total_in:,} Output={total_out:,}. Check breakdown above."
        )

    def test_every_pipeline_stage_emitted_calls(self):
        """Guards against silent partial-failure: e.g. enrich returns early and
        never hits Gemini, hiding token usage from this budget check."""
        agg = self._per_label_totals()
        missing = [lbl for lbl in ("pain_points", "hooks", "enrich", "outreach", "linkedin")
                   if agg.get(lbl, {}).get("calls", 0) == 0]
        self.assertFalse(
            missing,
            f"No Gemini calls captured for stages: {missing}. "
            f"Either the stage short-circuited or the tracker missed them."
        )


if __name__ == "__main__":
    unittest.main()
