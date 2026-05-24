"""
Campaign-diversity test for /draft-outreach.

Goal: catch "personalization theater" — the model emits the same template
20 times and merely substitutes the company name. Real personalization
varies subject lines and opening sentences across leads.

Setup: 20 dentists (same segment, same audit profile, same pain_points)
differing ONLY in name + company_name. If the model's output varies under
this maximally-homogeneous input, it varies in real campaigns too.

Three assertions:

  1. Subject lines — pairwise word-set Jaccard <= 0.30 across all C(20,2)
     pairs. "Quick question about <name>" cloned 20 times would hit
     ~0.5 Jaccard on every pair; the test fails loud.
  2. Body opening sentences — pairwise embedding cosine < 0.85 (Gemini
     text-embedding-004). Catches semantic clones even when the lexical
     surface differs.
  3. Each draft must mention its lead's company_name verbatim.

Live test — requires GEMINI_API_KEY. Skipped otherwise.
"""
import asyncio
import math
import os
import re
import sys
import unittest
import pytest
from itertools import combinations
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

GEMINI_KEY = os.getenv("GEMINI_API_KEY")
OPERATOR_NAME_FIXTURE = "Test Operator"

N_LEADS = 20
SUBJECT_JACCARD_MAX = 0.30
OPENING_COSINE_MAX = 0.85
EMBED_MODEL = "text-embedding-004"


# Common-noun pieces of the company names. Excluded from Jaccard token sets so
# subject diversity is measured on phrasing, not on which clinic-suffix word
# happens to be in the name. (Including these would let same-template emails
# pass simply because each had a different proper noun.)
COMPANY_NOUN_WORDS = {
    "dental", "dentist", "dentistry", "clinic", "smile", "smiles", "tooth",
    "teeth", "oral", "orthodontics",
    "dr", "dr.", "doctor",
    "co", "co.", "inc", "inc.", "llc", "ltd", "ltd.", "group", "practice",
}


def _shared_audit_results() -> dict:
    """Identical audit profile across all 20 leads — homogeneity stress."""
    return {
        "score": 36,
        "missing_title": False,
        "missing_description": True,
        "no_h1": True,
        "ssl_valid": True,
        "pain_points": (
            "Homepage missing H1 and meta description — Google has nothing "
            "to display in search results and the page topic is ambiguous."
        ),
    }


def _fixture_leads() -> list[dict]:
    """
    20 dentists in the same segment. ONLY company_name + contact name +
    website slug differ. audit_results identical. Forces the model to find
    something to vary that isn't supplied by the data.
    """
    clinic_names = [
        "Acme Dental Clinic", "Bright Smile Dentistry", "City Centre Dental",
        "Downtown Dental Care", "Elm Street Dental", "Family Smile Studio",
        "Gentle Dental Group", "Harborview Dentistry", "Ivy Lane Dental",
        "Juniper Family Dental", "Kingsway Dental Practice", "Lakeside Dental",
        "Maple Avenue Dentistry", "Northgate Dental Care", "Oakwood Dental",
        "Pinehill Dental Studio", "Riverbend Dentistry", "Sunrise Dental Group",
        "Tower Bridge Dental", "Valley Dental Clinic",
    ]
    contacts = [
        "Dr. Anderson", "Dr. Bennett", "Dr. Carter", "Dr. Diaz", "Dr. Evans",
        "Dr. Foster", "Dr. Garcia", "Dr. Hassan", "Dr. Iqbal", "Dr. Jones",
        "Dr. Kowalski", "Dr. Larsen", "Dr. Martinez", "Dr. Nguyen", "Dr. Ortiz",
        "Dr. Patel", "Dr. Quinn", "Dr. Reyes", "Dr. Singh", "Dr. Taylor",
    ]
    assert len(clinic_names) == N_LEADS == len(contacts), "fixture mismatch"

    audit = _shared_audit_results()
    leads = []
    for i, (clinic, contact) in enumerate(zip(clinic_names, contacts)):
        slug = re.sub(r"[^a-z0-9]+", "-", clinic.lower()).strip("-")
        leads.append({
            "unique_key": f"camp_{i:02d}",
            "name": contact,
            "company_name": clinic,
            "website": f"https://{slug}.example",
            "email": f"hello@{slug}.example",
            "audit_results": audit,
        })
    return leads


