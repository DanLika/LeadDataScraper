"""
Golden-set quality test for the /draft-linkedin generator.

Mirrors tests/test_outreach_golden_set.py but adapted to LinkedIn-specific
constraints:
- Hard 300-char limit (LinkedIn invitation message ceiling).
- No subject line (LinkedIn invitations don't have one).
- First line is a warm hook, not "Dear X".
- Must mention the lead's company OR what they offer.
- No URLs (LinkedIn auto-flags messages with links).
- No "as an AI" leakage.
- Gemini-as-judge: "would a human send this?" rated 1-10, avg >= 7.5.

Unlike _generate_outreach_draft, _generate_linkedin_draft has no `lead_data`
bypass — it always fetches from Supabase. We intercept with a fake client
that maps unique_key → lead.

Live test — requires GEMINI_API_KEY. Skipped otherwise.
"""
import asyncio
import json
import os
import re
import sys
import unittest
import pytest
from typing import Any
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

GEMINI_KEY = os.getenv("GEMINI_API_KEY")

LINKEDIN_MIN_CHARS = 50
LINKEDIN_MAX_CHARS = 300

DISCLAIMER_PATTERNS = [
    r"\bas an ai\b",
    r"\bi am an ai\b",
    r"\bi'm an ai\b",
    r"\blanguage model\b",
    r"\bas a language model\b",
]
URL_PATTERNS = [
    r"https?://",
    r"\bwww\.\w",
    # Common TLDs preceded by a word char + dot — catches bare-domain mentions
    # like "acme.com" that LinkedIn's link detector also flags.
    r"\b\w+\.(?:com|net|org|io|co|app|dev|ai|example)\b(?:/|\s|$)",
]
DEAR_OPENERS = (
    "dear ",
    "to whom",
    "hello sir",
    "hello madam",
    "greetings",
)


def _golden_leads() -> list[dict]:
    """
    Same 10 identities as the outreach golden set, repopulated with the
    fields _generate_linkedin_draft actually reads:
    leadership_team, company_name, business_details, target_clients.
    """
    return [
        {
            "unique_key": "g1",
            "name": "Sarah Bennett",
            "leadership_team": "Sarah Bennett",
            "company_name": "Acme Dental Clinic",
            "business_details": "Family dentistry practice in Mostar offering cleanings, fillings, whitening, and braces consultations.",
            "target_clients": "Local families, working professionals, parents booking pediatric checkups.",
            "offering_keywords": ["dental", "dentist", "cleaning", "whitening", "braces", "family practice"],
        },
        {
            "unique_key": "g2",
            "name": "Marko Petrović",
            "leadership_team": "Marko Petrović",
            "company_name": "BlueWave Plumbing",
            "business_details": "24/7 emergency plumbing service for residential and small commercial properties in Sarajevo.",
            "target_clients": "Homeowners, property managers, small business operators.",
            "offering_keywords": ["plumbing", "plumber", "emergency", "residential", "commercial"],
        },
        {
            "unique_key": "g3",
            "name": "Lana Hodžić",
            "leadership_team": "Lana Hodžić",
            "company_name": "Studio Vesta Architects",
            "business_details": "Boutique architecture studio focused on adaptive reuse of stone houses on the Herzegovina coast.",
            "target_clients": "Heritage-property owners, boutique hotel developers.",
            "offering_keywords": ["architecture", "architect", "design", "stone houses", "adaptive reuse", "heritage"],
        },
        {
            "unique_key": "g4",
            "name": "Emir Karić",
            "leadership_team": "Emir Karić",
            "company_name": "NorthStar Logistics",
            "business_details": "Regional freight forwarding company serving the Western Balkans. Specializes in cross-border customs handling.",
            "target_clients": "Mid-size manufacturers, e-commerce sellers shipping cross-border.",
            "offering_keywords": ["freight", "logistics", "shipping", "customs", "cross-border", "forwarding"],
        },
        {
            "unique_key": "g5",
            "name": "Ivana Marić",
            "leadership_team": "Ivana Marić",
            "company_name": "Petit Café Mostar",
            "business_details": "Independent specialty coffee shop in Old Town Mostar that roasts its own beans on-site.",
            "target_clients": "Tourists, local coffee enthusiasts, remote workers.",
            "offering_keywords": ["coffee", "café", "specialty", "roasting", "espresso", "old town"],
        },
        {
            "unique_key": "g6",
            "name": "Tarik Selimović",
            "leadership_team": "Tarik Selimović",
            "company_name": "Vrelo Software",
            "business_details": "Custom software development shop building Laravel + Vue.js apps for SMBs in Slovenia and Croatia.",
            "target_clients": "SMBs needing internal tools, CRM integrations, custom dashboards.",
            "offering_keywords": ["software", "development", "laravel", "vue", "smb", "custom"],
        },
        {
            "unique_key": "g7",
            "name": "Aida Begović",
            "leadership_team": "Aida Begović",
            "company_name": "Velebit Veterinary Clinic",
            "business_details": "Small-animal veterinary clinic in Banja Luka with emergency services and pet boarding.",
            "target_clients": "Pet owners, dog walkers, local breeders.",
            "offering_keywords": ["veterinary", "vet", "animal", "pet", "boarding", "emergency"],
        },
        {
            "unique_key": "g8",
            "name": "Damir Mehmedović",
            "leadership_team": "Damir Mehmedović",
            "company_name": "Aurora Fitness Studio",
            "business_details": "Boutique fitness studio offering small-group HIIT, mobility, and personal training.",
            "target_clients": "Working professionals, weekend athletes, beginners on referral.",
            "offering_keywords": ["fitness", "training", "hiit", "mobility", "personal training", "studio"],
        },
        {
            "unique_key": "g9",
            "name": "Selma Tahirović",
            "leadership_team": "Selma Tahirović",
            "company_name": "Drina River Tours",
            "business_details": "Adventure tourism operator running rafting and kayaking trips on the Drina canyon.",
            "target_clients": "Group travel bookers, corporate retreat planners, weekend adventurers.",
            "offering_keywords": ["rafting", "kayaking", "tour", "adventure", "tourism", "canyon", "drina"],
        },
        {
            "unique_key": "g10",
            "name": "Nikolina Jurić",
            "leadership_team": "Nikolina Jurić",
            "company_name": "Lavanda Skincare",
            "business_details": "Handmade organic skincare brand using lavender from a family farm. Sells via Instagram and boutique stores.",
            "target_clients": "Boutique retailers, eco-conscious consumers, Instagram followers.",
            "offering_keywords": ["skincare", "lavender", "organic", "handmade", "cosmetic", "boutique"],
        },
    ]


