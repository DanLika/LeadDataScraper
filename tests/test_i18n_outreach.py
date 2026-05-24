"""
Internationalisation tests for outreach + LinkedIn drafts + AI column mapper.

Why this matters for THIS market: the operator is BiH-based, lead inventory
is heavily Bosnian/Croatian, and the prompts are hardcoded English. We need
to guarantee that diacritics survive the round-trip, the lead name doesn't
silently get transliterated to ASCII, and the draft body doesn't end up
half-English-half-Bosnian.

Test surface:

  1. /draft-outreach with Bosnian/Croatian leads
     - Default behaviour is English output (current prompt requires it).
     - Diacritics (č, ć, š, ž, đ) in the lead's name / company name must
       be preserved byte-for-byte where the name is echoed.
     - No mojibake substrings (ÄŸ, Å¾, Ã©, etc.).
     - No "mixed-language slop" — the body is monolingual English save
       for the proper-noun fragments of the lead's identity.

  2. /draft-linkedin with the same leads — same diacritic + slop checks.

  3. AI column mapper on a Croatian CSV header row — diacritic-bearing
     headers (Mjesto, Računovođa) must still map to standard targets.

Live test — requires GEMINI_API_KEY. Skipped otherwise. Supabase is mocked.
"""
import asyncio
import json
import os
import re
import sys
import unicodedata
import unittest
import pytest
from typing import Any
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

GEMINI_KEY = os.getenv("GEMINI_API_KEY")
OPERATOR_NAME_FIXTURE = "Test Operator"

# Mojibake fingerprints — sequences that appear when UTF-8 bytes get decoded
# as Latin-1 / Windows-1252. If any of these shows up in a draft, the text
# was double-encoded somewhere along the wire.
MOJIBAKE_SUBSTRINGS = (
    "Ã¡", "Ã©", "Ã­", "Ã³", "Ãº",
    "Å¡", "Å¾", "Å ", "Å½",
    "Ä‡", "Ä�", "ÄŒ", "Ä�", "Ä‘",
    "Ä�ev", "Ä‡ev",
    "Â ", "â€™", "â€œ", "â€�", "â€"
)

# Bosnian / Croatian function-word stopwords. If a draft is supposed to be
# English but contains 2+ of these, it's mixed-language slop. The list is
# deliberately conservative — common words ONLY, never proper nouns.
BCS_FUNCTION_WORDS = {
    "vaš", "vaša", "vaše", "vaši", "vašu", "vašom",
    "moj", "moja", "moje", "moji", "moju",
    "ja", "ti", "mi", "vi", "oni", "ona", "ono", "ovaj", "ova", "ovo",
    "taj", "ta", "to", "onaj", "ona",
    "je", "su", "smo", "ste", "sam", "si",
    "biti", "imati", "ima", "imaju",
    "dobar", "dobra", "dobro", "loš", "loša",
    "kako", "što", "šta", "gdje", "kada", "kad", "zašto", "ko",
    "za", "na", "u", "od", "do", "sa", "po", "iz",
    "ali", "ili", "kao", "iako", "jer", "dok", "ako",
    "samo", "već", "još", "uvijek", "nikad",
    "možete", "možemo", "želite", "želimo", "trebate", "trebamo",
    "puno", "vrlo", "malo", "više", "manje",
    "vidio", "vidjeli", "vidim",
    "pomoći", "pomoć", "pomagati",
    "molim", "hvala", "pozdrav", "srdačno",
    "stranice", "stranica", "stranicu", "stranici",
    "preduzeće", "tvrtka", "firma", "firme",
}


def _strip_diacritics(s: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFD", s)
        if not unicodedata.combining(ch)
    )


def _has_mojibake(text: str) -> list[str]:
    return [m for m in MOJIBAKE_SUBSTRINGS if m in text]


def _bcs_function_tokens(text: str) -> list[str]:
    """Lowercase-word match against the BCS function-word set."""
    tokens = re.findall(r"[A-Za-zÀ-ÿĀ-žČčĆćĐđŠšŽž]+", text)
    return [t for t in tokens if t.lower() in BCS_FUNCTION_WORDS]


def _name_fragments_with_diacritics(name: str) -> list[str]:
    """Split a name on whitespace + punctuation; keep only fragments that
    actually contain a non-ASCII diacritic. These are the strings we care
    about preserving."""
    pieces = re.split(r"[\s.,/—-]+", name)
    return [p for p in pieces if p and _strip_diacritics(p) != p]


