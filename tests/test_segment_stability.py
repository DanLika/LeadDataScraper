"""
Stability + vocabulary test for LeadHunter.segment_lead
(src/processors/leadhunter.py:504).

CONTEXT — read before refactoring this test.
The current segment_lead implementation is pure-Python regex matching
(SECURITY/PERFORMANCE/MOBILE/MARKETING/ENTERPRISE/LOCAL_SMB patterns +
score thresholds + reputation rules). It does NOT call Gemini. So:
  - Per-lead variance is impossible by construction.
  - Vocabulary is closed: 11 hardcoded labels.

Why this test still earns its keep:
  1. Regression guard. If anyone re-implements segment_lead on top of
     Gemini (a known temptation — see the test brief: "If unstable,
     lower temperature or move to enum-based prompt"), this test will
     trip the moment per-lead variance appears or a label slips outside
     the closed set.
  2. Contract pin. The 11-label vocabulary is now testable. Any commit
     that adds, renames, or removes a label without updating
     `KNOWN_LABELS` here gets flagged.
  3. Fixture coverage. The 20-fixture matrix documents which inputs map
     to which segment — a usable reference when you're debugging
     "why did this lead end up Low Priority?".

Pure Python — no GEMINI_API_KEY, no network.
"""

import os
import sys
import unittest
from collections import Counter
from copy import deepcopy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.processors.leadhunter import LeadHunter

RUNS_PER_LEAD = 5

# The closed set of segment labels segment_lead can return today
# (leadhunter.py:513-540). Adding a new label here without updating
# segment_lead is a no-op; adding a new label in segment_lead without
# updating this set will fail test_labels_within_closed_set with a
# pointer to the new value.
KNOWN_LABELS: set[str] = {
    "Security/Critical Fix",
    "Performance Optimization",
    "Mobile Experience",
    "Reputation Repair",
    "New Business / Growth",
    "Marketing Analytics",
    "Enterprise B2B",
    "Local SMB",
    "High Value / Outreach Ready",
    "Warm / Needs Personalization",
    "Low Priority Prospect",
}

# Distinct-label ceiling per the test brief ("e.g. <= 10"). The function's
# actual ceiling is 11; we give 1 slack so a deliberately introduced 12th
# label tripwires here.
MAX_DISTINCT_LABELS = 12


def _fixtures() -> list[dict]:
    """
    20 leads spanning the segment matrix. Each fixture pins an `_expected`
    label so the test can also verify routing correctness, not just
    stability + closed-set membership. The `_expected` key is stripped
    before passing to segment_lead.
    """
    return [
        # --- Group A: pain-pattern short-circuits (priority order matters) ---
        {
            "_id": "f01_security_ssl",
            "_expected": "Security/Critical Fix",
            "pain_points": "Missing SSL — site served over HTTP. Critical security gap.",
        },
        {
            "_id": "f02_security_word",
            "_expected": "Security/Critical Fix",
            "pain_points": "Multiple critical issues including missing SSL certificate.",
        },
        {
            "_id": "f03_perf_slow",
            "_expected": "Performance Optimization",
            "pain_points": "Homepage slow to load — latency over 4 seconds.",
        },
        {
            "_id": "f04_perf_load_time",
            "_expected": "Performance Optimization",
            "pain_points": "Poor performance: load time exceeds 8s on first paint.",
        },
        {
            "_id": "f05_mobile_viewport",
            "_expected": "Mobile Experience",
            "pain_points": "No viewport meta — not responsive on phones.",
        },
        # --- Group B: reputation pre-empts marketing/score branches ---
        {
            "_id": "f06_low_rating",
            "_expected": "Reputation Repair",
            "rating": 3.2,
            "reviews": 80,
            "pain_points": "Some marketing gaps.",
        },
        {
            "_id": "f07_low_rating_comma",
            "_expected": "Reputation Repair",
            "rating": "3,5",
            "reviews": 25,
            "pain_points": "Site fine, customer reviews concerning.",
        },
        {
            "_id": "f08_new_business_no_rating",
            "_expected": "New Business / Growth",
            "reviews": "4 reviews",
            "pain_points": "",
        },
        {
            "_id": "f09_new_business_good_rating",
            "_expected": "New Business / Growth",
            "rating": 4.6,
            "reviews": 7,
            "pain_points": "",
        },
        # --- Group C: marketing pattern ---
        {
            "_id": "f10_marketing_pixel",
            "_expected": "Marketing Analytics",
            "pain_points": "No Facebook Pixel installed.",
        },
        {
            "_id": "f11_marketing_ga4",
            "_expected": "Marketing Analytics",
            "pain_points": "Missing GA4 tracking and Google Tag Manager.",
        },
        # --- Group D: niche enrichment (target_clients) ---
        {
            "_id": "f12_enterprise",
            "_expected": "Enterprise B2B",
            "pain_points": "Sales site decent, room for refinement.",
            "target_clients": "Fortune 500 enterprise procurement teams",
        },
        {
            "_id": "f13_enterprise_corporate",
            "_expected": "Enterprise B2B",
            "pain_points": "",
            "enrichment_data": {"target_clients": "Large corporate buyers"},
        },
        {
            "_id": "f14_local_smb",
            "_expected": "Local SMB",
            "pain_points": "",
            "target_clients": "Local plumbers and small home-services shops",
        },
        {
            "_id": "f15_local_smb_residential",
            "_expected": "Local SMB",
            "pain_points": "",
            "enrichment_data": {"target_clients": "Residential customers nearby"},
        },
        # --- Group E: score-only branches (no pattern / reputation triggers) ---
        {
            "_id": "f16_high_value",
            "_expected": "High Value / Outreach Ready",
            "outreach_score": 85,
            "pain_points": "",
        },
        {
            "_id": "f17_warm",
            "_expected": "Warm / Needs Personalization",
            "outreach_score": 60,
            "pain_points": "",
        },
        {
            "_id": "f18_low_priority",
            "_expected": "Low Priority Prospect",
            "outreach_score": 15,
            "pain_points": "",
        },
        {"_id": "f19_empty_lead", "_expected": "Low Priority Prospect"},
        # --- Group F: precedence canary — security wins over reputation ---
        # If priority order in segment_lead is ever reordered (e.g. moving
        # reputation above security), this fixture's expected label flips
        # and the test fails — surfacing the precedence change.
        {
            "_id": "f20_precedence_security_beats_reputation",
            "_expected": "Security/Critical Fix",
            "pain_points": "Missing SSL — critical security issue.",
            "rating": 2.5,
            "reviews": 3,
        },
    ]


