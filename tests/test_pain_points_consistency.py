"""
Stability + input-sensitivity test for LeadHunter.analyze_pain_points_async.

Two failure modes worth catching:

  1. Per-lead instability — same input, wildly different pain points each
     run. Tested by running 10 calls per lead and asserting the top-3
     pain-point categories pairwise-overlap (Jaccard) >= 0.60 on average.

  2. Input-blindness — Gemini emits the same generic SEO/analytics
     boilerplate regardless of input. Tested by asserting that
     aggregate-top-3 categories for any pair of distinct-industry leads
     overlap (Jaccard) < 0.30.

5 fixtures × 10 runs = 50 live Gemini calls. Concurrency capped at 10
in-flight via a semaphore so we don't trip flash RPM ceilings.

Live test — requires GEMINI_API_KEY. Skipped otherwise.
"""

import asyncio
import os
import re
import sys
import unittest
import pytest
from collections import Counter
from itertools import combinations
from typing import Iterable
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

GEMINI_KEY = os.getenv("GEMINI_API_KEY")

RUNS_PER_LEAD = 10
TOP_K = 3
INTRA_LEAD_JACCARD_MIN = 0.60
INTER_LEAD_JACCARD_MAX = 0.30
CONCURRENCY = 10

# Pain-point taxonomy. Each category groups synonyms so 'ssl' and 'https'
# don't split a single concept into two top-K slots. If the model leans on
# a category, ANY of its tokens triggers a hit for that run.
PAIN_CATEGORIES: dict[str, tuple[str, ...]] = {
    "ssl": ("ssl", "https", "certificate", "tls", "not secure", "insecure"),
    "mobile": (
        "mobile",
        "viewport",
        "responsive",
        "mobile-friendly",
        "mobile friendly",
    ),
    "analytics": (
        "analytics",
        "ga4",
        "google analytics",
        "gtm",
        "tag manager",
        "tracking",
    ),
    "pixel": ("pixel", "facebook pixel", "retargeting", "ad tracking"),
    "performance": ("slow", "latency", "load time", "performance", "speed", "loading"),
    "cms": ("shopify", "wordpress", "webflow", "wix", "squarespace", "cms", "platform"),
    "seo": (
        "sitemap",
        "robots.txt",
        "title tag",
        "meta description",
        "h1",
        "heading",
        "ranking",
        "seo",
    ),
    "social": (
        "social media",
        "instagram",
        "facebook page",
        "linkedin",
        "twitter",
        "social presence",
    ),
    "design": (
        "outdated",
        "design",
        "layout",
        "modern design",
        "user experience",
        "ux",
    ),
    "portal": ("portal", "client dashboard", "login area", "self-service"),
    "structure": ("structure", "value proposition", "messaging", "copy"),
    "footprint": ("digital footprint", "online presence", "discoverability"),
}


