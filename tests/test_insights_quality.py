"""
Quality bar for /insights (AgenticRouter._get_strategic_insights).

50-lead fixture spanning audit_status mix, score range, and lead_source
distribution. Calls /insights 5 times and asserts:

  1. Output structure matches the documented schema.
  2. Every integer mentioned in the analysis maps to a real ground-truth
     count or percentage — no invented numbers.
  3. At least 2 verb-led actionable recommendations per call.
  4. The dominant data-fact (most common audit_status or lead_source) is
     mentioned somewhere in the output.
  5. Gemini-as-judge: "Does this analysis match the ground truth?"
     Rated 1-10, averaged across the 5 runs. Threshold >= 8.

Ground truth is computed in this file (pure Python over the same fixture
data fed to the mocked Supabase). The brief mentions postgres-mcp for
ground truth — that's an agent-time tool, not a CI runtime, so we
duplicate the aggregation here. Trade-off: one extra Counter; gain: the
test runs offline without MCP.

Important: _get_strategic_insights only SELECTs
`name,company_name,audit_status,seo_score,lead_source` (agentic_router.py:517).
The model does NOT see the `segment` column. So "dominant segment" in the
brief is interpreted operationally as "most common bucket in the data the
model actually sees" — audit_status or lead_source.

Live test — requires GEMINI_API_KEY. Skipped otherwise.
"""
import asyncio
import json
import os
import re
import sys
import unittest
import pytest
from collections import Counter
from typing import Any
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

GEMINI_KEY = os.getenv("GEMINI_API_KEY")
N_RUNS = 5
JUDGE_THRESHOLD = 8.0
MIN_ACTIONABLE = 2

# Verbs we accept as action-led recommendation openers. Lowercase match,
# first-token-of-insight. Mirrors typical CRM-action vocabulary.
ACTION_VERBS = {
    "prioritize", "prioritise",
    "audit", "review", "investigate", "check", "verify", "validate",
    "target", "focus", "contact", "outreach", "engage", "reach",
    "remove", "delete", "archive", "filter", "exclude",
    "enrich", "schedule", "draft", "send", "follow", "respond",
    "fix", "address", "resolve", "update", "improve", "optimize",
    "segment", "tag", "label", "categorise", "categorize",
    "consider", "use", "leverage", "build", "create", "expand",
}


def _fixture_leads() -> list[dict]:
    """
    50 leads designed so the ground-truth distribution is unambiguous:
      audit_status: Completed=30, Failed=10, Pending=10
      lead_source:  google_maps=30, csv=15, facebook=5
      seo_score:    spread 10..90, mean ~50
    """
    leads = []

    # 30 Completed leads
    for i in range(30):
        leads.append({
            "name": f"Completed Co {i + 1}",
            "company_name": f"Completed Co {i + 1}",
            "audit_status": "Completed",
            "seo_score": 30 + (i * 2) % 60,  # spreads 30-88
            "lead_source": "google_maps" if i < 20 else ("csv" if i < 27 else "facebook"),
        })

    # 10 Failed leads — low seo_scores
    for i in range(10):
        leads.append({
            "name": f"Failed Biz {i + 1}",
            "company_name": f"Failed Biz {i + 1}",
            "audit_status": "Failed",
            "seo_score": 10 + i,  # 10..19
            "lead_source": "google_maps" if i < 6 else ("csv" if i < 9 else "facebook"),
        })

    # 10 Pending leads — no score yet (use 0)
    for i in range(10):
        leads.append({
            "name": f"Pending Inc {i + 1}",
            "company_name": f"Pending Inc {i + 1}",
            "audit_status": "Pending",
            "seo_score": 0,
            "lead_source": "google_maps" if i < 4 else ("csv" if i < 9 else "facebook"),
        })

    assert len(leads) == 50, len(leads)
    return leads


