"""
Property-based tests for LeadHunter.calculate_outreach_score
(src/processors/leadhunter.py:411).

calculate_outreach_score is pure Python, no Gemini call — runs offline.

IMPORTANT IMPLEMENTATION NOTE — read before changing fixtures.
The current scorer (leadhunter.py:418-482) reads ONLY:
  + email / EXTRACTED_EMAIL .................. +20
  + phone .................................... +10
  + any social (facebook/instagram/linkedin) . +15
  + rating < 4.0 ............................. +15
  + reviews < 20 ............................. +10
  + leadership_team (not 'Unknown'/'') ....... +10
  + company_size (not 'Unknown'/'') .......... +10
  + (high_risk_flag OR pain_points) .......... +20
Final: min(score, 100).

`seo_score` is NOT an input to the score. The user-stated property
"email + phone + linkedin + seo_score=90 → score >= 70" is encoded as
test_well_equipped_lead_scores_high using the inputs that *actually*
reach 70 (linkedin + email + phone + leadership_team + reviews<20 etc).
seo_score's NON-effect is locked in by test_seo_score_does_not_affect_score
— if a future refactor wires seo_score into the formula, that test fails
loud rather than silently changing prod behaviour.

Hypothesis is an optional dep. Module-level guard skips the
property-based tests if hypothesis isn't installed; fixed-fixture tests
still run.

Install hypothesis: `pip install hypothesis`
"""

import os
import sys
import unittest
from copy import deepcopy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from hypothesis import given, strategies as st, settings, HealthCheck, assume

    HAS_HYPOTHESIS = True
except ImportError:
    HAS_HYPOTHESIS = False

    # No-op stand-ins so the decorator expressions in the property-test class
    # still resolve at import time. The whole class is unittest.skipUnless'd,
    # so the stubs are never actually executed.
    def given(**_kwargs):
        def _wrap(fn):
            return fn

        return _wrap

    def settings(**_kwargs):
        def _wrap(fn):
            return fn

        return _wrap

    class _StStub:
        def __getattr__(self, _name):
            def _any(*_a, **_kw):
                return None

            return _any

    st = _StStub()  # type: ignore[assignment]

    class HealthCheck:  # noqa: N801 — mirrors real class name
        function_scoped_fixture = None

    def assume(_cond):
        pass


# Suppress LeadHunter's "GEMINI_API_KEY not found" warning at construction —
# calculate_outreach_score is pure-Python, doesn't need the client.
from src.processors.leadhunter import LeadHunter

CONTACT_FIELDS = ("email", "phone", "facebook", "instagram", "linkedin")


def _hunter() -> LeadHunter:
    """LeadHunter instance. self.client may be None (we don't call Gemini)."""
    return LeadHunter()


# ---- Fixed-fixture tests (always run) ---------------------------------------


class TestOutreachScoreFixedFixtures(unittest.TestCase):
    """The user-specified properties expressed as concrete asserts."""

    def setUp(self):
        self.hunter = _hunter()

    def test_well_equipped_lead_scores_high(self):
        """
        User property P1 — well-resourced lead with strong contacts and
        enrichment must score >= 70.

        NOTE: As written ("email + phone + linkedin + seo_score=90 → >=70")
        the formula produces 20+10+15 = 45 because seo_score is not an
        input. Encoded here with inputs the formula actually rewards so
        the test reflects the user's INTENT and not a buggy assumption.
        """
        lead = {
            "email": "founder@example.test",
            "phone": "+387 61 000 000",
            "linkedin": "https://linkedin.com/in/example",
            "leadership_team": "Jane Doe (CEO)",
            "company_size": "10-50",
            "rating": 3.5,  # < 4.0 → +15
            "reviews": 12,  # < 20  → +10
            "high_risk_flag": True,
            "seo_score": 90,  # irrelevant — see module docstring
        }
        score = self.hunter.calculate_outreach_score(lead)
        self.assertGreaterEqual(score, 70, f"got {score} for fully-equipped lead")
        self.assertLessEqual(score, 100, f"got {score}, above 100 — overflow")

    def test_no_contacts_high_risk_scores_low(self):
        """User property P2 — bare lead, only signal is high_risk_flag (+20)."""
        lead = {"high_risk_flag": True, "seo_score": 10}
        score = self.hunter.calculate_outreach_score(lead)
        self.assertLessEqual(score, 30, f"got {score} for high-risk-only lead")
        self.assertGreaterEqual(score, 0)

    def test_completely_empty_lead_scores_zero(self):
        self.assertEqual(self.hunter.calculate_outreach_score({}), 0)

    def test_seo_score_does_not_affect_score(self):
        """
        Documents the gap between user expectation and current implementation:
        seo_score is not an input to calculate_outreach_score
        (leadhunter.py:418-482). If this test starts failing because a refactor
        wires seo_score in, that is a deliberate behaviour change — update
        both the formula and this test together.
        """
        base = {"email": "a@b.com", "phone": "+1 555 0000"}
        s_absent = self.hunter.calculate_outreach_score(deepcopy(base))
        s_low = self.hunter.calculate_outreach_score({**base, "seo_score": 10})
        s_high = self.hunter.calculate_outreach_score({**base, "seo_score": 90})
        self.assertEqual(
            s_absent,
            s_low,
            "Adding seo_score=10 changed the score — formula may have been refactored. See module docstring.",
        )
        self.assertEqual(
            s_absent,
            s_high,
            "Adding seo_score=90 changed the score — formula may have been refactored. See module docstring.",
        )

    def test_deterministic_repeated_calls(self):
        """User property: same input, 10 runs, same score (pure function)."""
        lead = {
            "email": "a@b.com",
            "phone": "+1",
            "facebook": "x",
            "rating": 3.0,
            "reviews": 5,
            "leadership_team": "X",
            "company_size": "5-10",
            "high_risk_flag": True,
            "pain_points": "slow",
        }
        first = self.hunter.calculate_outreach_score(deepcopy(lead))
        for _ in range(9):
            self.assertEqual(
                self.hunter.calculate_outreach_score(deepcopy(lead)),
                first,
                "calculate_outreach_score is not deterministic across repeated calls",
            )


