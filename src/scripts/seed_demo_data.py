"""Seed 20 demo Croatian leads for dogfood + onboarding screenshots.

Idempotent — each row carries a stable `unique_key` (`_demo_<slug>`),
which is the table's UNIQUE column, so `upsert_leads()` falls into an
ON CONFLICT DO UPDATE that leaves the row equivalent. Safe to re-run.

Every seeded row carries `is_demo=True` so the dashboard filter +
admin "Remove all demo data" path can scope them. `lead_source='_demo_'`
double-marks them in case a future query loses the boolean flag.

Run:
    python -m src.scripts.seed_demo_data
    SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... python -m src.scripts.seed_demo_data

Exit codes:
    0 = upsert succeeded (any data path)
    1 = upsert refused or returned mismatched row count
    2 = misconfigured run (missing env, no SupabaseHelper client)

Removal (when dogfood ends): hit `DELETE /leads/clear-demo` from the UI
(Settings → "Remove all demo data") or directly:
    curl -X DELETE -H 'X-API-Key: ...' -H 'X-Admin-Token: ...' \\
      https://<backend>/leads/clear-demo
"""
from __future__ import annotations

import sys
from typing import Final

from src.utils.supabase_helper import SupabaseHelper

# 20 fictional Croatian businesses across 4 segments × 4-5 cities.
# Names use common Croatian surnames + descriptors so they read plausibly
# without overlapping any real registered business. Phones are valid +385
# country code with realistic city dialing prefixes (1=Zagreb, 21=Split,
# 20=Dubrovnik, 51=Rijeka, 53=Plitvice/Lika).
DEMO_LEADS: Final[list[dict]] = [
    # ── Vacation rentals (5) ──────────────────────────────────────
    {
        "unique_key": "_demo_kovacevic_apartments_zg",
        "name": "Kovačević Apartments Zagreb",
        "website": "https://kovacevic-apartments.example.hr",
        "phone": "+385 1 555 0101",
        "address": "Ilica 234, 10000 Zagreb",
        "segment": "Vacation Rental",
        "first_name": "Ivana",
        "company_name": "Kovačević Apartments d.o.o.",
    },
    {
        "unique_key": "_demo_villa_jadran_split",
        "name": "Villa Jadran Split",
        "website": "https://villa-jadran.example.hr",
        "email": "info@villa-jadran.example.hr",
        "phone": "+385 21 555 0102",
        "address": "Šetalište Bačvice 12, 21000 Split",
        "segment": "Vacation Rental",
        "first_name": "Marko",
        "company_name": "Villa Jadran d.o.o.",
    },
    {
        "unique_key": "_demo_dubrovnik_old_town_rooms",
        "name": "Dubrovnik Old Town Rooms",
        "website": "https://oldtown-dubrovnik.example.hr",
        "phone": "+385 20 555 0103",
        "address": "Stradun 8, 20000 Dubrovnik",
        "segment": "Vacation Rental",
        "first_name": "Petra",
        "company_name": "Old Town Rooms d.o.o.",
    },
    {
        "unique_key": "_demo_rijeka_seaside_house",
        "name": "Rijeka Seaside House",
        "website": "https://rijeka-seaside.example.hr",
        "email": "booking@rijeka-seaside.example.hr",
        "phone": "+385 51 555 0104",
        "address": "Korzo 22, 51000 Rijeka",
        "segment": "Vacation Rental",
        "first_name": "Tomislav",
        "company_name": "Seaside Holdings d.o.o.",
    },
    {
        "unique_key": "_demo_plitvice_cabin",
        "name": "Plitvice Forest Cabin",
        "website": "https://plitvice-cabin.example.hr",
        "phone": "+385 53 555 0105",
        "address": "Plitvička Jezera 14, 53231 Plitvička Jezera",
        "segment": "Vacation Rental",
        "first_name": "Ana",
        "company_name": "Plitvice Cabin Rentals d.o.o.",
    },
    # ── Restaurants (5) ───────────────────────────────────────────
    {
        "unique_key": "_demo_konoba_dalmatino_split",
        "name": "Konoba Dalmatino",
        "website": "https://konoba-dalmatino.example.hr",
        "email": "rezervacije@konoba-dalmatino.example.hr",
        "phone": "+385 21 555 0201",
        "address": "Marmontova 18, 21000 Split",
        "segment": "Restaurant",
        "first_name": "Josip",
        "company_name": "Konoba Dalmatino j.d.o.o.",
    },
    {
        "unique_key": "_demo_restoran_zagreb_centar",
        "name": "Restoran Zagreb Centar",
        "website": "https://restoran-zagreb.example.hr",
        "phone": "+385 1 555 0202",
        "address": "Tkalčićeva 45, 10000 Zagreb",
        "segment": "Restaurant",
        "first_name": "Mirjana",
        "company_name": "Zagreb Centar d.o.o.",
    },
    {
        "unique_key": "_demo_taverna_dubrovnik",
        "name": "Taverna Stari Grad",
        "website": "https://taverna-starigrad.example.hr",
        "email": "kontakt@taverna-starigrad.example.hr",
        "phone": "+385 20 555 0203",
        "address": "Prijeko 4, 20000 Dubrovnik",
        "segment": "Restaurant",
        "first_name": "Luka",
        "company_name": "Taverna Stari Grad d.o.o.",
    },
    {
        "unique_key": "_demo_riva_grill_rijeka",
        "name": "Riva Grill Rijeka",
        "website": "https://riva-grill.example.hr",
        "phone": "+385 51 555 0204",
        "address": "Adamićeva 6, 51000 Rijeka",
        "segment": "Restaurant",
        "first_name": "Branka",
        "company_name": "Riva Grill d.o.o.",
    },
    {
        "unique_key": "_demo_zagreb_pivnica",
        "name": "Zagrebačka Pivnica",
        "website": "https://zg-pivnica.example.hr",
        "email": "narudzbe@zg-pivnica.example.hr",
        "phone": "+385 1 555 0205",
        "address": "Savska cesta 88, 10000 Zagreb",
        "segment": "Restaurant",
        "first_name": "Stipe",
        "company_name": "Zagrebačka Pivnica d.o.o.",
    },
    # ── Dental practices (5) ──────────────────────────────────────
    {
        "unique_key": "_demo_dr_horvat_dental_zg",
        "name": "Dr. Horvat Dental Studio",
        "website": "https://horvat-dental.example.hr",
        "email": "ordinacija@horvat-dental.example.hr",
        "phone": "+385 1 555 0301",
        "address": "Vlaška 102, 10000 Zagreb",
        "segment": "Dental Practice",
        "first_name": "Dr. Goran",
        "company_name": "Dr. Horvat Dental Studio d.o.o.",
    },
    {
        "unique_key": "_demo_split_dental_centar",
        "name": "Split Dental Centar",
        "website": "https://split-dental.example.hr",
        "phone": "+385 21 555 0302",
        "address": "Velebitska 24, 21000 Split",
        "segment": "Dental Practice",
        "first_name": "Dr. Nataša",
        "company_name": "Split Dental Centar d.o.o.",
    },
    {
        "unique_key": "_demo_dubrovnik_implant_clinic",
        "name": "Dubrovnik Implant Clinic",
        "website": "https://dubrovnik-implants.example.hr",
        "email": "info@dubrovnik-implants.example.hr",
        "phone": "+385 20 555 0303",
        "address": "Branitelja Dubrovnika 41, 20000 Dubrovnik",
        "segment": "Dental Practice",
        "first_name": "Dr. Mladen",
        "company_name": "Adriatic Implants d.o.o.",
    },
    {
        "unique_key": "_demo_rijeka_smile_studio",
        "name": "Rijeka Smile Studio",
        "website": "https://rijeka-smile.example.hr",
        "phone": "+385 51 555 0304",
        "address": "Trg Republike Hrvatske 8, 51000 Rijeka",
        "segment": "Dental Practice",
        "first_name": "Dr. Sanja",
        "company_name": "Smile Studio d.o.o.",
    },
    {
        "unique_key": "_demo_zg_orto_centar",
        "name": "Zagreb Ortodoncija Centar",
        "website": "https://zg-ortodoncija.example.hr",
        "email": "narudzbe@zg-ortodoncija.example.hr",
        "phone": "+385 1 555 0305",
        "address": "Maksimirska 47, 10000 Zagreb",
        "segment": "Dental Practice",
        "first_name": "Dr. Antonio",
        "company_name": "ZG Ortodoncija d.o.o.",
    },
    # ── Gyms / fitness (5) ────────────────────────────────────────
    {
        "unique_key": "_demo_fitness_arena_zg",
        "name": "Fitness Arena Zagreb",
        "website": "https://fitness-arena.example.hr",
        "phone": "+385 1 555 0401",
        "address": "Slavonska avenija 26, 10000 Zagreb",
        "segment": "Fitness Gym",
        "first_name": "Mihael",
        "company_name": "Fitness Arena d.o.o.",
    },
    {
        "unique_key": "_demo_crossfit_split",
        "name": "CrossFit Adriatic Split",
        "website": "https://crossfit-adriatic.example.hr",
        "email": "info@crossfit-adriatic.example.hr",
        "phone": "+385 21 555 0402",
        "address": "Domovinskog rata 81, 21000 Split",
        "segment": "Fitness Gym",
        "first_name": "Dario",
        "company_name": "CrossFit Adriatic d.o.o.",
    },
    {
        "unique_key": "_demo_pilates_studio_rijeka",
        "name": "Pilates Studio Rijeka",
        "website": "https://pilates-rijeka.example.hr",
        "phone": "+385 51 555 0403",
        "address": "Krešimirova 30, 51000 Rijeka",
        "segment": "Fitness Gym",
        "first_name": "Lana",
        "company_name": "Pilates Studio d.o.o.",
    },
    {
        "unique_key": "_demo_dubrovnik_yoga_loft",
        "name": "Dubrovnik Yoga Loft",
        "website": "https://dubrovnik-yoga.example.hr",
        "email": "namaste@dubrovnik-yoga.example.hr",
        "phone": "+385 20 555 0404",
        "address": "Lapadska obala 19, 20000 Dubrovnik",
        "segment": "Fitness Gym",
        "first_name": "Marija",
        "company_name": "Yoga Loft d.o.o.",
    },
    {
        "unique_key": "_demo_zg_boxing_club",
        "name": "Zagreb Boxing Club",
        "website": "https://zg-boxing.example.hr",
        "phone": "+385 1 555 0405",
        "address": "Heinzelova 33, 10000 Zagreb",
        "segment": "Fitness Gym",
        "first_name": "Robert",
        "company_name": "ZG Boxing Club d.o.o.",
    },
]