def _ground_truth(leads: list[dict]) -> dict[str, Any]:
    """Pure Python aggregation. Identical math to what the model is asked to do."""
    total = len(leads)
    by_status = Counter(l["audit_status"] for l in leads)
    by_source = Counter(l["lead_source"] for l in leads)
    scores = [l["seo_score"] for l in leads if l["audit_status"] == "Completed"]
    return {
        "total": total,
        "by_audit_status": dict(by_status),
        "by_lead_source": dict(by_source),
        "avg_score_completed": round(sum(scores) / len(scores), 1) if scores else 0,
        "min_score_completed": min(scores) if scores else 0,
        "max_score_completed": max(scores) if scores else 0,
        "low_score_count": sum(1 for s in scores if s < 50),
        "high_score_count": sum(1 for s in scores if s >= 70),
        "dominant_status": by_status.most_common(1)[0][0],
        "dominant_source": by_source.most_common(1)[0][0],
    }


def _allowed_numbers(gt: dict) -> set[int]:
    """
    Build the universe of integers the analysis may legitimately mention.
    Anything outside (and > 3) is treated as invented.
    """
    counts = {
        gt["total"],
        *gt["by_audit_status"].values(),
        *gt["by_lead_source"].values(),
        gt["min_score_completed"], gt["max_score_completed"],
        gt["low_score_count"], gt["high_score_count"],
        int(gt["avg_score_completed"]),
        round(gt["avg_score_completed"]),
        100,  # 100% of pipeline — common phrasing
        0,
    }
    # Percentage approximations for every count, +/- 1 to absorb rounding.
    for c in list(gt["by_audit_status"].values()) + list(gt["by_lead_source"].values()):
        pct = round(c / gt["total"] * 100)
        counts |= {pct, pct - 1, pct + 1}
    # Small integers (1..3) reserved for enumeration ("3 insights")
    counts |= {1, 2, 3}
    return counts


_INT_RE = re.compile(r"-?\d+")


def _integers_in(text: str) -> list[int]:
    return [int(m) for m in _INT_RE.findall(text or "")]


def _is_action_led(insight: str) -> bool:
    """First word (after any leading punctuation/quotes) ∈ ACTION_VERBS."""
    if not insight:
        return False
    m = re.match(r"^[\s\"\*\-—•]*([A-Za-z]+)", insight)
    if not m:
        return False
    return m.group(1).lower() in ACTION_VERBS


# ---- Fake Supabase ---------------------------------------------------------

class _FakeExec:
    def __init__(self, rows): self.data = rows


class _FakeQuery:
    def __init__(self, leads): self._leads = leads
    def select(self, *_a, **_k): return self
    def eq(self, _col, _val): return self
    def limit(self, n): return _Limited(self._leads[:n])
    def execute(self): return _FakeExec(self._leads)


class _Limited:
    def __init__(self, rows): self._rows = rows
    def execute(self): return _FakeExec(self._rows)


class _FakeSB:
    def __init__(self, leads): self._leads = leads
    def table(self, _name): return _FakeQuery(self._leads)


# ---- Judge -----------------------------------------------------------------

def _judge_prompt(ground_truth: dict, output: dict) -> str:
    return (
        "You are auditing a strategic-insights JSON output for accuracy "
        "against the ground-truth aggregation it was supposed to summarise.\n\n"
        "GROUND TRUTH (computed deterministically from the lead DB):\n"
        f"{json.dumps(ground_truth, indent=2)}\n\n"
        "INSIGHTS OUTPUT under review:\n"
        f"{json.dumps(output, indent=2)}\n\n"
        "Question: does this output accurately reflect the ground truth?\n"
        "Penalize: invented numbers, contradicted distributions, missing the\n"
        "dominant status/source, recommendations unsupported by the data.\n"
        "Reward: correctly named dominants, accurate counts/ratios, concrete\n"
        "next steps tied to the actual distribution.\n\n"
        "Return ONLY this JSON (no prose, no fences):\n"
        '{"score": <int 1-10>, "reason": "<one short sentence>"}'
    )