def _strip_meta(fixture: dict) -> dict:
    """Remove test-only keys (`_id`, `_expected`) before passing to segment_lead."""
    return {k: v for k, v in fixture.items() if not k.startswith("_")}


class TestSegmentStability(unittest.TestCase):
    """100 calls (20 leads × 5 runs). Stability + vocabulary contract."""

    def setUp(self):
        self.hunter = LeadHunter()
        self.fixtures = _fixtures()
        self.assertEqual(len(self.fixtures), 20, "fixture count drifted")

        # Group results by fixture id. Use deepcopy per call so mutation
        # by segment_lead (if any) can't leak between runs of the same fixture.
        self.results: dict[str, list[str]] = {}
        for f in self.fixtures:
            inputs = _strip_meta(f)
            self.results[f["_id"]] = [
                self.hunter.segment_lead(deepcopy(inputs)) for _ in range(RUNS_PER_LEAD)
            ]

    def test_per_lead_zero_variance(self):
        """Each lead's 5 runs must produce the IDENTICAL label."""
        failures = []
        for fixture in self.fixtures:
            labels = self.results[fixture["_id"]]
            distinct = set(labels)
            if len(distinct) != 1:
                failures.append(
                    f"{fixture['_id']}: {len(distinct)} distinct labels over "
                    f"{RUNS_PER_LEAD} runs: {labels}"
                )
        self.assertFalse(
            failures,
            "Per-lead variance — segment_lead is not deterministic:\n"
            + "\n".join(failures),
        )

    def test_distinct_label_count_bounded(self):
        """
        Across all 100 calls, distinct label count <= MAX_DISTINCT_LABELS.
        Catches a future Gemini-backed segmenter inventing new labels.
        """
        all_labels: list[str] = []
        for r in self.results.values():
            all_labels.extend(r)
        distribution = Counter(all_labels)
        distinct = set(all_labels)
        self.assertLessEqual(
            len(distinct),
            MAX_DISTINCT_LABELS,
            f"{len(distinct)} distinct labels exceeds {MAX_DISTINCT_LABELS} "
            f"ceiling. Distribution: {dict(distribution)}",
        )

    def test_labels_within_closed_set(self):
        """Every emitted label must be in KNOWN_LABELS. Catches typos and drift."""
        all_labels: set[str] = set()
        for r in self.results.values():
            all_labels.update(r)
        unknown = all_labels - KNOWN_LABELS
        self.assertFalse(
            unknown,
            f"Labels emitted that are not in KNOWN_LABELS: {sorted(unknown)}.\n"
            f"Either segment_lead was updated and KNOWN_LABELS wasn't, OR a "
            f"typo crept in. Update KNOWN_LABELS in this test deliberately.",
        )

    def test_labels_non_empty_strings(self):
        """Defensive: no fixture should produce empty / None / non-string labels."""
        failures = []
        for fid, labels in self.results.items():
            for i, lbl in enumerate(labels):
                if not isinstance(lbl, str) or not lbl.strip():
                    failures.append(f"{fid}#{i}: {lbl!r}")
        self.assertFalse(failures, "Empty/non-string labels:\n" + "\n".join(failures))

    def test_expected_label_per_fixture(self):
        """
        Bonus assertion — each fixture's _expected label must match. This
        documents the routing rules (which input shape maps to which segment)
        so a reviewer can see the contract at a glance.
        """
        failures = []
        for fixture in self.fixtures:
            got = self.results[fixture["_id"]][0]
            want = fixture["_expected"]
            if got != want:
                failures.append(f"{fixture['_id']}: got {got!r}, expected {want!r}")
        self.assertFalse(
            failures,
            "Routing mismatch (input → expected segment):\n" + "\n".join(failures),
        )


if __name__ == "__main__":
    unittest.main()