def _enrich_with_flags(rows: list[dict]) -> list[dict]:
    """Stamp every demo row with is_demo + lead_source. Centralised here so
    a caller can't forget the flag and accidentally seed an indistinguishable
    row.
    """
    out: list[dict] = []
    for r in rows:
        row = dict(r)
        row["is_demo"] = True
        row["lead_source"] = "_demo_"
        row.setdefault("audit_status", "Pending")
        out.append(row)
    return out


def main() -> int:
    db = SupabaseHelper()
    if not db.client:
        print(
            "ERROR: Supabase client unavailable. Set SUPABASE_URL + "
            "SUPABASE_SERVICE_ROLE_KEY in the env.",
            file=sys.stderr,
        )
        return 2

    rows = _enrich_with_flags(DEMO_LEADS)
    expected = len(rows)
    print(f"Seeding {expected} demo leads (is_demo=True, lead_source='_demo_')...")
    result = db.upsert_leads(rows)
    if result is None:
        print("ERROR: upsert_leads returned None (client missing or refused).",
              file=sys.stderr)
        return 1

    actual = len(getattr(result, "data", None) or [])
    print(f"Upsert returned {actual} rows.")
    if actual != expected:
        print(
            f"WARNING: expected {expected}, got {actual} — partial reject? "
            "Inspect Supabase logs (RLS, CHECK violation, missing column).",
            file=sys.stderr,
        )
        return 1
    print("Demo seed complete. Visit the dashboard, toggle 'Hide demo data' "
          "in the filter bar to verify.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