def _fixture_leads() -> list[dict]:
    """
    5 leads spanning industries + distinct technical-issue profiles. Each lead
    has page_text + audit_results that lean the prompt toward a *different*
    pain-point category, so input-blindness (lead A vs lead B overlapping
    heavily) is an unambiguous regression.
    """
    return [
        {
            "id": "shopify-no-ssl",
            "business_name": "Marigold Boutique",
            "page_text": (
                "Marigold Boutique is a small online clothing store powered by Shopify, "
                "selling handmade dresses, scarves, and accessories. Customers complete "
                "checkout via the storefront but our site is currently served over HTTP "
                "and browsers display a 'Not secure' warning on the cart page. We have "
                "no Facebook Pixel installed and run no retargeting ads."
            ),
            "audit_results": {
                "cms": "Shopify",
                "tech_flags": {
                    "has_viewport": True,
                    "has_google_analytics": True,
                    "has_gtm": False,
                    "has_facebook_pixel": False,
                    "has_portal": False,
                    "has_robots_txt": True,
                    "has_sitemap": True,
                },
                "red_flags": ["no_ssl"],
                "response_time": 1.2,
            },
        },
        {
            "id": "saas-no-analytics",
            "business_name": "Vector Insights",
            "page_text": (
                "Vector Insights builds a B2B analytics dashboard for product managers "
                "running A/B experiments. Our marketing site has a clean modern design, "
                "loads fast, and is fully responsive on mobile. However we run no "
                "Google Analytics, no GTM, and no marketing pixels — the team has no "
                "visibility into where signup traffic originates."
            ),
            "audit_results": {
                "cms": None,
                "tech_flags": {
                    "has_viewport": True,
                    "has_google_analytics": False,
                    "has_gtm": False,
                    "has_facebook_pixel": False,
                    "has_portal": True,
                    "has_robots_txt": True,
                    "has_sitemap": True,
                },
                "red_flags": [],
                "response_time": 0.9,
            },
        },
        {
            "id": "restaurant-not-mobile",
            "business_name": "Konoba Stari Most",
            "page_text": (
                "Konoba Stari Most is a family-owned restaurant in Mostar serving "
                "traditional Bosnian cuisine. The website displays our menu, opening "
                "hours, and reservation phone number. Built years ago, the site has "
                "no viewport meta tag and the layout breaks on phones — text overflows, "
                "the menu image scrolls horizontally, and the reservation button is "
                "off-screen on iPhone."
            ),
            "audit_results": {
                "cms": "WordPress",
                "tech_flags": {
                    "has_viewport": False,
                    "has_google_analytics": True,
                    "has_gtm": False,
                    "has_facebook_pixel": True,
                    "has_portal": False,
                    "has_robots_txt": True,
                    "has_sitemap": True,
                },
                "red_flags": ["mobile_broken"],
                "response_time": 1.5,
            },
        },
        {
            "id": "wordpress-slow",
            "business_name": "Heritage Press Books",
            "page_text": (
                "Heritage Press Books is an independent publisher selling rare-book "
                "reprints through a WordPress storefront with 40+ plugins active. "
                "The homepage takes over 8 seconds to load on a fast connection, "
                "Time to First Byte exceeds 4 seconds, and the Largest Contentful "
                "Paint metric is flagged red in PageSpeed. Tracking, design, and "
                "mobile responsiveness are all already in good shape."
            ),
            "audit_results": {
                "cms": "WordPress",
                "tech_flags": {
                    "has_viewport": True,
                    "has_google_analytics": True,
                    "has_gtm": True,
                    "has_facebook_pixel": True,
                    "has_portal": False,
                    "has_robots_txt": True,
                    "has_sitemap": True,
                },
                "red_flags": ["slow_response"],
                "response_time": 8.2,
            },
        },
        {
            "id": "brochure-no-social",
            "business_name": "Drina Adventure Tours",
            "page_text": (
                "Drina Adventure Tours runs guided rafting trips on the Drina canyon. "
                "The marketing site has SSL, loads quickly, is mobile-friendly, and "
                "tracking is fully configured (GA4 + Meta Pixel). What's missing is "
                "any link to Instagram, Facebook, or TikTok — and the company has no "
                "social-media presence at all. Customers searching the business name "
                "find no Instagram profile and no Facebook page."
            ),
            "audit_results": {
                "cms": None,
                "tech_flags": {
                    "has_viewport": True,
                    "has_google_analytics": True,
                    "has_gtm": True,
                    "has_facebook_pixel": True,
                    "has_portal": False,
                    "has_robots_txt": True,
                    "has_sitemap": True,
                },
                "red_flags": [],
                "response_time": 0.8,
            },
        },
    ]


def _categorize(text: str) -> Counter:
    """
    Return a Counter mapping category -> mention count. A category is hit if
    ANY of its synonym tokens appears (case-insensitive); count is the total
    occurrences across all synonyms, used as the ranking signal for top-K.
    """
    lower = text.lower()
    counts: Counter = Counter()
    for category, synonyms in PAIN_CATEGORIES.items():
        c = 0
        for s in synonyms:
            # Token boundary on either side for single-word synonyms; for
            # multi-word phrases (e.g. "google analytics") we substring-match
            # since \b won't anchor mid-phrase.
            if " " in s:
                c += lower.count(s)
            else:
                c += len(re.findall(rf"\b{re.escape(s)}\b", lower))
        if c:
            counts[category] = c
    return counts


def _top_k(counts: Counter, k: int = TOP_K) -> set[str]:
    """Top-K categories by count. Tie-break: insertion order (Counter is stable)."""
    return {cat for cat, _ in counts.most_common(k)}


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    A, B = set(a), set(b)
    if not A and not B:
        return 1.0
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


async def _analyze_once(hunter, lead: dict, sem: asyncio.Semaphore) -> str:
    async with sem:
        return await hunter.analyze_pain_points_async(
            page_text=lead["page_text"],
            business_name=lead["business_name"],
            audit_results=lead["audit_results"],
        )