# ---- Fake Supabase client ----------------------------------------------------

class _FakeExecResult:
    def __init__(self, rows):
        self.data = rows


class _FakeQuery:
    """Records the eq() filter and returns the matching lead at execute()."""
    def __init__(self, leads_by_key: dict):
        self._lbk = leads_by_key
        self._eq_filter = None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        if col == "unique_key":
            self._eq_filter = val
        return self

    def limit(self, _n):
        return self

    def execute(self):
        if self._eq_filter is not None:
            lead = self._lbk.get(self._eq_filter)
            return _FakeExecResult([lead] if lead else [])
        return _FakeExecResult([])


class _FakeSupabaseClient:
    def __init__(self, leads_by_key: dict):
        self._lbk = leads_by_key

    def table(self, _name):
        return _FakeQuery(self._lbk)


# ---- Helpers -----------------------------------------------------------------

def _contains_any(text: str, needles: list[str]) -> bool:
    lower = text.lower()
    return any(n.lower() in lower for n in needles)


def _matches_any(text: str, patterns: list[str]) -> list[str]:
    lower = text.lower()
    return [p for p in patterns if re.search(p, lower)]


async def _generate_one(router, lead: dict) -> dict:
    return await router._generate_linkedin_draft({"unique_key": lead["unique_key"]})


async def _generate_all(router, leads: list[dict]) -> list[dict]:
    return await asyncio.gather(*(_generate_one(router, l) for l in leads))


def _build_judge_prompt(graded: list[dict]) -> str:
    """Single batched judge call — 'would a human send this?' 1-10."""
    items = []
    for i, g in enumerate(graded, start=1):
        items.append({
            "id": i,
            "person": g["lead"]["leadership_team"],
            "company_name": g["lead"]["company_name"],
            "business_details": g["lead"]["business_details"],
            "target_clients": g["lead"]["target_clients"],
            "draft": g["draft"].get("draft", ""),
        })
    return (
        "You are grading LinkedIn connection-request messages on a single\n"
        "question: 'Would a real human send this message as-is?'\n"
        "Rate each draft 1-10 (10 = sounds like a thoughtful human reaching\n"
        "out; 1 = obvious template / AI sludge). Penalize: generic openers,\n"
        "no specificity, overly formal tone, sales pitch, URLs, robotic\n"
        "phrasing. Reward: warm specific opener, mentions the business by\n"
        "name or domain, sounds conversational, fits LinkedIn's 300-char box.\n\n"
        "DATA:\n"
        f"{json.dumps(items, ensure_ascii=False)}\n\n"
        "Return ONLY a JSON object of the exact shape:\n"
        '{"scores":[{"id":1,"score":<int 1-10>,"reason":"<short>"}, ...]}\n'
        "No prose, no markdown fences."
    )


