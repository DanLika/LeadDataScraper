"""Seed 20 realistic Croatian demo leads.

Phase 13.3 deliverable. Lets a fresh operator install (or a screenshot
session, or a feature-walkthrough video) show a dashboard that already
has content — without polluting the real lead pool. Every row carries
``is_demo = TRUE`` so the frontend's "Show demo data" toggle (default
OFF) hides them, the backend's ``/leads`` + ``/stats`` skip them by
default, and the Settings → Danger Zone "Remove all demo data" button
wipes them in one click.

Safety:

- Every website + email uses the IANA-reserved ``.invalid`` TLD
  (RFC 6761). Any accidental SSRF probe by the audit pipeline fails at
  DNS resolution; any accidental SMTP attempt by a future dispatcher
  hard-bounces in the resolver, never hitting a real mailbox.
- ``audit_status`` is set to ``Completed`` / ``Failed`` for 17 of 20
  rows with a pre-filled ``audit_results`` JSONB so the
  ``ParallelAuditor`` never has a reason to fetch the website. The 3
  ``Pending`` rows would also fail-fast on resolution.
- ``lead_source`` is the literal sentinel ``_demo_`` — orthogonal to
  the production ``google_maps`` / ``csv_upload`` values, lets ad-hoc
  SQL filter ``WHERE lead_source = '_demo_'`` even without the
  ``is_demo`` column.
- Idempotent: ``upsert(ignore_duplicates=True)`` ⇒ re-running this
  script is a no-op (existing rows are left intact, including any
  manual edits the operator made for screenshot purposes).

Run with backend ``.env`` loaded (uses
``SUPABASE_SERVICE_ROLE_KEY``):

    python -m src.scripts.seed_demo_data

Exit codes:
    0 — ok (rows inserted or already present)
    2 — Supabase client could not be configured
    3 — upsert raised
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

from src.utils.supabase_helper import SupabaseHelper

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _audit_results(
    score: int, *, is_up: bool = True, red_flags: list[str] | None = None
) -> dict[str, Any]:
    """Build a JSONB payload matching the shape gate in
    ``src/scripts/check_jsonb_shapes.py`` (required keys: ``score``,
    ``is_up``, ``tech_flags``, ``red_flags``).
    """
    return {
        "score": score,
        "is_up": is_up,
        "tech_flags": {"https": is_up, "mobile_viewport": is_up, "favicon": is_up},
        "red_flags": red_flags or [],
    }


# Each tuple drives a single row. Diacritic-rich names + addresses
# exercise the i18n / mojibake regression surface (see
# tests/test_i18n_outreach.py for the canonical guard).
_DEMO_LEADS: list[dict[str, Any]] = [
    {
        "unique_key": "_demo_001",
        "name": "Apartmani Maslina",
        "company_name": "Maslina Holiday d.o.o.",
        "website": "https://apartmani-maslina.demo.invalid",
        "email": "info@apartmani-maslina.demo.invalid",
        "phone": "+385 21 555 101",
        "address": "Šetalište 1. svibnja 12, 21000 Split",
        "rating": 4.7,
        "reviews": 128,
        "audit_status": "Completed",
        "seo_score": 72,
        "outreach_score": 65,
        "segment": "Vacation Rental",
        "key_offerings": "Vacation apartments, sea view, parking, Wi-Fi",
        "pain_points": "No online booking widget; outdated mobile layout",
        "high_risk_flag": False,
        "audit_results": _audit_results(72),
    },
    {
        "unique_key": "_demo_002",
        "name": "Restoran Konoba Mate",
        "company_name": "Konoba Mate j.d.o.o.",
        "website": "https://konoba-mate.demo.invalid",
        "email": "rezervacije@konoba-mate.demo.invalid",
        "phone": "+385 20 555 202",
        "address": "Trg Republike 5, 20000 Dubrovnik",
        "rating": 4.5,
        "reviews": 312,
        "audit_status": "Completed",
        "seo_score": 58,
        "outreach_score": 70,
        "segment": "Restaurant",
        "key_offerings": "Seafood, Dalmatian cuisine, terrace dining",
        "pain_points": "Menu PDF only; no Google Business hours",
        "high_risk_flag": False,
        "audit_results": _audit_results(58, red_flags=["no_schema_markup"]),
    },
    {
        "unique_key": "_demo_003",
        "name": "Stomatološka ordinacija Kovačević",
        "company_name": "Dr Kovačević d.o.o.",
        "website": "https://stomatolog-kovacevic.demo.invalid",
        "email": "ordinacija@stomatolog-kovacevic.demo.invalid",
        "phone": "+385 1 555 303",
        "address": "Vukovarska 17, 10000 Zagreb",
        "rating": 4.8,
        "reviews": 86,
        "audit_status": "Completed",
        "seo_score": 81,
        "outreach_score": 55,
        "segment": "Dentist",
        "key_offerings": "General dentistry, implants, orthodontics",
        "pain_points": "No appointment booking flow; thin About page",
        "high_risk_flag": False,
        "audit_results": _audit_results(81),
    },
    {
        "unique_key": "_demo_004",
        "name": "Apartmani Adriatic View",
        "company_name": "Adriatic View d.o.o.",
        "website": "https://adriaticview.demo.invalid",
        "email": "contact@adriaticview.demo.invalid",
        "phone": "+385 52 555 404",
        "address": "Petrićeva 23, 52100 Pula",
        "rating": 4.6,
        "reviews": 204,
        "audit_status": "Completed",
        "seo_score": 64,
        "outreach_score": 68,
        "segment": "Vacation Rental",
        "key_offerings": "Sea-view apartments, family-friendly, bike rentals",
        "pain_points": "Slow homepage (4.2s LCP); no structured data",
        "high_risk_flag": False,
        "audit_results": _audit_results(64, red_flags=["slow_lcp"]),
    },
    {
        "unique_key": "_demo_005",
        "name": "Villa Jadran",
        "company_name": "Jadran Holiday Homes d.o.o.",
        "website": "https://villa-jadran.demo.invalid",
        "email": "booking@villa-jadran.demo.invalid",
        "phone": "+385 20 555 505",
        "address": "Vlaha Bukovca 8, 20000 Dubrovnik",
        "rating": 4.9,
        "reviews": 47,
        "audit_status": "Completed",
        "seo_score": 88,
        "outreach_score": 78,
        "segment": "Luxury Rental",
        "key_offerings": "Luxury villa, pool, private chef, concierge",
        "pain_points": "No multilingual content (Croatian only)",
        "high_risk_flag": False,
        "audit_results": _audit_results(88),
    },
    {
        "unique_key": "_demo_006",
        "name": "Konoba Riblji Restoran",
        "company_name": "Riblji Restoran Zadar d.o.o.",
        "website": "https://riblji-restoran.demo.invalid",
        "email": "info@riblji-restoran.demo.invalid",
        "phone": "+385 23 555 606",
        "address": "Široka ulica 22, 23000 Zadar",
        "rating": 4.4,
        "reviews": 189,
        "audit_status": "Completed",
        "seo_score": 42,
        "outreach_score": 62,
        "segment": "Restaurant",
        "key_offerings": "Fresh seafood, daily catch, sunset views",
        "pain_points": "No mobile responsive design; broken contact form",
        "high_risk_flag": True,
        "audit_results": _audit_results(
            42, red_flags=["no_mobile_viewport", "broken_form"]
        ),
    },
    {
        "unique_key": "_demo_007",
        "name": "Salon ljepote Đurđica",
        "company_name": "Đurđica Beauty obrt",
        "website": "https://salon-djurdjica.demo.invalid",
        "email": "rezervacije@salon-djurdjica.demo.invalid",
        "phone": "+385 1 555 707",
        "address": "Ilica 128, 10000 Zagreb",
        "rating": 4.7,
        "reviews": 94,
        "audit_status": "Completed",
        "seo_score": 67,
        "outreach_score": 58,
        "segment": "Beauty",
        "key_offerings": "Hair styling, manicure, facials",
        "pain_points": "No service price list visible; weak SEO meta tags",
        "high_risk_flag": False,
        "audit_results": _audit_results(67),
    },
    {
        "unique_key": "_demo_008",
        "name": "Yoga Studio Mir",
        "company_name": "Mir Wellness j.d.o.o.",
        "website": "https://yoga-mir.demo.invalid",
        "email": "namaste@yoga-mir.demo.invalid",
        "phone": "+385 51 555 808",
        "address": "Korzo 14, 51000 Rijeka",
        "rating": 4.9,
        "reviews": 73,
        "audit_status": "Completed",
        "seo_score": 75,
        "outreach_score": 71,
        "segment": "Fitness",
        "key_offerings": "Hatha, vinyasa, prenatal yoga classes",
        "pain_points": "No class schedule on homepage; manual booking only",
        "high_risk_flag": False,
        "audit_results": _audit_results(75),
    },
    {
        "unique_key": "_demo_009",
        "name": "Pizzeria Trattoria Bella",
        "company_name": "Bella Hospitality d.o.o.",
        "website": "https://trattoria-bella.demo.invalid",
        "email": "ciao@trattoria-bella.demo.invalid",
        "phone": "+385 21 555 909",
        "address": "Marmontova 5, 21000 Split",
        "rating": 4.3,
        "reviews": 421,
        "audit_status": "Failed",
        "seo_score": None,
        "outreach_score": 40,
        "segment": "Restaurant",
        "key_offerings": "Wood-fired pizza, Italian classics, delivery",
        "pain_points": "Website returns 503; SSL certificate expired",
        "high_risk_flag": True,
        "last_error": "503 Service Unavailable",
        "audit_results": None,
    },
    {
        "unique_key": "_demo_010",
        "name": "Auto-škola Šumić",
        "company_name": "Šumić Driving d.o.o.",
        "website": "https://autoskola-sumic.demo.invalid",
        "email": "upisi@autoskola-sumic.demo.invalid",
        "phone": "+385 31 555 010",
        "address": "Trg Ante Starčevića 9, 31000 Osijek",
        "rating": 4.2,
        "reviews": 156,
        "audit_status": "Completed",
        "seo_score": 51,
        "outreach_score": 60,
        "segment": "Education",
        "key_offerings": "Driving lessons, B-category training, theory prep",
        "pain_points": "No online enrollment; brochure-style site",
        "high_risk_flag": False,
        "audit_results": _audit_results(51, red_flags=["no_meta_description"]),
    },
    {
        "unique_key": "_demo_011",
        "name": "Frizerski salon Žika",
        "company_name": "Žika Frizer obrt",
        "website": "https://salon-zika.demo.invalid",
        "email": "narudzbe@salon-zika.demo.invalid",
        "phone": "+385 52 555 111",
        "address": "Flanatička 11, 52100 Pula",
        "rating": 4.6,
        "reviews": 62,
        "audit_status": "Pending",
        "seo_score": None,
        "outreach_score": None,
        "segment": "Beauty",
        "key_offerings": "Men's grooming, beard styling, classic cuts",
        "pain_points": None,
        "high_risk_flag": False,
        "audit_results": None,
    },
    {
        "unique_key": "_demo_012",
        "name": "Caffe Bar Korčula",
        "company_name": "Korčula Hospitality j.d.o.o.",
        "website": "https://caffebar-korcula.demo.invalid",
        "email": "hello@caffebar-korcula.demo.invalid",
        "phone": "+385 20 555 212",
        "address": "Plokata 19, 20260 Korčula",
        "rating": 4.5,
        "reviews": 211,
        "audit_status": "Completed",
        "seo_score": 60,
        "outreach_score": 64,
        "segment": "Hospitality",
        "key_offerings": "Specialty coffee, craft cocktails, sea-front terrace",
        "pain_points": "No Instagram link; thin homepage copy",
        "high_risk_flag": False,
        "audit_results": _audit_results(60),
    },
    {
        "unique_key": "_demo_013",
        "name": "Apartmani Mediterana",
        "company_name": "Mediterana Property d.o.o.",
        "website": "https://apartmani-mediterana.demo.invalid",
        "email": "rezervacije@apartmani-mediterana.demo.invalid",
        "phone": "+385 22 555 313",
        "address": "Obala dr. Franje Tuđmana 4, 22000 Šibenik",
        "rating": 4.4,
        "reviews": 98,
        "audit_status": "Failed",
        "seo_score": None,
        "outreach_score": 38,
        "segment": "Vacation Rental",
        "key_offerings": "Old-town apartments, walking distance to cathedral",
        "pain_points": "Domain unreachable; assumed offline",
        "high_risk_flag": True,
        "last_error": "Timeout",
        "audit_results": None,
    },
    {
        "unique_key": "_demo_014",
        "name": "Optika Vid",
        "company_name": "Vid Optika d.o.o.",
        "website": "https://optika-vid.demo.invalid",
        "email": "narudzbe@optika-vid.demo.invalid",
        "phone": "+385 51 555 414",
        "address": "Riva 10, 51000 Rijeka",
        "rating": 4.5,
        "reviews": 71,
        "audit_status": "Completed",
        "seo_score": 73,
        "outreach_score": 66,
        "segment": "Retail",
        "key_offerings": "Prescription glasses, contact lenses, eye exams",
        "pain_points": "No appointment scheduler; pricing requires phone call",
        "high_risk_flag": False,
        "audit_results": _audit_results(73),
    },
    {
        "unique_key": "_demo_015",
        "name": "Restoran Dalmatino",
        "company_name": "Dalmatino Fine Dining d.o.o.",
        "website": "https://restoran-dalmatino.demo.invalid",
        "email": "rezervacije@restoran-dalmatino.demo.invalid",
        "phone": "+385 21 555 515",
        "address": "Bosanska 6, 21000 Split",
        "rating": 4.8,
        "reviews": 567,
        "audit_status": "Completed",
        "seo_score": 79,
        "outreach_score": 74,
        "segment": "Restaurant",
        "key_offerings": "Fine dining, Mediterranean tasting menu, sommelier",
        "pain_points": "Reservation page hidden 3 clicks deep",
        "high_risk_flag": False,
        "audit_results": _audit_results(79),
    },
    {
        "unique_key": "_demo_016",
        "name": "Stomatolog dr Šarić",
        "company_name": "Šarić Dental Clinic d.o.o.",
        "website": "https://stomatolog-saric.demo.invalid",
        "email": "info@stomatolog-saric.demo.invalid",
        "phone": "+385 52 555 616",
        "address": "Giardini 3, 52100 Pula",
        "rating": 4.7,
        "reviews": 102,
        "audit_status": "Completed",
        "seo_score": 69,
        "outreach_score": 63,
        "segment": "Dentist",
        "key_offerings": "Cosmetic dentistry, dental implants, pediatric care",
        "pain_points": "Outdated team photos; no service breakdown by treatment",
        "high_risk_flag": False,
        "audit_results": _audit_results(69),
    },
    {
        "unique_key": "_demo_017",
        "name": "Plovi Hrvatska Tours",
        "company_name": "Plovi Hrvatska d.o.o.",
        "website": "https://plovi-hrvatska.demo.invalid",
        "email": "tours@plovi-hrvatska.demo.invalid",
        "phone": "+385 20 555 717",
        "address": "Pile 1, 20000 Dubrovnik",
        "rating": 4.9,
        "reviews": 318,
        "audit_status": "Completed",
        "seo_score": 84,
        "outreach_score": 80,
        "segment": "Tour Operator",
        "key_offerings": "Day cruises, island hopping, Game of Thrones tours",
        "pain_points": "Inventory not synced — tours marked available are sold out",
        "high_risk_flag": False,
        "audit_results": _audit_results(84),
    },
    {
        "unique_key": "_demo_018",
        "name": "Studio Pilates Lana",
        "company_name": "Lana Pilates obrt",
        "website": "https://pilates-lana.demo.invalid",
        "email": "lana@pilates-lana.demo.invalid",
        "phone": "+385 1 555 818",
        "address": "Savska cesta 41, 10000 Zagreb",
        "rating": 4.8,
        "reviews": 54,
        "audit_status": "Pending",
        "seo_score": None,
        "outreach_score": None,
        "segment": "Fitness",
        "key_offerings": "Reformer pilates, mat classes, prenatal sessions",
        "pain_points": None,
        "high_risk_flag": False,
        "audit_results": None,
    },
    {
        "unique_key": "_demo_019",
        "name": "Apartmani Sunčani Hvar",
        "company_name": "Sunčani Hvar Property d.o.o.",
        "website": "https://suncani-hvar.demo.invalid",
        "email": "info@suncani-hvar.demo.invalid",
        "phone": "+385 21 555 919",
        "address": "Riva 17, 21450 Hvar",
        "rating": 4.6,
        "reviews": 142,
        "audit_status": "Completed",
        "seo_score": 56,
        "outreach_score": 61,
        "segment": "Vacation Rental",
        "key_offerings": "Boutique apartments, harbor views, nightlife adjacent",
        "pain_points": "Booking calendar opens in separate iframe; conversion drop",
        "high_risk_flag": False,
        "audit_results": _audit_results(56, red_flags=["iframe_booking"]),
    },
    {
        "unique_key": "_demo_020",
        "name": "Foto Studio Marin",
        "company_name": "Marin Photography obrt",
        "website": "https://foto-marin.demo.invalid",
        "email": "shoot@foto-marin.demo.invalid",
        "phone": "+385 21 555 020",
        "address": "Tolstojeva 2, 21000 Split",
        "rating": 4.9,
        "reviews": 38,
        "audit_status": "Pending",
        "seo_score": None,
        "outreach_score": None,
        "segment": "Photography",
        "key_offerings": "Wedding photography, portraits, branding shoots",
        "pain_points": None,
        "high_risk_flag": False,
        "audit_results": None,
    },
]


def _enrich_row(row: dict[str, Any]) -> dict[str, Any]:
    """Apply demo-wide defaults so the per-row dicts stay readable."""
    now_iso = datetime.now(timezone.utc).isoformat()
    return {
        **row,
        "lead_source": "_demo_",
        "is_demo": True,
        "enrichment_status": "COMPLETED"
        if row["audit_status"] == "Completed"
        else "PENDING",
        "needs_manual_review": False,
        "retry_count": 0,
        "created_at": now_iso,
        "updated_at": now_iso,
    }


def main() -> int:
    db = SupabaseHelper()
    if not db.client:
        logger.error(
            "Supabase client not configured — set SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY"
        )
        return 2

    rows = [_enrich_row(r) for r in _DEMO_LEADS]
    logger.info("Seeding %d demo leads (idempotent: ignore_duplicates=True)", len(rows))

    try:
        result = (
            db.client.table("leads")
            .upsert(rows, on_conflict="unique_key", ignore_duplicates=True)
            .execute()
        )
    except Exception as exc:
        logger.exception("Upsert failed: %s", exc)
        return 3

    inserted = len(result.data or [])
    skipped = len(rows) - inserted
    logger.info("Done — %d inserted, %d already present", inserted, skipped)
    logger.info(
        "Verify with: SELECT COUNT(*) FROM leads WHERE is_demo = TRUE;  -- expect >= %d",
        len(rows),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
