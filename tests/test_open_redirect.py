"""Open-redirect e2e for `/login?next=<payload>`.

`sanitizeNext` in `frontend/utils/url.mjs` is unit-tested by
`frontend/utils/url.test.mjs` — fast, deterministic, no infra. This
file exercises the same contract through a real browser + real login,
verifying that after a successful sign-in the user lands on a
same-origin path, not on the attacker's host. Catches integration
regressions where the sanitiser is bypassed (e.g., someone wires up an
unsanitised second redirect, or a future Next.js change starts
double-decoding the value).

Opt-in (mirrors the other e2e files):
  RUN_OPEN_REDIRECT_E2E=1
  FRONTEND_URL=http://localhost:3000
  TEST_USER_EMAIL=<real Supabase Auth user>
  TEST_USER_PASSWORD=<that user's password>

The destination origin is asserted via `urlparse(...).netloc` against
the FRONTEND_URL's netloc — exact host:port match, so an attacker
who routes the redirect through `https://localhost.evil.com` (host
ends in the legitimate hostname) is still caught.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

import pytest
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_env() -> dict[str, str]:
    merged: dict[str, str] = {}
    for path in [REPO_ROOT / ".env.test", REPO_ROOT / ".env"]:
        if path.exists():
            for k, v in dotenv_values(path).items():
                if v and k not in merged:
                    merged[k] = v
    for k in (
        "RUN_OPEN_REDIRECT_E2E",
        "FRONTEND_URL",
        "TEST_USER_EMAIL",
        "TEST_USER_PASSWORD",
    ):
        v = os.environ.get(k)
        if v:
            merged[k] = v
    return merged


ENV = _load_env()
OPT_IN = ENV.get("RUN_OPEN_REDIRECT_E2E", "").strip() in ("1", "true", "yes")
FRONTEND_URL = (ENV.get("FRONTEND_URL") or "").rstrip("/")
TEST_USER_EMAIL = ENV.get("TEST_USER_EMAIL", "")
TEST_USER_PASSWORD = ENV.get("TEST_USER_PASSWORD", "")

pytestmark = pytest.mark.skipif(
    not (OPT_IN and FRONTEND_URL and TEST_USER_EMAIL and TEST_USER_PASSWORD),
    reason=(
        "Set RUN_OPEN_REDIRECT_E2E=1 + FRONTEND_URL + TEST_USER_EMAIL + "
        "TEST_USER_PASSWORD to run open-redirect e2e. Skipping."
    ),
)


# The attack matrix. Each payload is what arrives in the address bar as
# `?next=<payload>`. The browser percent-decodes the query value before
# Next.js sees it, so test inputs use the LITERAL strings the operator
# would type — both raw and pre-encoded variants — to cover the chain.
OPEN_REDIRECT_PAYLOADS = [
    "https://evil.com",
    "https://evil.com/path",
    "http://evil.com",
    "//evil.com",
    "//evil.com/path",
    "/\\evil.com",
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "/dashboard%2f%2fevil.com",
    "/dashboard%2F%2Fevil.com",
    "/%2e%2e/evil.com",
    "/%2E%2E/evil.com",
    "/foo%5cevil.com",
    "%2F%2Fevil.com",
    "/foo%00bar",
    "/foo%0devil",
    "/foo%0aevil",
    "/@evil.com/foo",
    "/path:8080@evil.com",
    "//evil.com\\@trusted.example",
    "/\t//evil.com",
    # Doubly-encoded — the first decode pass yields `%2F%2Fevil.com`
    # which doesn't start with `//`, but a misconfigured downstream
    # decoder could expose the host-swap. Browser-rendered redirect
    # destinations must still resolve same-origin.
    "/%252F%252Fevil.com",
]


def _same_origin(url: str, frontend_url: str) -> bool:
    """Exact host:port match against the frontend's origin.

    Substring/endswith checks are unsafe: an attacker registering
    `localhost.evil.com` would `.endswith('localhost')` and slip past.
    """
    a = urlparse(url)
    b = urlparse(frontend_url)
    return (a.scheme, a.netloc) == (b.scheme, b.netloc)


def _login_with_next(
    page, frontend_url: str, email: str, password: str, next_param: str
) -> None:
    """Drive the real login form with the payload pre-loaded as ?next=."""
    # Build the URL ourselves so the test payload survives unchanged —
    # `page.goto` doesn't re-encode the query string we hand it.
    from urllib.parse import quote

    target = f"{frontend_url}/login?next={quote(next_param, safe='')}"
    page.goto(target, wait_until="domcontentloaded")
    page.fill('input[name="email"]', email)
    page.fill('input[name="password"]', password)
    page.click('button[type="submit"]')
    # Wait for navigation away from /login (the action's `redirect()` runs
    # server-side and the browser follows the Location response).
    page.wait_for_url(
        lambda u: "/login" not in u,
        timeout=15_000,
    )


@pytest.mark.parametrize(
    "payload",
    OPEN_REDIRECT_PAYLOADS,
    ids=[f"payload-{i:02d}" for i in range(len(OPEN_REDIRECT_PAYLOADS))],
)
def test_login_next_param_never_redirects_off_origin(payload):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(base_url=FRONTEND_URL)
        page = context.new_page()
        try:
            _login_with_next(
                page,
                FRONTEND_URL,
                TEST_USER_EMAIL,
                TEST_USER_PASSWORD,
                payload,
            )
            final_url = page.url
        finally:
            browser.close()

    assert _same_origin(final_url, FRONTEND_URL), (
        f"OPEN REDIRECT: payload {payload!r} sent us to {final_url!r} "
        f"(expected same origin as {FRONTEND_URL!r})"
    )


def test_login_next_param_legit_path_still_works():
    """Coupling test: a legitimate same-origin `next=` must still navigate
    to that path. Without this, a hardening that 403s all `next=` values
    would pass the attack-matrix above while breaking the real UX (deep-
    link from `/insights` → `/login?next=/insights` → back to insights)."""
    from playwright.sync_api import sync_playwright

    legit = "/insights?view=audited"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(base_url=FRONTEND_URL)
        page = context.new_page()
        try:
            _login_with_next(
                page,
                FRONTEND_URL,
                TEST_USER_EMAIL,
                TEST_USER_PASSWORD,
                legit,
            )
            final_url = page.url
        finally:
            browser.close()

    assert _same_origin(final_url, FRONTEND_URL), (
        f"Legit next= didn't stay same-origin: {final_url!r}"
    )
    parsed = urlparse(final_url)
    assert parsed.path == "/insights" and "view=audited" in (parsed.query or ""), (
        f"Legit next= didn't reach the intended path: {final_url!r}"
    )