def _parse_judge_scores(raw: str) -> list[int]:
    text = raw.strip()
    fence = re.match(r"^```(?:json)?\s*\n(.*?)\n```$", text, flags=re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    data = json.loads(text)
    return [int(s["score"]) for s in data["scores"]]


# ---- Test --------------------------------------------------------------------

@pytest.mark.live
@unittest.skipUnless(GEMINI_KEY, "Requires GEMINI_API_KEY for live Gemini calls")
class TestLinkedInGoldenSet(unittest.IsolatedAsyncioTestCase):
    """Live golden-set quality bar for /draft-linkedin."""

    async def asyncSetUp(self):
        self.env_patcher = patch.dict(os.environ, {
            "GEMINI_API_KEY": GEMINI_KEY or "",
        })
        self.env_patcher.start()

        self.leads = _golden_leads()
        leads_by_key = {l["unique_key"]: l for l in self.leads}

        self.sb_patcher = patch("src.core.agentic_router.SupabaseHelper")
        sb_mock = self.sb_patcher.start()
        sb_mock.return_value.client = _FakeSupabaseClient(leads_by_key)

        from src.core.agentic_router import AgenticRouter
        self.router = AgenticRouter()
        self.assertIsNotNone(self.router.client, "Gemini client must initialize")

        drafts = await _generate_all(self.router, self.leads)
        self.graded: list[dict[str, Any]] = [
            {"lead": l, "draft": d} for l, d in zip(self.leads, drafts)
        ]

    async def asyncTearDown(self):
        self.sb_patcher.stop()
        self.env_patcher.stop()

    def test_no_generator_errors(self):
        failures = []
        for g in self.graded:
            d = g["draft"]
            if "error" in d:
                failures.append(f"{g['lead']['unique_key']}: {d['error']}")
            elif not (d.get("draft") or "").strip():
                failures.append(f"{g['lead']['unique_key']}: empty draft")
        self.assertFalse(failures, "Generator errors / empty drafts:\n" + "\n".join(failures))

    def test_character_count_within_band(self):
        """50-300 chars. Upper bound is LinkedIn's hard limit; lower bound is
        the human-content gate (a 12-char 'Hi, let's connect' is too thin)."""
        failures = []
        for g in self.graded:
            body = g["draft"].get("draft", "")
            n = len(body)
            if n < LINKEDIN_MIN_CHARS or n > LINKEDIN_MAX_CHARS:
                failures.append(
                    f"{g['lead']['unique_key']}: chars={n} "
                    f"(need {LINKEDIN_MIN_CHARS}-{LINKEDIN_MAX_CHARS})"
                )
        self.assertFalse(failures, "Char count out of band:\n" + "\n".join(failures))

    def test_no_subject_line(self):
        """LinkedIn invitations have no subject. Reject `Subject:` prefix and
        any explicit `subject` field in the return shape."""
        failures = []
        for g in self.graded:
            d = g["draft"]
            body = d.get("draft", "")
            if re.match(r"^\s*subject\s*:", body, flags=re.IGNORECASE):
                failures.append(f"{g['lead']['unique_key']}: body starts with 'Subject:'")
            if d.get("subject"):
                failures.append(f"{g['lead']['unique_key']}: return has subject={d['subject']!r}")
        self.assertFalse(failures, "Subject-line leakage:\n" + "\n".join(failures))

    def test_first_line_is_hook_not_dear(self):
        """First line is a warm hook, not formal 'Dear X' / 'To whom'."""
        failures = []
        for g in self.graded:
            body = g["draft"].get("draft", "").strip()
            first_line = body.splitlines()[0] if body else ""
            lower = first_line.lower().lstrip()
            if any(lower.startswith(p) for p in DEAR_OPENERS):
                failures.append(f"{g['lead']['unique_key']}: opens with {first_line!r}")
        self.assertFalse(failures, "Formal/cold openers:\n" + "\n".join(failures))

    def test_mentions_company_or_offering(self):
        """Body must mention company_name OR an offering keyword from
        business_details. Generic 'Hi, would love to connect' fails."""
        failures = []
        for g in self.graded:
            lead = g["lead"]
            body = g["draft"].get("draft", "")
            signals = [lead["company_name"]] + lead["offering_keywords"]
            if not _contains_any(body, signals):
                failures.append(
                    f"{lead['unique_key']}: neither company {lead['company_name']!r} "
                    f"nor offering keywords {lead['offering_keywords']} present"
                )
        self.assertFalse(failures, "No company/offering anchor:\n" + "\n".join(failures))

    def test_no_urls(self):
        """LinkedIn flags messages with links. Reject any URL-shaped substring."""
        failures = []
        for g in self.graded:
            body = g["draft"].get("draft", "")
            hits = _matches_any(body, URL_PATTERNS)
            if hits:
                failures.append(f"{g['lead']['unique_key']}: URL patterns matched {hits}")
        self.assertFalse(failures, "URL leakage:\n" + "\n".join(failures))

    def test_no_ai_disclaimers(self):
        failures = []
        for g in self.graded:
            body = g["draft"].get("draft", "")
            hits = _matches_any(body, DISCLAIMER_PATTERNS)
            if hits:
                failures.append(f"{g['lead']['unique_key']}: disclaimers matched {hits}")
        self.assertFalse(failures, "AI disclaimer leakage:\n" + "\n".join(failures))

    def test_judge_average_at_least_7_5(self):
        """Second Gemini call: 'would a human send this?' 1-10, avg >= 7.5."""
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
        breakdown = ", ".join(
            f"{g['lead']['unique_key']}={s}" for g, s in zip(self.graded, scores)
        )
        self.assertGreaterEqual(
            avg, 7.5,
            f"Judge average {avg:.2f} below 7.5. Per-draft: {breakdown}"
        )


if __name__ == "__main__":
    unittest.main()
