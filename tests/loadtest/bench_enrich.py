"""Benchmark EnrichmentEngine.enrich_lead over 20 real leads.

Pulls up to N rows from Supabase that have a website URL set, runs
`enricher.enrich_lead` on each via `asyncio.gather` (same pattern the
orchestrator uses inside `_process_and_upsert_chunk`), and writes timing
JSON to `tests/loadtest/reports/bench_enrich_<label>.json`.

Run BEFORE the browser-pool refactor (label `baseline`), then AFTER
(label `pool`) and diff the totals:

    python -m tests.loadtest.bench_enrich --label baseline --no-ai
    # ... apply refactor ...
    python -m tests.loadtest.bench_enrich --label pool --no-ai

`--no-ai` monkeypatches `deep_ai_parse` to a noop so the timing isolates
browser/network cost — that is the cost the pool refactor changes.
Gemini round-trip is ~1-2s/lead and unaffected; mixing it in muddies the
delta. Pass `--with-ai` to include the AI step for end-to-end numbers.

Required env: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, GEMINI_API_KEY
(GEMINI_API_KEY only enforced by EnrichmentEngine constructor — set any
non-empty value for `--no-ai` runs).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
from pathlib import Path
from typing import Any, Dict, List

# Ensure repo root is importable when invoked as a script.
import sys

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.scrapers.enrichment_engine import EnrichmentEngine  # noqa: E402
from src.utils.supabase_helper import SupabaseHelper  # noqa: E402


def _fetch_leads(limit: int) -> List[Dict[str, Any]]:
    """Pull rows with a non-null website. Service-role key bypasses RLS so
    this works against the same backend Supabase instance."""
    db = SupabaseHelper()
    if not db.client:
        raise RuntimeError(
            "Supabase client not configured. Export SUPABASE_URL and "
            "SUPABASE_SERVICE_ROLE_KEY before invoking the benchmark."
        )
    resp = (
        db.client.table("leads")
        .select("unique_key,name,website,about_url,team_url,clients_url")
        .not_.is_("website", "null")
        .limit(limit)
        .execute()
    )
    rows = resp.data or []
    return [r for r in rows if r.get("website")]


async def _time_one(engine: EnrichmentEngine, lead: Dict[str, Any]) -> Dict[str, Any]:
    start = time.perf_counter()
    try:
        await engine.enrich_lead(dict(lead))
        ok = True
        err = None
    except Exception as exc:  # noqa: BLE001 — benchmark wants the raw error
        ok = False
        err = repr(exc)
    elapsed_ms = (time.perf_counter() - start) * 1000
    return {
        "unique_key": lead.get("unique_key"),
        "website": lead.get("website"),
        "ms": round(elapsed_ms, 1),
        "ok": ok,
        "error": err,
    }


async def _bench(label: str, limit: int, with_ai: bool) -> Dict[str, Any]:
    leads = _fetch_leads(limit)
    if not leads:
        raise RuntimeError(
            "No leads with a website URL found. Run the discovery flow "
            "to populate rows before benchmarking."
        )

    engine = EnrichmentEngine()

    if not with_ai:
        # Skip the Gemini call so the timing reflects browser+network only.
        # Returns {} so enrich_lead lands in the FAILED_NO_CONTENT branch or
        # COMPLETED-with-empty-update — neither affects browser cost.
        async def _noop(*_args, **_kwargs):
            return {}

        engine.deep_ai_parse = _noop  # type: ignore[assignment]

    print(f"[bench] label={label!r} leads={len(leads)} ai={'on' if with_ai else 'off'}")
    overall_start = time.perf_counter()
    results = await asyncio.gather(
        *(_time_one(engine, lead) for lead in leads),
        return_exceptions=False,
    )

    # If the engine grew an aclose() in the refactor, call it. Bench tolerates
    # both shapes so the same script runs against baseline and pooled engines.
    aclose = getattr(engine, "aclose", None)
    if callable(aclose):
        try:
            await aclose()
        except Exception as exc:  # noqa: BLE001
            print(f"[bench] aclose() raised: {exc!r}")

    total_ms = (time.perf_counter() - overall_start) * 1000

    per_lead_ms = [r["ms"] for r in results if r["ok"]]
    p50 = statistics.median(per_lead_ms) if per_lead_ms else None
    p95 = (
        statistics.quantiles(per_lead_ms, n=20)[18]
        if len(per_lead_ms) >= 2
        else (per_lead_ms[0] if per_lead_ms else None)
    )
    failures = sum(1 for r in results if not r["ok"])

    summary = {
        "label": label,
        "with_ai": with_ai,
        "count": len(results),
        "failures": failures,
        "total_wall_ms": round(total_ms, 1),
        "per_lead_p50_ms": round(p50, 1) if p50 is not None else None,
        "per_lead_p95_ms": round(p95, 1) if p95 is not None else None,
        "per_lead": results,
    }

    out_dir = _REPO_ROOT / "tests" / "loadtest" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"bench_enrich_{label}.json"
    out_path.write_text(json.dumps(summary, indent=2))

    print(
        f"[bench] total_wall={summary['total_wall_ms']}ms  "
        f"p50/lead={summary['per_lead_p50_ms']}ms  "
        f"p95/lead={summary['per_lead_p95_ms']}ms  "
        f"failures={failures}  -> {out_path}"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True, help="e.g. baseline | pool")
    parser.add_argument("--limit", type=int, default=20)
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--no-ai", dest="with_ai", action="store_false", default=False)
    g.add_argument("--with-ai", dest="with_ai", action="store_true")
    args = parser.parse_args()

    asyncio.run(_bench(args.label, args.limit, args.with_ai))


if __name__ == "__main__":
    main()