def _parse_judge(raw: str) -> tuple[int, str]:
    text = (raw or "").strip()
    fence = re.match(r"^```(?:json)?\s*\n(.*?)\n```\s*$", text, flags=re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    data = json.loads(text)
    return int(data["score"]), str(data.get("reason", ""))


# ---- Test class ------------------------------------------------------------

@pytest.mark.live
@unittest.skipUnless(GEMINI_KEY, "Requires GEMINI_API_KEY for live Gemini calls")
class TestInsightsQuality(unittest.IsolatedAsyncioTestCase):
    """50-lead seeded DB, 5 /insights runs, ground-truth judge."""

    async def asyncSetUp(self):
        self.env_patcher = patch.dict(os.environ, {
            "GEMINI_API_KEY": GEMINI_KEY or "",
        })
        self.env_patcher.start()

        self.leads = _fixture_leads()
        self.gt = _ground_truth(self.leads)
        self.allowed = _allowed_numbers(self.gt)

        self.sb_patcher = patch("src.core.agentic_router.SupabaseHelper")
        sb_mock = self.sb_patcher.start()
        sb_mock.return_value.client = _FakeSB(self.leads)

        from src.core.agentic_router import AgenticRouter
        self.router = AgenticRouter()
        self.assertIsNotNone(self.router.client, "Gemini client must initialize")

        # 5 insights calls in parallel (the call is sync inside; await it
        # directly since the method is `async def`).
        sem = asyncio.Semaphore(5)

        async def _one():
            async with sem:
                return await self.router._get_strategic_insights()

        self.outputs: list[dict] = await asyncio.gather(*(_one() for _ in range(N_RUNS)))

    async def asyncTearDown(self):
        self.sb_patcher.stop()
        self.env_patcher.stop()

    # ---- Helpers used by multiple tests ----

    def _texts_for(self, output: dict) -> str:
        """Flatten summary + insights + top_priorities into one searchable blob."""
        parts: list[str] = []
        if isinstance(output.get("summary"), str):
            parts.append(output["summary"])
        for ins in output.get("insights") or []:
            if isinstance(ins, str):
                parts.append(ins)
        for tp in output.get("top_priorities") or []:
            if isinstance(tp, dict):
                for v in tp.values():
                    if isinstance(v, str):
                        parts.append(v)
        return " ".join(parts)

    # ---- Tests ----

    def test_no_fallback_returned(self):
        """The fallback shape (`summary == "System analysis completed."`)
        indicates the JSON parser couldn't read Gemini's output. We want
        real analyses, not fallbacks."""
        fallbacks = []
        for i, o in enumerate(self.outputs):
            summary = o.get("summary", "")
            if summary in {
                "System analysis completed.",
                "Insights currently unavailable.",
            }:
                fallbacks.append(f"run {i}: '{summary}'")
        self.assertFalse(
            fallbacks,
            "Insights returned a fallback shape — JSON parse failed upstream:\n"
            + "\n".join(fallbacks)
        )

    def test_output_structure(self):
        failures = []
        for i, o in enumerate(self.outputs):
            if not isinstance(o, dict):
                failures.append(f"run {i}: not a dict ({type(o).__name__})")
                continue
            for key in ("summary", "insights", "top_priorities"):
                if key not in o:
                    failures.append(f"run {i}: missing {key!r}")
            if "insights" in o and not isinstance(o["insights"], list):
                failures.append(f"run {i}: insights is {type(o['insights']).__name__}")
            if "top_priorities" in o and not isinstance(o["top_priorities"], list):
                failures.append(f"run {i}: top_priorities is {type(o['top_priorities']).__name__}")
        self.assertFalse(failures, "Structure violations:\n" + "\n".join(failures))

    def test_no_invented_numbers(self):
        """
        Every integer in the analysis must be in the allowed-numbers set
        derived from ground truth. Small integers (1..3) are reserved for
        enumeration. Off-by-one is absorbed via the percentage band.
        """
        failures = []
        for i, o in enumerate(self.outputs):
            blob = self._texts_for(o)
            for n in _integers_in(blob):
                if n in self.allowed:
                    continue
                if abs(n) <= 3:
                    continue
                # Surface the integer + the local sentence for debug
                sentence = next(
                    (s for s in re.split(r"[.!?]\s+", blob) if str(n) in s),
                    "<no sentence found>",
                )
                failures.append(f"run {i}: invented number {n}  in: {sentence!r}")
        if failures:
            self.fail(
                "Invented integers found (not in ground-truth allowed set):\n"
                + "\n".join(failures)
                + f"\nGround truth: {self.gt}\nAllowed set: {sorted(self.allowed)}"
            )

    def test_at_least_2_actionable_recommendations(self):
        failures = []
        for i, o in enumerate(self.outputs):
            insights = [s for s in (o.get("insights") or []) if isinstance(s, str)]
            tp_reasons = [
                tp.get("reason", "") for tp in (o.get("top_priorities") or [])
                if isinstance(tp, dict)
            ]
            candidates = insights + tp_reasons
            actionable = [c for c in candidates if _is_action_led(c)]
            if len(actionable) < MIN_ACTIONABLE:
                failures.append(
                    f"run {i}: {len(actionable)} action-led recommendations "
                    f"(need >= {MIN_ACTIONABLE}). Candidates: {candidates}"
                )
        self.assertFalse(failures, "Not enough actionable recommendations:\n" + "\n".join(failures))

    def test_dominant_data_fact_mentioned(self):
        """
        At least one of: dominant audit_status ('Completed'), dominant lead_source
        ('google_maps'), or a direct count for either must be referenced. This
        is the operational reading of 'mentions the dominant segment' given the
        model only sees audit_status + lead_source (not segment).
        """
        failures = []
        for i, o in enumerate(self.outputs):
            blob = self._texts_for(o).lower()
            signals = [
                self.gt["dominant_status"].lower(),
                self.gt["dominant_source"].lower(),
                self.gt["dominant_source"].replace("_", " ").lower(),  # 'google maps'
                str(self.gt["by_audit_status"][self.gt["dominant_status"]]),
                str(self.gt["by_lead_source"][self.gt["dominant_source"]]),
            ]
            if not any(s in blob for s in signals):
                failures.append(f"run {i}: no dominant signal mentioned. tried={signals}")
        self.assertFalse(
            failures,
            "Dominant data-fact missing (must mention dominant audit_status or lead_source):\n"
            + "\n".join(failures)
        )

    async def test_judge_average_at_least_8(self):
        """Gemini-as-judge — "Does this match ground truth?" — avg >= 8."""
        from google.genai import types as genai_types

        async def _judge_one(output: dict) -> tuple[int, str]:
            prompt = _judge_prompt(self.gt, output)
            resp = await asyncio.to_thread(
                self.router.client.models.generate_content,
                model="gemini-flash-latest",
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    system_instruction=(
                        "You are a strict factual auditor. Output ONLY the requested JSON."
                    ),
                    response_mime_type="application/json",
                ),
            )
            raw = (resp.text or "").strip()
            try:
                return _parse_judge(raw)
            except Exception as e:
                return -1, f"<PARSE_ERROR>: {e}  raw={raw[:200]!r}"

        results = await asyncio.gather(*(_judge_one(o) for o in self.outputs))

        parse_errors = [(i, r) for i, (s, r) in enumerate(results) if s < 0]
        self.assertFalse(
            parse_errors,
            "Judge JSON parse failures:\n" + "\n".join(str(e) for e in parse_errors)
        )

        scores = [s for s, _ in results]
        for s in scores:
            self.assertGreaterEqual(s, 1)
            self.assertLessEqual(s, 10)
        avg = sum(scores) / len(scores)
        breakdown = ", ".join(f"run{i}={s} ({r[:40]!r})" for i, (s, r) in enumerate(results))
        self.assertGreaterEqual(
            avg, JUDGE_THRESHOLD,
            f"Judge average {avg:.2f} below threshold {JUDGE_THRESHOLD}. "
            f"Per-run: {breakdown}"
        )


if __name__ == "__main__":
    unittest.main()