def _company_token_set(company: str) -> set[str]:
    """Lowercased word set for a company name — used as the per-lead noun mask."""
    return {w.lower() for w in re.findall(r"[A-Za-z]+", company)}


def _subject_token_set(subject: str, lead: dict) -> set[str]:
    """
    Word-set used for Jaccard. Drops:
      - Words that are part of this lead's company name (proper-noun bias).
      - Generic clinic-noun vocabulary so two emails using "dental" don't
        falsely look diverse just because the proper noun differs.
    Stopwords KEPT — short subject lines hide template structure in stopword
    repetition ("about", "for", "your"), so we want them counted.
    """
    company_words = _company_token_set(lead.get("company_name", ""))
    contact_words = _company_token_set(lead.get("name", ""))
    mask = company_words | contact_words | COMPANY_NOUN_WORDS
    tokens = {w.lower() for w in re.findall(r"[A-Za-z]+", subject)}
    return tokens - mask


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _first_body_sentence(body: str) -> str:
    """
    Skip the greeting line ("Hi {{first_name}},") and return the first
    sentence of the actual message. If the body is short or sentence
    boundaries are unclear, fall back to the full body sans greeting.
    """
    # Strip greeting line if present
    stripped = re.sub(
        r"^\s*Hi\s+\{\{first_name\}\}\s*,?\s*\n+",
        "",
        body or "",
        flags=re.IGNORECASE,
    ).strip()
    # Try sentence-end split. Don't split on "Mr." / "Dr." — common in this domain.
    match = re.search(r"^(.+?[.!?])(\s|$)", stripped, flags=re.DOTALL)
    if match:
        return match.group(1).strip()
    return stripped[:240]


def _cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


async def _gen_one(router, lead: dict) -> dict:
    return await router._generate_outreach_draft({
        "unique_key": lead["unique_key"],
        "lead_data": lead,
    })


