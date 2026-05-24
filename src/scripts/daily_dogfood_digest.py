"""Daily dogfood digest — one Markdown summary per signal, posted to
Discord every morning during the 2-week dogfood (roadmap 13.15).

Goal: catch pipeline regressions and budget surprises early.

Sources today (each independently optional — the digest omits a source
it can't read rather than failing):

* **Leads** — ``SELECT count(*)`` + per-status breakdown + last-24h adds
* **Storage** — ``pg_database_size`` (same source as ``cost_report.py``)
* **Gemini budget** — HTTP GET against ``/admin/gemini-budget`` on the
  live backend (the budget state lives in a SQLite file on Render's
  filesystem; in-process import on the GH runner would read an empty
  fresh file and lie about usage)
* **Orchestration jobs** — last-24h status breakdown
* **Orphans/zombies** — count from the auto-heal sweep
  (``check_orphans_and_zombies.py``)

Output goes to stdout as Markdown; the GitHub Actions workflow captures
it and posts to Discord. **No day-over-day delta** — GH Actions runner
filesystem is ephemeral and we deliberately don't pay the
``actions/cache`` complexity for a 2-week experiment. Absolute numbers
+ daily-log entries are sufficient signal.

Run locally:

    DATABASE_URL=... BACKEND_URL=... API_SECRET_KEY=... \\
    python -m src.scripts.daily_dogfood_digest

See [`docs/dogfood-plan-2026-05.md`](../../docs/dogfood-plan-2026-05.md)
§4 for the operator routine that consumes this output.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

import requests


def _section(title: str, body: str) -> str:
    return f"## {title}\n\n{body}\n"


def _connect():
    """Return a psycopg connection or raise. Caller wraps in try/except."""
    import psycopg  # type: ignore

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg.connect(db_url, autocommit=True)


def _leads_section() -> str:
    """Total lead count, per audit_status, and last-24h adds."""
    try:
        with _connect() as conn:
            total = conn.execute("SELECT count(*) FROM leads").fetchone()[0] or 0
            by_status = conn.execute(
                "SELECT audit_status, count(*) FROM leads GROUP BY audit_status"
            ).fetchall()
            new_24h = conn.execute(
                "SELECT count(*) FROM leads WHERE created_at > now() - interval '24 hours'"
            ).fetchone()[0] or 0
    except Exception as e:  # noqa: BLE001
        return _section("📋 Leads", f"_Could not read — {e}_")

    status_lines = "\n".join(
        f"  - {status or '_(NULL)_'}: **{count}**"
        for status, count in sorted(by_status, key=lambda r: -r[1])
    )
    body = (
        f"- Total: **{total}**\n"
        f"- New in last 24h: **{new_24h}**\n"
        f"- By audit_status:\n{status_lines}"
    )
    return _section("📋 Leads", body)


def _storage_section() -> str:
    try:
        with _connect() as conn:
            mb = (conn.execute(
                "SELECT pg_database_size(current_database())"
            ).fetchone()[0] or 0) / (1024 ** 2)
    except Exception as e:  # noqa: BLE001
        return _section("💾 Storage", f"_Could not read — {e}_")

    return _section("💾 Storage", f"- DB size: **{mb:,.1f} MB**")


def _gemini_budget_section() -> str:
    """HTTP GET against the live backend's /admin/gemini-budget. The
    budget state SQLite file lives on Render's filesystem — the GH
    runner cannot read it directly, so we have to hop through the API.
    """
    backend_url = os.environ.get("BACKEND_URL", "").rstrip("/")
    api_key = os.environ.get("API_SECRET_KEY", "")
    if not (backend_url and api_key):
        return _section(
            "🤖 Gemini budget",
            "Skipped — `BACKEND_URL` + `API_SECRET_KEY` not set.",
        )

    try:
        r = requests.get(
            f"{backend_url}/admin/gemini-budget",
            headers={"X-API-Key": api_key},
            timeout=10,
        )
        r.raise_for_status()
        state = r.json()
    except requests.HTTPError as e:
        return _section("🤖 Gemini budget", f"_HTTP {e.response.status_code} — {e.response.reason}_")
    except Exception as e:  # noqa: BLE001
        return _section("🤖 Gemini budget", f"_Could not read — {e}_")

    used = int(state.get("used_today", 0))
    ceiling = int(state.get("ceiling", 0))
    remaining = int(state.get("remaining", 0))
    pct = (used / ceiling * 100.0) if ceiling > 0 else 0.0

    bar = "🟢" if pct < 50 else ("🟡" if pct < 90 else "🔴")
    body = (
        f"- {bar} Used today: **{used:,}** / {ceiling:,} ({pct:.1f}%)\n"
        f"- Input: **{state.get('input_today', 0):,}** · "
        f"Output: **{state.get('output_today', 0):,}**\n"
        f"- Remaining: **{remaining:,}** (resets {state.get('reset_at_utc', 'unknown')})"
    )
    return _section("🤖 Gemini budget", body)


def _orchestration_section() -> str:
    """Job counts in last 24h by status."""
    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT status, count(*) FROM orchestration_jobs "
                "WHERE created_at > now() - interval '24 hours' "
                "GROUP BY status"
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        return _section("⚙️  Orchestration (24h)", f"_Could not read — {e}_")

    if not rows:
        return _section("⚙️  Orchestration (24h)", "_No jobs in the last 24h._")

    lines = "\n".join(
        f"  - {status or '_(NULL)_'}: **{count}**"
        for status, count in sorted(rows, key=lambda r: -r[1])
    )
    failed_count = next((c for s, c in rows if s == "failed"), 0)
    warning = "\n\n⚠️  **Failed jobs in the last 24h.**" if failed_count else ""
    body = f"- Jobs by status:\n{lines}{warning}"
    return _section("⚙️  Orchestration (24h)", body)


def _orphans_section() -> str:
    """Run the orphan/zombie counters (read-only versions) inline so we
    surface the daily numbers in the digest without re-running the
    full sweep workflow."""
    try:
        with _connect() as conn:
            soft_orphans = conn.execute(
                "SELECT count(*) FROM campaign_messages cm "
                "WHERE NOT EXISTS (SELECT 1 FROM leads l "
                "WHERE l.unique_key = cm.lead_unique_key)"
            ).fetchone()[0] or 0
            zombies = conn.execute(
                "SELECT count(*) FROM orchestration_jobs "
                "WHERE status = 'running' AND updated_at < now() - interval '4 hours'"
            ).fetchone()[0] or 0
            stuck_leads = conn.execute(
                "SELECT count(*) FROM leads "
                "WHERE audit_status IN ('Pending', 'Processing') "
                "AND updated_at < now() - interval '24 hours'"
            ).fetchone()[0] or 0
    except Exception as e:  # noqa: BLE001
        return _section("🧹 Orphans / zombies", f"_Could not read — {e}_")

    lines = [
        f"- Soft-orphan campaign_messages: **{soft_orphans}**",
        f"- Zombie orchestration_jobs (>4h running): **{zombies}**"
        + (" _(auto-heal will flip to failed)_" if zombies else ""),
        f"- Stuck leads (>24h Pending/Processing): **{stuck_leads}**",
    ]
    return _section("🧹 Orphans / zombies", "\n".join(lines))


def main() -> int:
    now = datetime.now(timezone.utc)

    sections = [
        fn() for fn in (
            _leads_section,
            _storage_section,
            _gemini_budget_section,
            _orchestration_section,
            _orphans_section,
        )
    ]

    repo = os.environ.get("GITHUB_REPOSITORY", "user/repo")
    out = [
        f"# Daily dogfood digest — {now.strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        f"_Roadmap 13.15 — see [`docs/dogfood-plan-2026-05.md`]"
        f"(https://github.com/{repo}/blob/main/docs/dogfood-plan-2026-05.md)_",
        "",
        *sections,
        "---",
        "_Suppressed sections need credentials set in repo secrets. "
        "Email reply tracking is manual — see plan §2.1._",
    ]
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
