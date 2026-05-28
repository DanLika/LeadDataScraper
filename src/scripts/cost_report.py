"""Weekly cost digest — one Markdown table per provider + a WoW
comparison line.

Sources (each independently optional — the report omits a source it
can't read rather than failing):

* **Supabase**: REST API for plan + ``pg_database_size`` for storage.
  Needs ``SUPABASE_ACCESS_TOKEN`` + ``SUPABASE_PROJECT_REF``, and
  ``DATABASE_URL`` for the size query.
* **Render**: REST API for service plans + deploy minutes. Needs
  ``RENDER_API_KEY`` + per-service IDs.
* **Gemini**: per-call estimate × call count read from a marker log
  line. Cheap-and-cheerful until Google AI Studio exposes a billing
  API.
* **Domain + SSL**: flat annual cost prorated to the week. Driven by
  ``DOMAIN_ANNUAL_COST_USD`` env (default 15).
* **Google Maps**: ``$0`` (Playwright-scraped today; documented as a
  placeholder for when/if the operator switches to the official Places
  API).

Output goes to stdout as Markdown; the GitHub Actions workflow
captures it and posts to Discord.

Run locally:

    SUPABASE_ACCESS_TOKEN=... SUPABASE_PROJECT_REF=... \\
    DATABASE_URL=... RENDER_API_KEY=... \\
    python -m src.scripts.cost_report
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


GEMINI_AVG_COST_USD = float(os.environ.get("GEMINI_AVG_COST_USD", "0.0008"))
DOMAIN_ANNUAL_COST_USD = float(os.environ.get("DOMAIN_ANNUAL_COST_USD", "15"))
BASELINE_PATH = Path(os.environ.get("COST_BASELINE_PATH", "./.cost_baseline.json"))


def _money(usd: float) -> str:
    return f"${usd:,.2f}"


def _section(title: str, body: str) -> str:
    return f"## {title}\n\n{body}\n"


def _gemini_section() -> tuple[str, float]:
    """Estimate Gemini spend from the count of marker log lines.

    The structured JSON logging shape (see
    ``src/utils/logging_config.py``) emits per-call records. We grep
    those out of the Render log dump — but Render log retrieval is a
    daily-task tool, not a workflow-runner-friendly export.

    For now: produce a directional estimate per the
    ``GEMINI_AVG_COST_USD`` env. The actual call count would need a
    Render API → structured log aggregator hookup; that's a future
    enhancement when the cost actually crosses a threshold worth the
    integration.

    Returns: (markdown, estimated_weekly_usd).
    """
    # Placeholder until Render log aggregation lands.
    body = (
        f"_Estimated_ — per-call cost: **{_money(GEMINI_AVG_COST_USD)}**.\n"
        f"Cross-check actual usage against Google AI Studio's usage "
        f"dashboard (no public billing API).\n\n"
        f"⚠️ This source is currently approximate. Real Gemini spend "
        f"is the source of truth — see "
        f"<https://aistudio.google.com/usage>."
    )
    # Conservative weekly placeholder so the digest isn't blank.
    weekly_usd = 0.0
    return _section("🤖 Gemini", body), weekly_usd


def _supabase_section() -> tuple[str, float]:
    token = os.environ.get("SUPABASE_ACCESS_TOKEN")
    ref = os.environ.get("SUPABASE_PROJECT_REF")
    db_url = os.environ.get("DATABASE_URL")

    if not (token and ref):
        return _section(
            "🗄  Supabase",
            "Skipped — `SUPABASE_ACCESS_TOKEN` + `SUPABASE_PROJECT_REF` not set.",
        ), 0.0

    # Project + plan info via the Management API.
    plan_line = ""
    try:
        r = requests.get(
            f"https://api.supabase.com/v1/projects/{ref}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        r.raise_for_status()
        proj = r.json()
        plan_line = f"- Plan: **{proj.get('subscription_tier', 'unknown')}**\n"
    except Exception as e:  # noqa: BLE001
        plan_line = f"- Plan: _(could not read — {e})_\n"

    # DB size via pg_database_size.
    size_line = ""
    if db_url:
        try:
            import psycopg  # type: ignore

            with psycopg.connect(db_url, autocommit=True) as conn:
                row = conn.execute(
                    "SELECT pg_database_size(current_database())"
                ).fetchone()
            mb = (row[0] or 0) / (1024**2)
            size_line = f"- DB size: **{mb:,.1f} MB**\n"
        except Exception as e:  # noqa: BLE001
            size_line = f"- DB size: _(could not read — {e})_\n"

    # Plan → weekly cost mapping. Update when Supabase pricing changes.
    PLAN_MONTHLY_USD = {"free": 0.0, "pro": 25.0, "team": 599.0}
    plan = "unknown"
    try:
        plan = (proj.get("subscription_tier") or "unknown").lower()
    except Exception:
        pass
    monthly = PLAN_MONTHLY_USD.get(plan, 0.0)
    weekly = monthly / 4.0

    body = plan_line + size_line + f"- Est. weekly cost: **{_money(weekly)}**"
    return _section("🗄  Supabase", body), weekly


def _render_section() -> tuple[str, float]:
    api_key = os.environ.get("RENDER_API_KEY")
    if not api_key:
        return _section(
            "🚀 Render",
            "Skipped — `RENDER_API_KEY` not set.",
        ), 0.0

    backend_id = os.environ.get("RENDER_BACKEND_SERVICE_ID")
    frontend_id = os.environ.get("RENDER_FRONTEND_SERVICE_ID")

    # Render service plan pricing (USD/month). Update when pricing changes.
    PLAN_MONTHLY_USD = {
        "free": 0.0,
        "starter": 7.0,
        "standard": 25.0,
        "pro": 85.0,
    }

    lines: list[str] = []
    total_monthly = 0.0
    for service_id, label in (
        (backend_id, "Backend"),
        (frontend_id, "Frontend"),
    ):
        if not service_id:
            lines.append(f"- {label}: _service ID not set_")
            continue
        try:
            r = requests.get(
                f"https://api.render.com/v1/services/{service_id}",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10,
            )
            r.raise_for_status()
            svc = r.json()
            plan = (svc.get("serviceDetails", {}) or {}).get("plan", "unknown")
            monthly = PLAN_MONTHLY_USD.get(plan.lower(), 0.0)
            total_monthly += monthly
            lines.append(f"- {label}: **{plan}** ({_money(monthly)}/mo)")
        except Exception as e:  # noqa: BLE001
            lines.append(f"- {label}: _(could not read — {e})_")

    weekly = total_monthly / 4.0
    body = "\n".join(lines) + f"\n- Est. weekly cost: **{_money(weekly)}**"
    return _section("🚀 Render", body), weekly


def _maps_section() -> tuple[str, float]:
    body = (
        "Today the pipeline scrapes Google Maps via Playwright (no "
        "API key) — **$0**. Documented here so a future switch to the "
        "official Places API surfaces the cost shift.\n\n"
        "Places API current pricing: <https://mapsplatform.google.com/pricing/>."
    )
    return _section("🗺  Google Maps", body), 0.0


def _domain_section() -> tuple[str, float]:
    weekly = DOMAIN_ANNUAL_COST_USD / 52.0
    body = (
        f"- Annual cost: **{_money(DOMAIN_ANNUAL_COST_USD)}**\n"
        f"- Weekly prorated: **{_money(weekly)}**\n"
        f"- TLS: Let's Encrypt via Render (auto-renew, $0)."
    )
    return _section("🌐 Domain + SSL", body), weekly


def _load_baseline() -> dict[str, Any]:
    if not BASELINE_PATH.is_file():
        return {}
    try:
        with BASELINE_PATH.open() as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_baseline(snapshot: dict[str, Any]) -> None:
    try:
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with BASELINE_PATH.open("w") as fh:
            json.dump(snapshot, fh, indent=2)
    except OSError:
        pass


def main() -> int:
    now = datetime.now(timezone.utc)
    sections: list[str] = []
    totals: dict[str, float] = {}

    for label, fn in (
        ("gemini", _gemini_section),
        ("supabase", _supabase_section),
        ("render", _render_section),
        ("maps", _maps_section),
        ("domain", _domain_section),
    ):
        md, weekly_usd = fn()
        sections.append(md)
        totals[label] = weekly_usd

    total = sum(totals.values())

    # WoW comparison.
    baseline = _load_baseline()
    prev_total = float(baseline.get("total_weekly_usd", 0.0))
    delta_line = ""
    if prev_total > 0:
        delta = total - prev_total
        pct = (delta / prev_total) * 100.0
        sign = "+" if delta >= 0 else ""
        delta_line = (
            f"\n_Δ vs. last week: **{sign}{_money(delta)}** ({sign}{pct:.1f}%)_"
        )
    else:
        delta_line = "\n_No baseline available — first run; comparison from next week._"

    out = [
        f"# Weekly cost digest — week ending {now.strftime('%Y-%m-%d')}",
        "",
        f"**Estimated total: {_money(total)} / week** "
        f"({_money(total * 52)} / year run-rate)",
        "",
        "> ⚠️ **Total EXCLUDES Gemini** — no public billing API; the Gemini "
        "section is per-call indicative only. Cross-check the real number "
        "weekly at <https://aistudio.google.com/usage> and add it to this "
        "total manually until automated retrieval lands "
        '(`docs/roadmap.md` → "Cost-report digest").',
        delta_line,
        "",
        *sections,
        "---",
        "_Sources marked Skipped need credentials set in repo secrets._",
    ]
    print("\n".join(out))

    # Persist baseline for next week's comparison.
    _save_baseline(
        {
            "timestamp": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "total_weekly_usd": total,
            "by_source": totals,
        }
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