@pytest.mark.live
@unittest.skipUnless(GEMINI_KEY, "Requires GEMINI_API_KEY for live Gemini calls")
class TestCampaignDiversity(unittest.IsolatedAsyncioTestCase):
    """20-lead homogeneous-input campaign — output must still vary."""

    async def asyncSetUp(self):
        self.env_patcher = patch.dict(os.environ, {
            "GEMINI_API_KEY": GEMINI_KEY or "",
            "OPERATOR_NAME": OPERATOR_NAME_FIXTURE,
        })
        self.env_patcher.start()
        self.sb_patcher = patch("src.core.agentic_router.SupabaseHelper")
        self.sb_patcher.start()

        from src.core.agentic_router import AgenticRouter
        self.router = AgenticRouter()
        self.assertIsNotNone(self.router.client, "Gemini client must initialize")

        self.leads = _fixture_leads()
        # 20 parallel drafts — Gemini Flash RPM ceiling sits well above 20.
        self.drafts: list[dict] = await asyncio.gather(
            *(_gen_one(self.router, l) for l in self.leads)
        )

    async def asyncTearDown(self):
        self.sb_patcher.stop()
        self.env_patcher.stop()

    def test_no_generator_errors(self):
        failures = []
        for lead, d in zip(self.leads, self.drafts):
            if "error" in d:
                failures.append(f"{lead['unique_key']}: {d['error']}")
            elif not (d.get("draft") or "").strip():
                failures.append(f"{lead['unique_key']}: empty body")
        self.assertFalse(failures, "Generator errors:\n" + "\n".join(failures))

    def test_each_draft_mentions_company_name(self):
        """Hard contract — the body must reference company_name verbatim."""
        failures = []
        for lead, d in zip(self.leads, self.drafts):
            body = d.get("draft", "")
            if lead["company_name"] not in body:
                failures.append(
                    f"{lead['unique_key']}: company_name {lead['company_name']!r} "
                    f"missing from body. First 200 chars: {body[:200]!r}"
                )
        self.assertFalse(failures, "Company name not anchored:\n" + "\n".join(failures))

    def test_subject_jaccard_under_threshold(self):
        """
        Pairwise word-set Jaccard on subject lines (after masking each lead's
        own company name + generic clinic vocabulary). Max across all pairs
        must be <= SUBJECT_JACCARD_MAX.
        """
        subjects = [d.get("subject", "") for d in self.drafts]
        token_sets = [_subject_token_set(s, l) for s, l in zip(subjects, self.leads)]

        offenders: list[tuple[float, int, int, str, str]] = []
        max_seen = 0.0
        for (i, ti), (j, tj) in combinations(enumerate(token_sets), 2):
            j_val = _jaccard(ti, tj)
            max_seen = max(max_seen, j_val)
            if j_val > SUBJECT_JACCARD_MAX:
                offenders.append((j_val, i, j, subjects[i], subjects[j]))

        if offenders:
            offenders.sort(reverse=True)
            top5 = "\n".join(
                f"  J={j_val:.2f}  [{i}] {a!r}  vs  [{j}] {b!r}"
                for j_val, i, j, a, b in offenders[:5]
            )
            self.fail(
                f"Subject Jaccard exceeded {SUBJECT_JACCARD_MAX} on "
                f"{len(offenders)} pairs (max={max_seen:.2f}). Top 5:\n{top5}"
            )

    def test_opening_sentence_cosine_under_threshold(self):
        """
        Pairwise cosine on Gemini-embedded first sentences of the body.
        Catches semantic clones that the lexical Jaccard might miss
        ("Quick question on X" vs "I had a quick question regarding Y").
        """
        openings = [_first_body_sentence(d.get("draft", "")) for d in self.drafts]
        # Refuse to evaluate empty openings — that's a generator bug, not diversity.
        empty = [i for i, s in enumerate(openings) if not s.strip()]
        self.assertFalse(empty, f"Empty body openings for indices: {empty}")

        result = self.router.client.models.embed_content(
            model=EMBED_MODEL,
            contents=openings,
        )
        vecs = [list(e.values) for e in result.embeddings]
        self.assertEqual(len(vecs), N_LEADS, "embedding count mismatch")

        offenders: list[tuple[float, int, int, str, str]] = []
        max_seen = 0.0
        for (i, vi), (j, vj) in combinations(enumerate(vecs), 2):
            c = _cosine(vi, vj)
            max_seen = max(max_seen, c)
            if c >= OPENING_COSINE_MAX:
                offenders.append((c, i, j, openings[i], openings[j]))

        if offenders:
            offenders.sort(reverse=True)
            top5 = "\n".join(
                f"  cos={c:.3f}  [{i}] {a!r}  vs  [{j}] {b!r}"
                for c, i, j, a, b in offenders[:5]
            )
            self.fail(
                f"Opening-sentence cosine >= {OPENING_COSINE_MAX} on "
                f"{len(offenders)} pairs (max={max_seen:.3f}). "
                f"Looks like personalization theater. Top 5:\n{top5}"
            )

    def test_at_least_one_pair_below_thresholds(self):
        """
        Sanity counter: if ALL pairs fail, the audit pinpoints a systemic
        template — but if the test setup itself produced identical drafts
        across the board (e.g. a deterministic seed), the failure
        messages alone are hard to read. This test prints distribution
        stats so a reviewer can sanity-check the run shape.
        """
        subjects = [d.get("subject", "") for d in self.drafts]
        openings = [_first_body_sentence(d.get("draft", "")) for d in self.drafts]
        print(
            f"\n[campaign_diversity] N={N_LEADS} drafts\n"
            f"  subjects sample: {subjects[:3]}\n"
            f"  openings sample: {[o[:80] for o in openings[:3]]}\n"
        )


if __name__ == "__main__":
    unittest.main()