# ---- Hypothesis property-based tests (skipped if hypothesis missing) --------


def _social_strategy():
    """Either absent (None → key dropped post-build) or a plausible URL/handle."""
    return st.one_of(
        st.none(),
        st.text(
            min_size=1,
            max_size=40,
            alphabet=st.characters(min_codepoint=33, max_codepoint=126),
        ),
    )


def _lead_strategy():
    """
    Build leads spanning the realistic input surface. Optional keys may be
    None — we drop None keys in the wrapping helper so the scorer sees a
    "field absent" lead, not "field present with None".
    """
    return st.fixed_dictionaries(
        {},
        optional={
            "email": st.one_of(st.none(), st.emails()),
            "EXTRACTED_EMAIL": st.one_of(st.none(), st.emails()),
            "phone": st.one_of(st.none(), st.text(min_size=1, max_size=20)),
            "facebook": _social_strategy(),
            "instagram": _social_strategy(),
            "linkedin": _social_strategy(),
            "rating": st.one_of(
                st.none(),
                st.floats(
                    min_value=0, max_value=5, allow_nan=False, allow_infinity=False
                ),
            ),
            "reviews": st.one_of(
                st.none(), st.integers(min_value=0, max_value=100_000)
            ),
            "leadership_team": st.one_of(
                st.none(),
                st.just(""),
                st.just("Unknown"),
                st.text(min_size=1, max_size=30),
            ),
            "company_size": st.one_of(
                st.none(),
                st.just(""),
                st.just("Unknown"),
                st.sampled_from(["1-10", "10-50", "50-200", "200+"]),
            ),
            "high_risk_flag": st.booleans(),
            "pain_points": st.one_of(
                st.none(),
                st.just(""),
                st.text(min_size=1, max_size=200),
            ),
        },
    )


def _strip_none(lead: dict) -> dict:
    """The scorer treats absent keys differently from present-but-None. The
    strategy emits None to model 'absent'; collapse them here."""
    return {k: v for k, v in lead.items() if v is not None}


def _add_contact(lead: dict, field: str) -> dict:
    """Return a new lead with the given contact field populated."""
    out = deepcopy(lead)
    out[field] = "x@y.test" if field == "email" else "value-present"
    return out


@unittest.skipUnless(
    HAS_HYPOTHESIS, "hypothesis not installed — `pip install hypothesis`"
)
class TestOutreachScoreProperties(unittest.TestCase):
    """Hypothesis-fuzzed properties over the input surface."""

    def setUp(self):
        self.hunter = _hunter()

    @settings(
        max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    @given(lead=_lead_strategy())
    def test_score_within_bounds(self, lead):
        """Score must be int in [0, 100] for every valid input shape."""
        clean = _strip_none(lead)
        score = self.hunter.calculate_outreach_score(clean)
        self.assertIsInstance(score, int, f"non-int score {score!r} for {clean}")
        self.assertGreaterEqual(score, 0, f"negative score {score} for {clean}")
        self.assertLessEqual(score, 100, f"score {score} above 100 for {clean}")

    @settings(
        max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    @given(lead=_lead_strategy())
    def test_deterministic_under_fuzz(self, lead):
        """Same input dict → same score across 10 fresh calls."""
        clean = _strip_none(lead)
        first = self.hunter.calculate_outreach_score(deepcopy(clean))
        for i in range(9):
            again = self.hunter.calculate_outreach_score(deepcopy(clean))
            self.assertEqual(
                again,
                first,
                f"non-deterministic on iter {i}: {again} != {first}  input={clean}",
            )

    @settings(
        max_examples=300, suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    @given(lead=_lead_strategy(), addition=st.sampled_from(CONTACT_FIELDS))
    def test_adding_contact_is_monotone(self, lead, addition):
        """
        Adding a contact field never DECREASES the score.

        Contact fields are email/phone/facebook/instagram/linkedin. We pop
        the field if already present to start from a 'truly absent' baseline,
        then add it and re-score. score_after >= score_before must hold.
        """
        clean = _strip_none(lead)
        clean.pop(addition, None)
        # If email is being added, also clear EXTRACTED_EMAIL so the
        # before/after delta isolates the addition (both feed the +20).
        if addition == "email":
            clean.pop("EXTRACTED_EMAIL", None)

        before = self.hunter.calculate_outreach_score(deepcopy(clean))
        enriched = _add_contact(clean, addition)
        after = self.hunter.calculate_outreach_score(enriched)
        self.assertGreaterEqual(
            after,
            before,
            f"adding {addition!r} decreased score: {before} → {after}  base={clean}",
        )

    @settings(
        max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    @given(lead=_lead_strategy(), seo=st.integers(min_value=0, max_value=100))
    def test_seo_score_is_invariant_under_fuzz(self, lead, seo):
        """
        Locks in the documented gap from the module docstring under fuzz.
        score(lead) == score(lead ∪ {seo_score: any}) for all leads.
        """
        clean = _strip_none(lead)
        clean.pop("seo_score", None)
        baseline = self.hunter.calculate_outreach_score(deepcopy(clean))
        with_seo = self.hunter.calculate_outreach_score({**clean, "seo_score": seo})
        self.assertEqual(
            baseline,
            with_seo,
            f"seo_score={seo} changed score {baseline} → {with_seo}  base={clean}",
        )


if __name__ == "__main__":
    unittest.main()