def _fixture_leads_outreach() -> list[dict]:
    """3 BiH/Croatian leads with diacritic-heavy names + Bosnian source data."""
    return [
        {
            "unique_key": "i18n_1",
            "name": "Zubarska ordinacija Dr. Kovačević",
            "company_name": "Zubarska ordinacija Dr. Kovačević",
            "website": "https://kovacevic-dent.example",
            "email": "ordinacija@kovacevic-dent.example",
            "business_details": (
                "Privatna stomatološka praksa u Sarajevu, specijalizirana "
                "za implantologiju i estetsku stomatologiju."
            ),
            "audit_results": {
                "score": 38,
                "no_h1": True,
                "missing_description": True,
                "ssl_valid": True,
                "pain_points": (
                    "Početna stranica nema H1 naslov i meta opis — Google "
                    "ne razumije o čemu je sajt."
                ),
            },
            # Optional fields for LinkedIn path
            "leadership_team": "Dr. Amira Kovačević",
            "target_clients": "Pacijenti iz Sarajeva i šire regije.",
        },
        {
            "unique_key": "i18n_2",
            "name": "Pekara Žito d.o.o.",
            "company_name": "Pekara Žito d.o.o.",
            "website": "http://pekara-zito.example",
            "email": "kontakt@pekara-zito.example",
            "business_details": (
                "Obiteljska pekara u Zagrebu specijalizirana za tradicionalni "
                "kruh, štrudle i sezonske kolače."
            ),
            "audit_results": {
                "score": 22,
                "ssl_valid": False,
                "pain_points": (
                    "Sajt se poslužuje preko HTTP — preglednici upozoravaju "
                    "da nije siguran."
                ),
            },
            "leadership_team": "Ivana Šimić",
            "target_clients": "Lokalni stanovnici Zagreba i okolice.",
        },
        {
            "unique_key": "i18n_3",
            "name": "Računovodstveni servis Đurić",
            "company_name": "Računovodstveni servis Đurić",
            "website": "https://djuric-racunovodstvo.example",
            "email": "info@djuric-racunovodstvo.example",
            "business_details": (
                "Mali računovodstveni servis u Banja Luci koji pruža usluge "
                "vođenja knjiga, obračuna plata i poreznih prijava za SME."
            ),
            "audit_results": {
                "score": 31,
                "no_h1": True,
                "ssl_valid": True,
                "pain_points": (
                    "Sajt nema H1 naslov i nedostaje sitemap.xml — slabo "
                    "rangira u Google rezultatima."
                ),
            },
            "leadership_team": "Marko Đurić",
            "target_clients": "Mala i srednja preduzeća u Republici Srpskoj.",
        },
    ]


# ---- Fake Supabase (LinkedIn draft path needs DB) ---------------------------

class _FakeExecResult:
    def __init__(self, rows): self.data = rows


class _FakeQuery:
    def __init__(self, leads_by_key: dict):
        self._lbk = leads_by_key
        self._eq_filter = None

    def select(self, *_a, **_k): return self
    def eq(self, col, val):
        if col == "unique_key":
            self._eq_filter = val
        return self
    def limit(self, _n): return self
    def execute(self):
        if self._eq_filter is not None:
            lead = self._lbk.get(self._eq_filter)
            return _FakeExecResult([lead] if lead else [])
        return _FakeExecResult([])


class _FakeSupabaseClient:
    def __init__(self, leads_by_key: dict):
        self._lbk = leads_by_key
    def table(self, _name): return _FakeQuery(self._lbk)


# ---- Per-draft validators (shared by outreach + linkedin tests) -------------

def _check_no_mojibake(label: str, text: str, failures: list[str]) -> None:
    hits = _has_mojibake(text)
    if hits:
        failures.append(f"{label}: mojibake substrings {hits}")


def _check_no_bcs_slop(label: str, text: str, failures: list[str]) -> None:
    """Default behaviour is English; >=2 BCS function words = mixed-language."""
    hits = _bcs_function_tokens(text)
    if len(hits) >= 2:
        failures.append(f"{label}: mixed-language slop, BCS tokens {hits[:6]}")


def _check_diacritics_preserved(label: str, lead: dict, text: str, failures: list[str]) -> None:
    """
    For every diacritic-bearing fragment in the lead's name, EITHER:
      - it appears in the draft verbatim (good), OR
      - it doesn't appear at all (the draft chose not to mention it — fine), OR
      - the ASCII-folded version appears (BAD: silently transliterated).
    Third case is the failure mode we catch here.
    """
    name = lead.get("name", "")
    for frag in _name_fragments_with_diacritics(name):
        ascii_form = _strip_diacritics(frag)
        if frag in text:
            continue  # diacritic-preserved mention — good
        if ascii_form != frag and ascii_form in text:
            failures.append(
                f"{label}: lead name fragment {frag!r} appears as ASCII "
                f"{ascii_form!r} (diacritics lost)"
            )


