"""Cross-origin CSRF gate e2e test against the Next.js proxy.

Threat model: an attacker page on `https://evil.com` tries to make the
operator's authenticated browser POST to `/api/proxy/<destructive-endpoint>`.
SameSite=Lax cookies already block this for cross-site POST, but the proxy
adds a fail-closed Origin allowlist as belt-and-braces. This test simulates
the leak by using a *real* logged-in browser context and overriding the
`Origin` header at the request layer (something a normal browser would
refuse to do — but a misconfigured CORS proxy / pinned-cookie attack could).

For every state-changing endpoint, both these variants MUST return 403:
  a) `Origin: https://evil.com`        — foreign origin
  b) `Origin` header omitted entirely  — fail-closed-on-missing branch

Opt-in only. Required env (in `.env.test` or process env):
  RUN_PROXY_ORIGIN_E2E=1
  FRONTEND_URL=http://localhost:3000  (or https://… for staging)
  TEST_USER_EMAIL=<a real Supabase Auth user>
  TEST_USER_PASSWORD=<that user's password>

Optional (for the "no state change" assertion):
  SUPABASE_URL=…
  SUPABASE_SERVICE_ROLE_KEY=…
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parent.parent
EVIL_ORIGIN = "https://evil.com"

# Endpoints below are a representative slice of the state-changing surface.
# Every entry covers a distinct risk class so a regression in any one class
# trips the test. Add new destructive routes here as they ship.
#
# (method, path, body) — body is intentionally minimal: the Origin gate
# fires BEFORE Pydantic validation, so a `{}` body must still get 403'd.
STATE_CHANGING_ENDPOINTS = [
    ("POST", "process-all", {}),
    ("POST", "ask", {"instruction": "hello"}),
    ("POST", "draft-outreach", {"unique_key": "nonexistent"}),
    ("POST", "draft-linkedin", {"unique_key": "nonexistent"}),
    ("POST", "execute", {"task": "STATUS_CHECK", "params": {}}),
    ("POST", "hunt-all", {}),
    ("POST", "discovery/start", {"query": "x", "location": "y"}),
    ("POST", "orchestrator/start", {"task": "audit", "filters": "all"}),
    ("POST", "campaigns", {"name": "x", "segment": "all"}),
    ("DELETE", "leads/clear", {}),
]


def _load_test_env() -> dict[str, str]:
    """Merge .env.test → .env → process env, .env.test winning."""
    merged: dict[str, str] = {}
    for path in [REPO_ROOT / ".env.test", REPO_ROOT / ".env"]:
        if path.exists():
            for k, v in dotenv_values(path).items():
                if v and k not in merged:
                    merged[k] = v
    for k in (
        "RUN_PROXY_ORIGIN_E2E",
        "FRONTEND_URL",
        "TEST_USER_EMAIL",
        "TEST_USER_PASSWORD",
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "NEXT_PUBLIC_SUPABASE_URL",
    ):
        v = os.environ.get(k)
        if v:
            merged[k] = v
    return merged


ENV = _load_test_env()

OPT_IN = ENV.get("RUN_PROXY_ORIGIN_E2E", "").strip() in ("1", "true", "yes")
FRONTEND_URL = (ENV.get("FRONTEND_URL") or "").rstrip("/")
TEST_USER_EMAIL = ENV.get("TEST_USER_EMAIL", "")
TEST_USER_PASSWORD = ENV.get("TEST_USER_PASSWORD", "")

pytestmark = pytest.mark.skipif(
    not (OPT_IN and FRONTEND_URL and TEST_USER_EMAIL and TEST_USER_PASSWORD),
    reason=(
        "Set RUN_PROXY_ORIGIN_E2E=1 + FRONTEND_URL + TEST_USER_EMAIL + "
        "TEST_USER_PASSWORD to run the cross-origin CSRF e2e. Skipping."
    ),
)


# ---------------------------------------------------------------------------
# Service-role state snapshots — used to *prove* no destructive endpoint
# slipped past the gate. Best-effort: if service-role isn't configured, the
# state-change assertion is skipped but the 403 assertions still run.
# ---------------------------------------------------------------------------


def _service_role_client():
    url = ENV.get("SUPABASE_URL") or ENV.get("NEXT_PUBLIC_SUPABASE_URL")
    key = ENV.get("SUPABASE_SERVICE_ROLE_KEY")
    if not (url and key):
        return None
    try:
        from supabase import create_client

        return create_client(url, key)
    except Exception:
        return None


def _snapshot_counts(client) -> dict[str, int]:
    if client is None:
        return {}
    counts: dict[str, int] = {}
    for table in ("leads", "campaigns", "orchestration_jobs"):
        try:
            r = client.table(table).select("*", count="exact").limit(1).execute()
            counts[table] = r.count or 0
        except Exception:
            counts[table] = -1  # sentinel: probe failed, don't compare
    return counts


# ---------------------------------------------------------------------------
# Login + e2e test.
# ---------------------------------------------------------------------------


def _login(page, frontend_url: str, email: str, password: str) -> None:
    """Drive the real login form. Bails if we don't end up on `/`."""
    page.goto(f"{frontend_url}/login", wait_until="domcontentloaded")
    page.fill('input[name="email"]', email)
    page.fill('input[name="password"]', password)
    # The login throttle is 5/60s — keep this test from racing itself by
    # using a long-enough wait_for_url window.
    page.click('button[type="submit"]')
    page.wait_for_url(f"{frontend_url}/", timeout=15_000)