@pytest.mark.live
@unittest.skipUnless(GEMINI_KEY, "Requires GEMINI_API_KEY for live Gemini calls")
class TestPainPointConsistency(unittest.IsolatedAsyncioTestCase):
    """Per-lead stability + cross-lead divergence for analyze_pain_points_async."""

    async def asyncSetUp(self):
        self.env_patcher = patch.dict(
            os.environ,
            {
                "GEMINI_API_KEY": GEMINI_KEY or "",
            },
        )
        self.env_patcher.start()

        from src.processors.leadhunter import LeadHunter

        self.hunter = LeadHunter()
        self.assertIsNotNone(self.hunter.client, "Gemini client must initialize")

        self.leads = _fixture_leads()
        sem = asyncio.Semaphore(CONCURRENCY)

        # 50 calls in flight (capped at CONCURRENCY simultaneous). Group by lead.
        tasks = []
        order = []
        for lead in self.leads:
            for _ in range(RUNS_PER_LEAD):
                tasks.append(_analyze_once(self.hunter, lead, sem))
                order.append(lead["id"])
        outputs = await asyncio.gather(*tasks)

        self.outputs_by_lead: dict[str, list[str]] = {l["id"]: [] for l in self.leads}
        for lead_id, out in zip(order, outputs):
            self.outputs_by_lead[lead_id].append(out)

        # Pre-compute top-K sets per run and aggregate-top-K per lead
        self.top_per_run: dict[str, list[set[str]]] = {}
        self.aggregate_top: dict[str, set[str]] = {}
        for lead_id, texts in self.outputs_by_lead.items():
            per_run = [_top_k(_categorize(t)) for t in texts]
            self.top_per_run[lead_id] = per_run
            # Aggregate: pool all category-counts across the lead's 10 runs,
            # then take top-K overall. Represents "what this lead is about".
            pooled: Counter = Counter()
            for t in texts:
                pooled.update(_categorize(t))
            self.aggregate_top[lead_id] = _top_k(pooled)

    async def asyncTearDown(self):
        await self.hunter.close()
        self.env_patcher.stop()

    def test_no_generator_errors(self):
        sentinel_failures = {
            "No page content available for analysis.",
            "Could not analyze pain points.",
        }
        failures = []
        for lead_id, texts in self.outputs_by_lead.items():
            for i, t in enumerate(texts):
                if not t or t.strip() in sentinel_failures:
                    failures.append(f"{lead_id}#{i}: {t!r}")
        self.assertFalse(failures, "Generator failures:\n" + "\n".join(failures))

    def test_each_run_emits_at_least_one_category(self):
        """If a run produces text matching ZERO pain categories, the taxonomy
        is wrong OR the model said something so vague top-K is meaningless."""
        failures = []
        for lead_id, per_run in self.top_per_run.items():
            for i, top in enumerate(per_run):
                if not top:
                    text = self.outputs_by_lead[lead_id][i]
                    failures.append(
                        f"{lead_id}#{i}: no category hit  text={text[:140]!r}"
                    )
        self.assertFalse(
            failures,
            "Runs producing no recognised pain category:\n" + "\n".join(failures),
        )

    def test_intra_lead_stability(self):
        """Same input × 10 runs → pairwise top-K Jaccard average >= 0.60."""
        failures = []
        for lead_id, per_run in self.top_per_run.items():
            pairs = list(combinations(per_run, 2))
            if not pairs:
                continue
            avg = sum(_jaccard(a, b) for a, b in pairs) / len(pairs)
            if avg < INTRA_LEAD_JACCARD_MIN:
                # Surface the distribution so a failure is debuggable
                dist = Counter()
                for s in per_run:
                    dist[frozenset(s)] += 1
                dist_str = "; ".join(f"{sorted(k)}×{v}" for k, v in dist.most_common())
                failures.append(
                    f"{lead_id}: avg pairwise Jaccard={avg:.3f} < {INTRA_LEAD_JACCARD_MIN}  "
                    f"top-K distribution: {dist_str}"
                )
        self.assertFalse(failures, "Intra-lead instability:\n" + "\n".join(failures))

    def test_inter_lead_divergence(self):
        """Different industries → aggregate-top-K Jaccard < 0.30 between any pair."""
        failures = []
        ids = list(self.aggregate_top.keys())
        for a, b in combinations(ids, 2):
            sa, sb = self.aggregate_top[a], self.aggregate_top[b]
            j = _jaccard(sa, sb)
            if j >= INTER_LEAD_JACCARD_MAX:
                failures.append(
                    f"{a} vs {b}: Jaccard={j:.3f} >= {INTER_LEAD_JACCARD_MAX}  "
                    f"{a}={sorted(sa)}  {b}={sorted(sb)}"
                )
        self.assertFalse(
            failures,
            "Cross-lead overlap too high — model may be ignoring inputs:\n"
            + "\n".join(failures),
        )


if __name__ == "__main__":
    unittest.main()