async def _gen_outreach(router, lead: dict) -> dict:
    return await router._generate_outreach_draft({
        "unique_key": lead["unique_key"],
        "lead_data": lead,
    })


async def _gen_linkedin(router, lead: dict) -> dict:
    return await router._generate_linkedin_draft({"unique_key": lead["unique_key"]})


# ---- Outreach + LinkedIn i18n test class ------------------------------------

@pytest.mark.live
@unittest.skipUnless(GEMINI_KEY, "Requires GEMINI_API_KEY for live Gemini calls")
class TestOutreachI18n(unittest.IsolatedAsyncioTestCase):
    """Bosnian/Croatian inputs through /draft-outreach and /draft-linkedin."""

    async def asyncSetUp(self):
        self.env_patcher = patch.dict(os.environ, {
            "GEMINI_API_KEY": GEMINI_KEY or "",
            "OPERATOR_NAME": OPERATOR_NAME_FIXTURE,
        })
        self.env_patcher.start()

        self.leads = _fixture_leads_outreach()
        leads_by_key = {l["unique_key"]: l for l in self.leads}

        # Outreach uses lead_data bypass; LinkedIn needs the DB fake.
        self.sb_patcher = patch("src.core.agentic_router.SupabaseHelper")
        sb_mock = self.sb_patcher.start()
        sb_mock.return_value.client = _FakeSupabaseClient(leads_by_key)

        from src.core.agentic_router import AgenticRouter
        self.router = AgenticRouter()
        self.assertIsNotNone(self.router.client, "Gemini client must initialize")

        # Generate both kinds in parallel
        outreach_drafts, linkedin_drafts = await asyncio.gather(
            asyncio.gather(*(_gen_outreach(self.router, l) for l in self.leads)),
            asyncio.gather(*(_gen_linkedin(self.router, l) for l in self.leads)),
        )
        self.outreach: list[dict] = list(outreach_drafts)
        self.linkedin: list[dict] = list(linkedin_drafts)

    async def asyncTearDown(self):
        self.sb_patcher.stop()
        self.env_patcher.stop()

    def test_outreach_no_generator_errors(self):
        failures = []
        for lead, d in zip(self.leads, self.outreach):
            if "error" in d:
                failures.append(f"{lead['unique_key']} outreach: {d['error']}")
            elif not (d.get("draft") or "").strip():
                failures.append(f"{lead['unique_key']} outreach: empty body")
        self.assertFalse(failures, "\n".join(failures))

    def test_linkedin_no_generator_errors(self):
        failures = []
        for lead, d in zip(self.leads, self.linkedin):
            if "error" in d:
                failures.append(f"{lead['unique_key']} linkedin: {d['error']}")
            elif not (d.get("draft") or "").strip():
                failures.append(f"{lead['unique_key']} linkedin: empty body")
        self.assertFalse(failures, "\n".join(failures))

    def test_outreach_no_mojibake(self):
        failures = []
        for lead, d in zip(self.leads, self.outreach):
            text = (d.get("subject", "") + " " + d.get("draft", ""))
            _check_no_mojibake(f"{lead['unique_key']} outreach", text, failures)
        self.assertFalse(failures, "Mojibake in outreach:\n" + "\n".join(failures))

    def test_linkedin_no_mojibake(self):
        failures = []
        for lead, d in zip(self.leads, self.linkedin):
            _check_no_mojibake(f"{lead['unique_key']} linkedin", d.get("draft", ""), failures)
        self.assertFalse(failures, "Mojibake in linkedin:\n" + "\n".join(failures))

    def test_outreach_no_bcs_slop(self):
        """Default = English. >=2 Bosnian/Croatian function words = slop."""
        failures = []
        for lead, d in zip(self.leads, self.outreach):
            _check_no_bcs_slop(f"{lead['unique_key']} outreach", d.get("draft", ""), failures)
        self.assertFalse(failures, "Mixed-language outreach:\n" + "\n".join(failures))

    def test_linkedin_no_bcs_slop(self):
        failures = []
        for lead, d in zip(self.leads, self.linkedin):
            _check_no_bcs_slop(f"{lead['unique_key']} linkedin", d.get("draft", ""), failures)
        self.assertFalse(failures, "Mixed-language linkedin:\n" + "\n".join(failures))

    def test_outreach_diacritics_preserved(self):
        """If the draft mentions the lead's name, diacritics must survive."""
        failures = []
        for lead, d in zip(self.leads, self.outreach):
            text = d.get("subject", "") + " " + d.get("draft", "")
            _check_diacritics_preserved(
                f"{lead['unique_key']} outreach", lead, text, failures
            )
        self.assertFalse(
            failures, "Silent ASCII transliteration in outreach:\n" + "\n".join(failures)
        )

    def test_linkedin_diacritics_preserved(self):
        failures = []
        for lead, d in zip(self.leads, self.linkedin):
            _check_diacritics_preserved(
                f"{lead['unique_key']} linkedin", lead, d.get("draft", ""), failures
            )
        self.assertFalse(
            failures, "Silent ASCII transliteration in linkedin:\n" + "\n".join(failures)
        )