def test_proxy_origin_gate_blocks_cross_origin_state_change():
    from playwright.sync_api import sync_playwright

    svc = _service_role_client()
    pre_counts = _snapshot_counts(svc)

    failures: list[str] = []
    successes_through_gate: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(base_url=FRONTEND_URL)
        page = context.new_page()
        try:
            _login(page, FRONTEND_URL, TEST_USER_EMAIL, TEST_USER_PASSWORD)
        except Exception as e:
            browser.close()
            pytest.fail(f"Login flow failed — cannot exercise origin gate. {e!r}")

        # `context.request` carries the browser context's cookies (Supabase
        # session) — but accepts arbitrary Origin overrides. That's exactly
        # the scenario the gate is supposed to defeat.
        api = context.request

        for method, path, body in STATE_CHANGING_ENDPOINTS:
            url = f"{FRONTEND_URL}/api/proxy/{path}"

            # Variant A: foreign Origin
            resp = api.fetch(
                url,
                method=method,
                headers={
                    "Origin": EVIL_ORIGIN,
                    "Content-Type": "application/json",
                },
                data=body,
            )
            if resp.status != 403:
                failures.append(
                    f"[foreign-origin] {method} {path} → {resp.status} (expected 403)"
                )
                if resp.status < 400:
                    successes_through_gate.append(f"{method} {path}")

            # Variant B: Origin header omitted entirely
            resp_b = api.fetch(
                url,
                method=method,
                headers={"Content-Type": "application/json"},
                data=body,
            )
            if resp_b.status != 403:
                failures.append(
                    f"[missing-origin] {method} {path} → {resp_b.status} (expected 403)"
                )
                if resp_b.status < 400:
                    successes_through_gate.append(f"{method} {path}")

        browser.close()

    # Hard-stop the test if any request landed past the gate — we don't want
    # the diff-vs-snapshot assertion to be the only thing catching a real
    # CSRF bypass. The failure list itself is the operator-visible proof.
    assert not failures, (
        "Origin gate failures (each must 403):\n  "
        + "\n  ".join(failures)
        + (
            "\n\nDESTRUCTIVE: the following landed past the gate: "
            + ", ".join(successes_through_gate)
            if successes_through_gate
            else ""
        )
    )

    # Belt-and-braces — counts must not move.
    if svc is not None:
        post_counts = _snapshot_counts(svc)
        for table, before in pre_counts.items():
            if before == -1 or post_counts.get(table, -1) == -1:
                continue  # sentinel — skip comparison
            assert post_counts[table] == before, (
                f"State changed despite Origin gate: "
                f"{table} {before} → {post_counts[table]}"
            )


def test_proxy_origin_gate_allows_same_origin_state_change():
    """Sanity / coupling test: with the *correct* same-origin Origin header,
    a state-changing request reaches the backend (we don't care what status
    the backend returns — just that the proxy didn't 403 us out at the gate).

    Without this companion test, the suite would pass if someone wired the
    proxy to 403 every state-change unconditionally — which would also block
    the legit dashboard.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(base_url=FRONTEND_URL)
        page = context.new_page()
        _login(page, FRONTEND_URL, TEST_USER_EMAIL, TEST_USER_PASSWORD)

        # `/ask` is read-mostly (it autoexecutes only read-only tasks like
        # STATUS_CHECK). Safe to exercise the happy path.
        resp = context.request.fetch(
            f"{FRONTEND_URL}/api/proxy/ask",
            method="POST",
            headers={
                "Origin": FRONTEND_URL,
                "Content-Type": "application/json",
            },
            data={"instruction": "How many leads are in the database?"},
        )
        browser.close()

    assert resp.status != 403, (
        f"Same-origin POST got 403'd by the proxy — "
        f"Origin allowlist is misconfigured. body={resp.text()[:300]}"
    )
