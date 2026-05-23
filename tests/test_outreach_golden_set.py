"""
Golden-set quality test for the /draft-outreach generator.

What this covers:
- 10 representative leads with known business_details + audit findings.
- Drives the real AgenticRouter._generate_outreach_draft (live Gemini call).
- Per-draft hard assertions: personalization, audit-finding reference,
  word count, no AI disclaimers, no leftover placeholders.
- Gemini-as-judge second call: rates each draft 1-10 on personalization;
  the average across 10 must be >= 7.5.

Live test — requires GEMINI_API_KEY. Skipped otherwise. Supabase is mocked.
"""
import asyncio
import json
import os
import re
import sys
import unittest
from typing import Any
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

GEMINI_KEY = os.getenv("GEMINI_API_KEY")
OPERATOR_NAME_FIXTURE = "Test Operator"

# Fixed signal — substring search for placeholders. {{first_name}} is the
# operator's literal mail-merge token (prompt requires it), so it is NOT
# treated as a placeholder leak.
DISCLAIMER_PATTERNS = [
    r"\bas an ai\b",
    r"\bi am an ai\b",
    r"\bi'm an ai\b",
    r"\blanguage model\b",
    r"\bas a language model\b",
]
PLACEHOLDER_PATTERNS = [
    r"\[your name\]",
    r"\[company( name)?\]",
    r"\[name\]",
    r"\[first[_ ]?name\]",
    r"\[website\]",
    r"\[insert[^\]]*\]",
]


