"""
Hallucination test for /draft-outreach.

Premise: when source data is DELIBERATELY SPARSE (name + website only,
empty audit_results), does the generator invent facts to satisfy the
prompt's "reference ONE specific concrete issue" requirement?

5 fixture leads with zero business_details, zero pain_points, zero audit
findings. Two-layer detection:

  1. Deterministic regex sweep — catches the easy hallucinations:
     - specific-number claims ("12 employees", "$2M revenue", "10 years")
     - named tech stacks (react/wordpress/shopify/stripe/etc.) that
       cannot have come from the empty audit_results
  2. Gemini judge per draft — enumerates every substantive factual
     claim about the recipient and marks each TRUE (verifiable from
     source) or FALSE (invented). ANY false claim fails the test.

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
OPERATOR_NAME_FIXTURE = "Test Operator"

# Number-claim regex. Any of these patterns is a hallucination because the
# source data carries no numeric facts about the lead beyond seo_score=N/A.
NUMBER_CLAIM_PATTERNS = [
    r"\b\d+\s*(?:employees?|staff|team\s*members?|people|developers?|engineers?)\b",
    r"\b\d+\s*(?:years?|months?|decades?)\s+(?:in\s+business|of\s+experience|old|ago)\b",
    r"\b\d+\s*(?:customers?|clients?|users?|leads?|visitors?|subscribers?)\b",
    r"\$\s*\d[\d,\.]*\s*(?:k|m|million|thousand|mm|b)?\b",
    r"\b\d+\s*(?:million|thousand|hundred)\s+(?:dollars?|euros?|usd|eur|kn|bam)\b",
    r"\b(?:revenue|arr|mrr|valuation)\s+of\s+\$?\d",
]

# Tech tokens the writer should never name. lead_data given to the prompt has
# no tech_stack field at all; any mention is fabricated.
TECH_TOKENS = [
    "react",
    "vue",
    "angular",
    "svelte",
    "next.js",
    "nextjs",
    "wordpress",
    "shopify",
    "squarespace",
    "webflow",
    "wix",
    "ghost",
    "laravel",
    "django",
    "rails",
    "ruby on rails",
    "node.js",
    "nodejs",
    "stripe",
    "hubspot",
    "salesforce",
    "mailchimp",
    "intercom",
    "zendesk",
    "google analytics",
    "ga4",
    "segment",
    "mixpanel",
    "amplitude",
    "aws",
    "gcp",
    "azure",
    "cloudflare",
    "vercel",
    "netlify",
    "mongodb",
    "postgresql",
    "mysql",
    "redis",
    "elasticsearch",
    "tailwind",
    "bootstrap",
    "material ui",
]

# Title claims that imply named-person knowledge the writer doesn't have.
# leadership_team is NOT in the lead_data dict for outreach drafts.
PERSON_CLAIM_PATTERNS = [
    r"\b(?:your|the)\s+(?:ceo|founder|cto|coo|cmo|cfo|owner|president|vp|chief\s+\w+)\b",
    r"\bunder\s+(?:your|the)\s+(?:leadership|direction|guidance)\b",
]


def _sparse_leads() -> list[dict]:
    """5 leads with ONLY name + website. Everything else absent or empty."""
    return [
        {
            "unique_key": "h1",
            "name": "Marcus Vidović",
            "website": "https://example-h1.test",
            "email": "",
            "audit_results": {},
        },
        {
            "unique_key": "h2",
            "name": "Adisa Begović",
            "website": "https://example-h2.test",
            "email": "",
            "audit_results": {},
        },
        {
            "unique_key": "h3",
            "name": "Robin Tanović",
            "website": "https://example-h3.test",
            "email": "",
            "audit_results": {},
        },
        {
            "unique_key": "h4",
            "name": "Iva Kraljević",
            "website": "https://example-h4.test",
            "email": "",
            "audit_results": {},
        },
        {
            "unique_key": "h5",
            "name": "Senad Hodžić",
            "website": "https://example-h5.test",
            "email": "",
            "audit_results": {},
        },
    ]


def _source_view_for_judge(lead: dict) -> dict:
    """
    Reconstruct the EXACT dict the email generator saw. Keep this in sync
    with _generate_outreach_draft (agentic_router.py:389) so the judge sees
    what the writer saw — no more, no less.
    """
    audit = lead.get("audit_results", {}) or {}
    return {
        "contact_name": lead.get("name", "there"),
        "company": lead.get("company_name", "your company"),
        "website": lead.get("website", ""),
        "seo_score": audit.get("score", "N/A"),
        "missing_title": audit.get("missing_title", False),
        "missing_description": audit.get("missing_description", False),
        "missing_h1": audit.get("no_h1", False),
        "ssl_valid": audit.get("ssl_valid", "N/A"),
        "pain_points": audit.get("pain_points", "No specific pain points identified."),
    }


def _matches(text: str, patterns: list[str]) -> list[str]:
    """Return the literal matches (not just the pattern)."""
    hits: list[str] = []
    for p in patterns:
        for m in re.finditer(p, text, flags=re.IGNORECASE):
            hits.append(m.group(0))
    return hits


def _tech_hits(text: str) -> list[str]:
    lower = text.lower()
    return [t for t in TECH_TOKENS if re.search(rf"\b{re.escape(t)}\b", lower)]


async def _generate_one(router, lead: dict) -> dict:
    return await router._generate_outreach_draft(
        {
            "unique_key": lead["unique_key"],
            "lead_data": lead,
        }
    )


async def _generate_all(router, leads: list[dict]) -> list[dict]:
    return await asyncio.gather(*(_generate_one(router, l) for l in leads))


def _judge_prompt(source: dict, email_body: str, subject: str) -> str:
    return (
        "You are auditing a cold outreach email for FACTUAL HALLUCINATIONS\n"
        "about the recipient.\n\n"
        "SOURCE DATA (the only facts the writer was given about the recipient):\n"
        f"{json.dumps(source, ensure_ascii=False)}\n\n"
        "EMAIL SUBJECT:\n"
        f"{subject}\n\n"
        "EMAIL BODY:\n"
        f"{email_body}\n\n"
        "Task: List EVERY substantive factual claim the email makes ABOUT\n"
        "THE RECIPIENT or their business. Specifically look for:\n"
        "  - Numbers (employee count, revenue, years in business, customer count)\n"
        "  - Named people or titles (CEO, founder, owner) attributed to the recipient\n"
        "  - Specific tools/technology the recipient is said to use\n"
        "  - Geographic claims beyond the bare website URL\n"
        "  - Specific business activities, offerings, or history\n\n"
        "EXCLUDE from your list:\n"
        "  - Generic pleasantries ('hope this finds you well')\n"
        "  - Statements about the sender ('I help businesses with SEO')\n"
        "  - The literal mail-merge placeholder {{first_name}}\n"
        "  - Generic web-best-practices commentary not attributed to the recipient\n"
        "  - The fact that the recipient HAS a website (the URL is in source)\n\n"
        "For each substantive claim, set verifiable=true ONLY if it can be\n"
        "directly checked against the SOURCE DATA above. Otherwise false.\n"
        "Be strict: 'noticed a few quick wins on your site' WITHOUT specifics\n"
        "is vague (not a factual claim — skip). But 'your homepage loads\n"
        "slowly' IS a claim (set verifiable=false because load time is not\n"
        "in source).\n\n"
        "Return ONLY this JSON object — no prose, no fences:\n"
        '{"claims":[{"claim":"<verbatim or paraphrase>","verifiable":<bool>,'
        '"source_field":"<field name or null>","reason":"<one short sentence>"}]}'
    )


def _parse_judge(raw: str) -> list[dict]:
    text = raw.strip()
    fence = re.match(r"^```(?:json)?\s*\n(.*?)\n```$", text, flags=re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    data = json.loads(text)
    return data.get("claims", [])


async def _judge_one(router, lead: dict, draft: dict) -> tuple[str, list[dict]]:
    """Returns (unique_key, claims_list). Caller filters for verifiable=False."""
    from google.genai import types as genai_types

    source = _source_view_for_judge(lead)
    prompt = _judge_prompt(
        source=source,
        email_body=draft.get("draft", ""),
        subject=draft.get("subject", ""),
    )
    resp = await asyncio.to_thread(
        router.client.models.generate_content,
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
        claims = _parse_judge(raw)
    except Exception:
        # Surface raw payload at the test layer rather than swallowing.
        return lead["unique_key"], [
            {
                "claim": f"<JUDGE_PARSE_ERROR>: {raw[:400]}",
                "verifiable": False,
                "source_field": None,
                "reason": "judge returned non-JSON",
            }
        ]
    return lead["unique_key"], claims


@pytest.mark.live
@unittest.skipUnless(GEMINI_KEY, "Requires GEMINI_API_KEY for live Gemini calls")
class TestOutreachHallucination(unittest.IsolatedAsyncioTestCase):
    """Sparse-input hallucination guard for /draft-outreach."""

    async def asyncSetUp(self):
        self.env_patcher = patch.dict(
            os.environ,
            {
                "GEMINI_API_KEY": GEMINI_KEY or "",
                "OPERATOR_NAME": OPERATOR_NAME_FIXTURE,
            },
        )
        self.env_patcher.start()
        self.sb_patcher = patch("src.core.agentic_router.SupabaseHelper")
        self.sb_patcher.start()

        from src.core.agentic_router import AgenticRouter

        self.router = AgenticRouter()
        self.assertIsNotNone(self.router.client, "Gemini client must initialize")

        self.leads = _sparse_leads()
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
                failures.append(f"{g['lead']['unique_key']}: empty body")
        self.assertFalse(failures, "Generator errors:\n" + "\n".join(failures))

    def test_no_specific_number_claims(self):
        """No employee/year/customer/revenue numbers — none of these are in source."""
        failures = []
        for g in self.graded:
            body = g["draft"].get("draft", "")
            hits = _matches(body, NUMBER_CLAIM_PATTERNS)
            if hits:
                failures.append(f"{g['lead']['unique_key']}: invented numbers {hits}")
        self.assertFalse(failures, "Fabricated numeric claims:\n" + "\n".join(failures))

    def test_no_named_titles(self):
        """No 'your CEO', 'your founder', etc. — leadership_team is not in source."""
        failures = []
        for g in self.graded:
            body = g["draft"].get("draft", "")
            hits = _matches(body, PERSON_CLAIM_PATTERNS)
            if hits:
                failures.append(
                    f"{g['lead']['unique_key']}: invented title claim {hits}"
                )
        self.assertFalse(
            failures, "Fabricated leadership claims:\n" + "\n".join(failures)
        )

    def test_no_tech_stack_mentions(self):
        """No specific tools/tech — audit_results is empty, no tech_stack exposed."""
        failures = []
        for g in self.graded:
            body = g["draft"].get("draft", "")
            hits = _tech_hits(body)
            if hits:
                failures.append(f"{g['lead']['unique_key']}: invented tech {hits}")
        self.assertFalse(
            failures, "Fabricated tech-stack mentions:\n" + "\n".join(failures)
        )

    async def test_judge_finds_no_false_claims(self):
        """
        Per-draft Gemini judge enumerates factual claims and marks each
        TRUE/FALSE against the exact source dict shown to the writer.
        ANY claim with verifiable=False fails this test.
        """
        results = await asyncio.gather(
            *(_judge_one(self.router, g["lead"], g["draft"]) for g in self.graded)
        )

        all_false: list[str] = []
        for unique_key, claims in results:
            for c in claims:
                if not c.get("verifiable", False):
                    claim_text = c.get("claim", "<no claim text>")
                    reason = c.get("reason", "")
                    all_false.append(f"  [{unique_key}] {claim_text}  ({reason})")

        self.assertFalse(
            all_false,
            "Judge flagged unverifiable (invented) claims:\n" + "\n".join(all_false),
        )


if __name__ == "__main__":
    unittest.main()