# ---- AI mapper i18n ---------------------------------------------------------

@pytest.mark.live
@unittest.skipUnless(GEMINI_KEY, "Requires GEMINI_API_KEY for live Gemini calls")
class TestMapperI18n(unittest.IsolatedAsyncioTestCase):
    """Croatian CSV header row through GeminiMapper.get_column_mapping."""

    async def asyncSetUp(self):
        self.env_patcher = patch.dict(os.environ, {
            "GEMINI_API_KEY": GEMINI_KEY or "",
        })
        self.env_patcher.start()

        from src.processors.ai_mapper import GeminiMapper
        self.mapper = GeminiMapper()
        self.assertIsNotNone(self.mapper.client, "Gemini client must initialize")

    async def asyncTearDown(self):
        self.env_patcher.stop()

    async def test_croatian_csv_headers_mapped(self):
        """
        Realistic Croatian CSV header row — must map the four core fields
        (company, website, email, phone). Diacritic-bearing headers must
        not crash the mapper or get silently dropped from the input list
        before the allowlist check.
        """
        headers = [
            "Naziv tvrtke",  # company name
            "Web stranica",  # website
            "E-pošta",       # email
            "Telefon",       # phone
            "Mjesto",        # city (no standard target — should be ignored)
            "Računovođa",    # accountant (no target — ignored, diacritic stress)
        ]
        mapping = await asyncio.to_thread(self.mapper.get_column_mapping, headers)
        self.assertIsInstance(mapping, dict, f"non-dict return: {type(mapping)}")

        expected = {
            "Naziv tvrtke": "company_name",
            "Web stranica": "website",
            "E-pošta": "email",
            "Telefon": "phone",
        }
        failures = []
        for src, want in expected.items():
            got = mapping.get(src)
            if got != want:
                failures.append(f"{src!r} → {got!r}, expected {want!r}")
        self.assertFalse(failures, "Croatian header mapping misses:\n" + "\n".join(failures))

        # Mojibake check on the keys the mapper echoed back — Gemini sometimes
        # round-trips strings through a non-UTF-8 path and corrupts diacritics
        # in the response.
        mojibake = [k for k in mapping if _has_mojibake(k)]
        self.assertFalse(mojibake, f"Mojibake in mapping keys: {mojibake}")

    async def test_bosnian_csv_headers_mapped(self):
        """Bosnian variant. 'Ime firme' and 'Telefon' overlap with Croatian;
        'Adresa' should be ignored (no standard target). Diacritics in the
        domain word 'računovodstvo' must not break the mapper."""
        headers = [
            "Ime firme",
            "Web adresa",
            "Email adresa",
            "Telefon",
            "Adresa",            # no target
            "računovodstvo",     # no target, lowercase diacritic stress
        ]
        mapping = await asyncio.to_thread(self.mapper.get_column_mapping, headers)
        self.assertIsInstance(mapping, dict)
        expected = {
            "Ime firme": "company_name",
            "Web adresa": "website",
            "Email adresa": "email",
            "Telefon": "phone",
        }
        # NB: 'Adresa' could plausibly map to 'address' (which IS a standard
        # column). We don't assert it doesn't — only that the core four hit.
        failures = []
        for src, want in expected.items():
            got = mapping.get(src)
            if got != want:
                failures.append(f"{src!r} → {got!r}, expected {want!r}")
        self.assertFalse(failures, "Bosnian header mapping misses:\n" + "\n".join(failures))

        mojibake = [k for k in mapping if _has_mojibake(k)]
        self.assertFalse(mojibake, f"Mojibake in mapping keys: {mojibake}")


if __name__ == "__main__":
    unittest.main()