def _golden_leads() -> list[dict]:
    """
    10 fixtures spanning the audit-finding matrix: SSL, H1, title, description,
    low score, and free-text pain_points. Each has known business_details so
    the judge has substance to rate against.
    """
    return [
        {
            "unique_key": "g1",
            "name": "Sarah Bennett",
            "company_name": "Acme Dental Clinic",
            "website": "https://acmedental.example",
            "email": "sarah@acmedental.example",
            "business_details": "Family dentistry practice in Mostar, BiH. Two dentists. Offers cleanings, fillings, whitening, and braces consultations.",
            "audit_results": {
                "score": 38,
                "missing_title": False,
                "missing_description": True,
                "no_h1": True,
                "ssl_valid": True,
                "pain_points": "No meta description on homepage; missing H1 heading.",
            },
            "expected_keywords": ["description", "meta", "h1", "heading", "header"],
        },
        {
            "unique_key": "g2",
            "name": "Marko Petrović",
            "company_name": "BlueWave Plumbing",
            "website": "http://bluewaveplumbing.example",
            "email": "marko@bluewaveplumbing.example",
            "business_details": "24/7 emergency plumbing service for residential and small commercial properties in Sarajevo.",
            "audit_results": {
                "score": 22,
                "missing_title": False,
                "missing_description": False,
                "no_h1": False,
                "ssl_valid": False,
                "pain_points": "Site served over HTTP — no SSL certificate. Visitors see a 'Not secure' warning.",
            },
            "expected_keywords": ["ssl", "https", "secure", "certificate", "not secure"],
        },
        {
            "unique_key": "g3",
            "name": "Lana Hodžić",
            "company_name": "Studio Vesta Architects",
            "website": "https://studiovesta.example",
            "email": "lana@studiovesta.example",
            "business_details": "Boutique architecture studio focused on adaptive reuse of stone houses on the Herzegovina coast.",
            "audit_results": {
                "score": 41,
                "missing_title": True,
                "missing_description": False,
                "no_h1": False,
                "ssl_valid": True,
                "pain_points": "Homepage has no <title> tag — appears as 'untitled' in search results.",
            },
            "expected_keywords": ["title", "untitled", "search result"],
        },
        {
            "unique_key": "g4",
            "name": "Emir Karić",
            "company_name": "NorthStar Logistics",
            "website": "https://northstarlog.example",
            "email": "emir@northstarlog.example",
            "business_details": "Regional freight forwarding company serving the Western Balkans. Specializes in cross-border customs handling.",
            "audit_results": {
                "score": 19,
                "missing_title": True,
                "missing_description": True,
                "no_h1": True,
                "ssl_valid": False,
                "pain_points": "Multiple critical SEO issues: no title, no meta description, no H1, no SSL.",
            },
            "expected_keywords": ["ssl", "https", "title", "description", "h1", "heading", "seo", "score"],
        },
        {
            "unique_key": "g5",
            "name": "Ivana Marić",
            "company_name": "Petit Café Mostar",
            "website": "https://petitcafe.example",
            "email": "ivana@petitcafe.example",
            "business_details": "Independent specialty coffee shop in Old Town Mostar. Roasts its own beans on-site.",
            "audit_results": {
                "score": 47,
                "missing_title": False,
                "missing_description": False,
                "no_h1": True,
                "ssl_valid": True,
                "pain_points": "No H1 on homepage — search engines can't identify the main page topic.",
            },
            "expected_keywords": ["h1", "heading", "header", "topic"],
        },
        {
            "unique_key": "g6",
            "name": "Tarik Selimović",
            "company_name": "Vrelo Software",
            "website": "https://vrelosoft.example",
            "email": "tarik@vrelosoft.example",
            "business_details": "Custom software development shop building Laravel + Vue.js apps for SMBs in Slovenia and Croatia.",
            "audit_results": {
                "score": 33,
                "missing_title": False,
                "missing_description": True,
                "no_h1": False,
                "ssl_valid": True,
                "pain_points": "No meta description tag — Google generates a snippet from random page text.",
            },
            "expected_keywords": ["description", "meta", "snippet", "google"],
        },
        {
            "unique_key": "g7",
            "name": "Aida Begović",
            "company_name": "Velebit Veterinary Clinic",
            "website": "http://velebitvet.example",
            "email": "aida@velebitvet.example",
            "business_details": "Small-animal veterinary clinic in Banja Luka with emergency services and pet boarding.",
            "audit_results": {
                "score": 28,
                "missing_title": False,
                "missing_description": False,
                "no_h1": False,
                "ssl_valid": False,
                "pain_points": "HTTP only — modern browsers warn visitors before page loads.",
            },
            "expected_keywords": ["ssl", "https", "secure", "certificate", "warn", "browser"],
        },
        {
            "unique_key": "g8",
            "name": "Damir Mehmedović",
            "company_name": "Aurora Fitness Studio",
            "website": "https://aurorafitness.example",
            "email": "damir@aurorafitness.example",
            "business_details": "Boutique fitness studio offering small-group HIIT, mobility, and personal training.",
            "audit_results": {
                "score": 44,
                "missing_title": True,
                "missing_description": True,
                "no_h1": False,
                "ssl_valid": True,
                "pain_points": "Title and description tags both missing — listing in Google looks broken.",
            },
            "expected_keywords": ["title", "description", "meta", "listing", "google"],
        },
        {
            "unique_key": "g9",
            "name": "Selma Tahirović",
            "company_name": "Drina River Tours",
            "website": "https://drinatours.example",
            "email": "selma@drinatours.example",
            "business_details": "Adventure tourism operator running rafting and kayaking trips on the Drina canyon.",
            "audit_results": {
                "score": 31,
                "missing_title": False,
                "missing_description": True,
                "no_h1": True,
                "ssl_valid": True,
                "pain_points": "Homepage missing H1 and meta description — both hurt organic ranking.",
            },
            "expected_keywords": ["h1", "heading", "description", "meta", "ranking", "organic"],
        },
        {
            "unique_key": "g10",
            "name": "Nikolina Jurić",
            "company_name": "Lavanda Skincare",
            "website": "http://lavandaskin.example",
            "email": "nikolina@lavandaskin.example",
            "business_details": "Handmade organic skincare brand using lavender from family farm. Sells via Instagram and small boutique stores.",
            "audit_results": {
                "score": 25,
                "missing_title": True,
                "missing_description": True,
                "no_h1": True,
                "ssl_valid": False,
                "pain_points": "Everything missing: no SSL, no title, no description, no H1. Site looks abandoned.",
            },
            "expected_keywords": ["ssl", "https", "title", "description", "h1", "heading", "secure", "score"],
        },
    ]


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w[\w'-]*\b", text))


def _contains_any(text: str, needles: list[str]) -> bool:
    lower = text.lower()
    return any(n.lower() in lower for n in needles)


def _matches_any(text: str, patterns: list[str]) -> list[str]:
    lower = text.lower()
    return [p for p in patterns if re.search(p, lower)]


async def _generate_one(router, lead: dict) -> dict:
    """Call _generate_outreach_draft with lead_data bypass (no DB round-trip)."""
    return await router._generate_outreach_draft({
        "unique_key": lead["unique_key"],
        "lead_data": lead,
    })


async def _generate_all(router, leads: list[dict]) -> list[dict]:
    return await asyncio.gather(*(_generate_one(router, l) for l in leads))


def _build_judge_prompt(graded: list[dict]) -> str:
    """
    Bundle all 10 drafts + lead context into one judge call. Returns strict JSON
    so we can parse scores deterministically.
    """
    items = []
    for i, g in enumerate(graded, start=1):
        items.append({
            "id": i,
            "company_name": g["lead"]["company_name"],
            "business_details": g["lead"]["business_details"],
            "audit_pain_points": g["lead"]["audit_results"]["pain_points"],
            "draft_subject": g["draft"].get("subject", ""),
            "draft_body": g["draft"].get("draft", ""),
        })
    return (
        "You are grading cold outreach emails for personalization quality.\n"
        "For each draft, rate 1-10 (10 = highly personalized, references the\n"
        "specific business and a concrete audit finding; 1 = generic spam).\n"
        "Penalize: generic language, missing audit reference, no business\n"
        "context. Reward: specific finding called out, business type woven in,\n"
        "natural human tone.\n\n"
        "DATA:\n"
        f"{json.dumps(items, ensure_ascii=False)}\n\n"
        "Return ONLY a JSON object of the exact shape:\n"
        '{"scores":[{"id":1,"score":<int 1-10>,"reason":"<short>"}, ...]}\n'
        "No prose, no markdown fences."
    )


def _parse_judge_scores(raw: str) -> list[int]:
    """Best-effort extract of the scores array from Gemini's JSON output."""
    text = raw.strip()
    # Strip ```json fences if the model adds them despite instructions
    fence = re.match(r"^```(?:json)?\s*\n(.*?)\n```$", text, flags=re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    data = json.loads(text)
    return [int(s["score"]) for s in data["scores"]]


@unittest.skipUnless(GEMINI_KEY, "Requires GEMINI_API_KEY for live Gemini calls")
class TestOutreachGoldenSet(unittest.IsolatedAsyncioTestCase):
    """Live golden-set quality bar for /draft-outreach."""

    async def asyncSetUp(self):
        self.env_patcher = patch.dict(os.environ, {
            "OPERATOR_NAME": OPERATOR_NAME_FIXTURE,
            "GEMINI_API_KEY": GEMINI_KEY or "",
        })
        self.env_patcher.start()
        self.supabase_patcher = patch("src.core.agentic_router.SupabaseHelper")
        self.supabase_patcher.start()

        from src.core.agentic_router import AgenticRouter
        self.router = AgenticRouter()
        # AgenticRouter caches GEMINI_API_KEY in __init__; sanity-check the
        # client wired up so failures here scream loud, not silent skip.
        self.assertIsNotNone(self.router.client, "Gemini client must initialize for live golden-set")

        self.leads = _golden_leads()
        drafts = await _generate_all(self.router, self.leads)
        self.graded: list[dict[str, Any]] = [
            {"lead": l, "draft": d} for l, d in zip(self.leads, drafts)
        ]

    async def asyncTearDown(self):
        self.supabase_patcher.stop()
        self.env_patcher.stop()

    def test_no_generator_errors(self):
        failures = []
        for g in self.graded:
            d = g["draft"]
            if "error" in d:
                failures.append(f"{g['lead']['unique_key']}: {d['error']}")
            elif not (d.get("draft") or "").strip():
                failures.append(f"{g['lead']['unique_key']}: empty draft body")
        self.assertFalse(failures, "Generator returned errors / empty bodies:\n" + "\n".join(failures))

    def test_each_draft_personalized(self):
        """Body must reference first_name OR company_name."""
        failures = []
        for g in self.graded:
            body = g["draft"].get("draft", "")
            lead = g["lead"]
            first_name = lead["name"].split()[0]
            if not _contains_any(body, [first_name, lead["company_name"]]):
                failures.append(
                    f"{lead['unique_key']}: neither '{first_name}' nor "
                    f"'{lead['company_name']}' present in body"
                )
        self.assertFalse(failures, "Personalization missing:\n" + "\n".join(failures))

    def test_each_draft_references_audit_finding(self):
        """Body must mention at least one expected audit-finding keyword."""
        failures = []
        for g in self.graded:
            body = g["draft"].get("draft", "")
            lead = g["lead"]
            if not _contains_any(body, lead["expected_keywords"]):
                failures.append(
                    f"{lead['unique_key']}: no audit keyword from "
                    f"{lead['expected_keywords']} found in body"
                )
        self.assertFalse(failures, "Audit reference missing:\n" + "\n".join(failures))

    def test_word_count_within_band(self):
        """80-200 words. The generator prompt caps at 150, so the upper bound
        is slack; the lower bound is the real quality gate."""
        failures = []
        for g in self.graded:
            body = g["draft"].get("draft", "")
            wc = _word_count(body)
            if wc < 80 or wc > 200:
                failures.append(f"{g['lead']['unique_key']}: word_count={wc} (need 80-200)")
        self.assertFalse(failures, "Word count out of band:\n" + "\n".join(failures))

    def test_no_ai_disclaimers(self):
        failures = []
        for g in self.graded:
            body = g["draft"].get("draft", "")
            hits = _matches_any(body, DISCLAIMER_PATTERNS)
            if hits:
                failures.append(f"{g['lead']['unique_key']}: disclaimers matched {hits}")
        self.assertFalse(failures, "AI disclaimer leakage:\n" + "\n".join(failures))

    def test_no_placeholders(self):
        """No leftover bracket placeholders. {{first_name}} is the operator's
        intentional mail-merge token and is allowed."""
        failures = []
        for g in self.graded:
            body = g["draft"].get("draft", "")
            hits = _matches_any(body, PLACEHOLDER_PATTERNS)
            if hits:
                failures.append(f"{g['lead']['unique_key']}: placeholders matched {hits}")
        self.assertFalse(failures, "Placeholder leakage:\n" + "\n".join(failures))

    def test_judge_average_at_least_7_5(self):
        """Second Gemini call rates each draft 1-10. Average must be >= 7.5."""
        prompt = _build_judge_prompt(self.graded)
        from google.genai import types as genai_types
        resp = self.router.client.models.generate_content(
            model="gemini-flash-latest",
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                system_instruction=(
                    "You are a strict grader. Output ONLY the requested JSON object."
                ),
                response_mime_type="application/json",
            ),
        )
        raw = (resp.text or "").strip()
        try:
            scores = _parse_judge_scores(raw)
        except Exception as e:
            self.fail(f"Judge JSON parse failed: {e}\nRaw: {raw[:500]}")

        self.assertEqual(
            len(scores), len(self.graded),
            f"Judge returned {len(scores)} scores for {len(self.graded)} drafts"
        )
        for s in scores:
            self.assertGreaterEqual(s, 1)
            self.assertLessEqual(s, 10)
        avg = sum(scores) / len(scores)
        # Surface per-draft scores in the failure message so a regression is debuggable
        breakdown = ", ".join(
            f"{g['lead']['unique_key']}={s}" for g, s in zip(self.graded, scores)
        )
        self.assertGreaterEqual(
            avg, 7.5,
            f"Judge average {avg:.2f} below 7.5 threshold. Per-draft: {breakdown}"
        )


if __name__ == "__main__":
    unittest.main()
