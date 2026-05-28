"""JWT manipulation e2e: prove every tamper variant fails closed at the
Next.js proxy auth gate.

The proxy calls `supabase.auth.getUser()` on every request; Supabase
validates the access_token against its `/auth/v1/user` endpoint, which
performs the HS256 signature check using the project JWT secret. Without
that secret, every forged/altered/expired/replayed token must be rejected.

Test vectors:
  1. Role promotion — flip `role` to `service_role`, re-sign with empty
     key. Signature mismatch → 401.
  2. Expired replay — set `exp` to the past on the real token. Signature
     breaks (we don't have the secret) → 401. (We can't actually re-sign
     a "still-valid-but-past-exp" token, so this collapses into the
     signature-mismatch case — same defense fires. Documented limitation.)
  3. Sub tampering — change `sub` to operator's UUID. Signature breaks → 401.
  4. Signature stripped — drop the third JWT segment. Malformed → 401.
  5. Cookie smuggling — set two competing auth cookies with different
     `sub` claims. Whichever the parser picks, the answer must be 401.
  6. Long-lived forged JWT — mint offline with `exp = now + 1y`, signed
     with a random key. Signature mismatch → 401.

Opt-in only:
  RUN_JWT_MANIPULATION_E2E=1
  FRONTEND_URL=http://localhost:3000
  TEST_USER_EMAIL, TEST_USER_PASSWORD
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from pathlib import Path
from typing import Iterable, Optional

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
        "RUN_JWT_MANIPULATION_E2E",
        "FRONTEND_URL",
        "TEST_USER_EMAIL",
        "TEST_USER_PASSWORD",
        "NEXT_PUBLIC_SUPABASE_URL",
        "SUPABASE_URL",
    ):
        v = os.environ.get(k)
        if v:
            merged[k] = v
    return merged


ENV = _load_env()

OPT_IN = ENV.get("RUN_JWT_MANIPULATION_E2E", "").strip() in ("1", "true", "yes")
FRONTEND_URL = (ENV.get("FRONTEND_URL") or "").rstrip("/")
TEST_USER_EMAIL = ENV.get("TEST_USER_EMAIL", "")
TEST_USER_PASSWORD = ENV.get("TEST_USER_PASSWORD", "")

pytestmark = pytest.mark.skipif(
    not (OPT_IN and FRONTEND_URL and TEST_USER_EMAIL and TEST_USER_PASSWORD),
    reason=(
        "Set RUN_JWT_MANIPULATION_E2E=1 + FRONTEND_URL + TEST_USER_EMAIL + "
        "TEST_USER_PASSWORD to run JWT manipulation e2e. Skipping."
    ),
)


# ---------------------------------------------------------------------------
# JWT + Supabase-cookie helpers.
# ---------------------------------------------------------------------------


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _decode_jwt(token: str) -> tuple[dict, dict, bytes]:
    h, p, s = token.split(".")
    return (
        json.loads(_b64url_decode(h)),
        json.loads(_b64url_decode(p)),
        _b64url_decode(s),
    )


def _encode_jwt(header: dict, payload: dict, signature: bytes) -> str:
    h = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    s = _b64url_encode(signature)
    return f"{h}.{p}.{s}"


def _hs256_sign(header: dict, payload: dict, key: bytes) -> str:
    h = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    msg = f"{h}.{p}".encode("ascii")
    sig = hmac.new(key, msg, hashlib.sha256).digest()
    return f"{h}.{p}.{_b64url_encode(sig)}"


def _supabase_project_ref(url: str) -> str:
    """`https://<ref>.supabase.co` → `<ref>`."""
    host = url.split("://", 1)[-1].split("/", 1)[0]
    return host.split(".", 1)[0]


def _auth_cookie_names_for(ref: str) -> str:
    return f"sb-{ref}-auth-token"


def _find_auth_cookies(cookies: list[dict], base_name: str) -> list[dict]:
    """All chunks of the Supabase SSR auth cookie."""
    return [
        c
        for c in cookies
        if c["name"] == base_name or c["name"].startswith(base_name + ".")
    ]


def _decode_session_value(cookies: list[dict], base_name: str) -> dict:
    """Concatenate chunked cookie values → strip `base64-` prefix → b64-decode → JSON parse."""
    chunks = sorted(
        _find_auth_cookies(cookies, base_name),
        key=lambda c: int(c["name"].rsplit(".", 1)[-1]) if "." in c["name"] else -1,
    )
    if not chunks:
        raise RuntimeError(f"Auth cookie {base_name!r} not found")
    raw = "".join(c["value"] for c in chunks)
    if raw.startswith("base64-"):
        raw = raw[len("base64-") :]
    return json.loads(_b64url_decode(raw))


def _encode_session_value(session: dict) -> str:
    """JSON → b64url → `base64-` prefix. Single non-chunked cookie."""
    serialised = json.dumps(session, separators=(",", ":")).encode()
    return "base64-" + _b64url_encode(serialised)


def _replace_auth_cookie(
    context, base_name: str, new_value: str, domain: str, path: str = "/"
) -> None:
    """Remove all chunks of the current auth cookie, set a single replacement."""
    context.clear_cookies(name=base_name)
    for i in range(0, 10):
        context.clear_cookies(name=f"{base_name}.{i}")
    context.add_cookies(
        [
            {
                "name": base_name,
                "value": new_value,
                "domain": domain,
                "path": path,
                "httpOnly": False,
                "secure": False,
                "sameSite": "Lax",
            }
        ]
    )


# ---------------------------------------------------------------------------
# Login + probe helpers.
# ---------------------------------------------------------------------------


def _login(page, frontend_url: str, email: str, password: str) -> None:
    page.goto(f"{frontend_url}/login", wait_until="domcontentloaded")
    page.fill('input[name="email"]', email)
    page.fill('input[name="password"]', password)
    page.click('button[type="submit"]')
    page.wait_for_url(f"{frontend_url}/", timeout=15_000)


def _probe_status(context) -> int:
    """GET /api/proxy/leads — read-only, returns 200 on legit auth, 401 on
    rejected. Smaller blast radius than a destructive endpoint."""
    resp = context.request.fetch(
        f"{FRONTEND_URL}/api/proxy/leads",
        method="GET",
    )
    return resp.status


def _fresh_context_with_tamper(p, tamper_fn) -> int:
    """Login fresh, run tamper_fn(session, context, base_name, domain) → set
    cookie → probe → return status. tamper_fn must return the modified
    session dict OR None to skip the replacement (cookie-smuggling case)."""
    supa_url = ENV.get("NEXT_PUBLIC_SUPABASE_URL") or ENV.get("SUPABASE_URL", "")
    if not supa_url:
        pytest.fail("NEXT_PUBLIC_SUPABASE_URL not configured")
    project_ref = _supabase_project_ref(supa_url)
    base_name = _auth_cookie_names_for(project_ref)

    browser = p.chromium.launch(headless=True)
    context = browser.new_context(base_url=FRONTEND_URL)
    page = context.new_page()
    try:
        _login(page, FRONTEND_URL, TEST_USER_EMAIL, TEST_USER_PASSWORD)

        # Sanity check: legitimate session works.
        baseline = _probe_status(context)
        assert baseline == 200, f"Login probe failed before tampering: {baseline}"

        cookies = context.cookies()
        session = _decode_session_value(cookies, base_name)

        # Determine cookie domain — use the host from the first existing chunk.
        existing = _find_auth_cookies(cookies, base_name)
        domain = existing[0]["domain"] if existing else "localhost"

        tamper_fn(session, context, base_name, domain)

        return _probe_status(context)
    finally:
        browser.close()


# ---------------------------------------------------------------------------
# The six manipulation tests.
# ---------------------------------------------------------------------------


def test_role_promotion_to_service_role_rejected():
    """Flip `role` → `service_role` in the access_token payload, re-sign
    with an empty key. The trio of header.payload.sig is well-formed JWT
    but the HS256 sig won't match → Supabase rejects."""
    from playwright.sync_api import sync_playwright

    def tamper(session, context, base_name, domain):
        header, payload, _ = _decode_jwt(session["access_token"])
        payload["role"] = "service_role"
        forged = _hs256_sign(header, payload, key=b"")
        session["access_token"] = forged
        # Blank refresh_token so the SSR client can't auto-refresh past the
        # tamper using the still-valid refresh cookie.
        session["refresh_token"] = ""
        _replace_auth_cookie(context, base_name, _encode_session_value(session), domain)

    with sync_playwright() as p:
        status = _fresh_context_with_tamper(p, tamper)
    assert status == 401, f"Role-promotion tamper not rejected: {status}"


def test_expired_jwt_replay_rejected():
    """Push `exp` into the past. We can't re-sign without the secret, so
    the resulting token also has a bad signature — the defense fires
    either way (signature OR exp check). What matters: 401."""
    from playwright.sync_api import sync_playwright

    def tamper(session, context, base_name, domain):
        header, payload, _ = _decode_jwt(session["access_token"])
        payload["exp"] = int(time.time()) - 3600
        forged = _hs256_sign(header, payload, key=b"")
        session["access_token"] = forged
        session["refresh_token"] = ""
        session["expires_at"] = int(time.time()) - 3600
        _replace_auth_cookie(context, base_name, _encode_session_value(session), domain)

    with sync_playwright() as p:
        status = _fresh_context_with_tamper(p, tamper)
    assert status == 401, f"Expired-JWT replay not rejected: {status}"


def test_sub_tampering_rejected():
    """Swap `sub` to an attacker-chosen UUID, keep the original signature.
    Signature won't validate → 401."""
    from playwright.sync_api import sync_playwright

    def tamper(session, context, base_name, domain):
        header, payload, sig = _decode_jwt(session["access_token"])
        payload["sub"] = str(uuid.uuid4())  # attacker-chosen target UUID
        forged = _encode_jwt(header, payload, sig)  # keep original sig
        session["access_token"] = forged
        session["refresh_token"] = ""
        _replace_auth_cookie(context, base_name, _encode_session_value(session), domain)

    with sync_playwright() as p:
        status = _fresh_context_with_tamper(p, tamper)
    assert status == 401, f"Sub-tamper not rejected: {status}"


def test_signature_stripped_rejected():
    """Remove the third segment of the JWT — malformed token → 401."""
    from playwright.sync_api import sync_playwright

    def tamper(session, context, base_name, domain):
        h, p, _ = session["access_token"].split(".")
        # Empty signature segment but trailing dot preserved — still a
        # malformed HS256 JWT.
        session["access_token"] = f"{h}.{p}."
        session["refresh_token"] = ""
        _replace_auth_cookie(context, base_name, _encode_session_value(session), domain)

    with sync_playwright() as p:
        status = _fresh_context_with_tamper(p, tamper)
    assert status == 401, f"Stripped-signature JWT not rejected: {status}"


def test_double_cookie_smuggling_rejected():
    """Set TWO competing auth-cookie names — the real one (valid session)
    AND a same-base-named cookie with a forged session carrying a
    different `sub`. Whichever the SSR cookie parser picks, the answer
    must be the safe one — 401 (or 200 only if the legit cookie wins; we
    accept that *deterministically*, not because the forged one slipped
    through). The smuggling defense is: a tampered cookie can never
    *grant* access it didn't already have.

    Concretely: we replace the real auth cookie with a forged one AND
    also add a duplicate cookie at a different path. Both carry forged
    tokens. Expected outcome: 401 — neither presents a valid session."""
    from playwright.sync_api import sync_playwright

    def tamper(session, context, base_name, domain):
        # First forged session — sub A.
        h1, p1, _ = _decode_jwt(session["access_token"])
        p1a = dict(p1, sub=str(uuid.uuid4()))
        session_a = dict(
            session, access_token=_hs256_sign(h1, p1a, b""), refresh_token=""
        )
        # Second forged session — sub B.
        p1b = dict(p1, sub=str(uuid.uuid4()))
        session_b = dict(
            session, access_token=_hs256_sign(h1, p1b, b""), refresh_token=""
        )

        # Replace the canonical cookie with forged A.
        _replace_auth_cookie(
            context, base_name, _encode_session_value(session_a), domain
        )
        # Smuggle B at a more-specific path — browsers send both, the SSR
        # parser must not blindly trust either.
        context.add_cookies(
            [
                {
                    "name": base_name,
                    "value": _encode_session_value(session_b),
                    "domain": domain,
                    "path": "/api",
                    "httpOnly": False,
                    "secure": False,
                    "sameSite": "Lax",
                }
            ]
        )

    with sync_playwright() as p:
        status = _fresh_context_with_tamper(p, tamper)
    assert status == 401, (
        f"Cookie smuggling granted access: status={status}. "
        f"Either forged session was accepted as valid."
    )


def test_long_lived_forged_jwt_rejected():
    """Build a JWT offline with `exp = now + 1y`, signed with a random
    256-bit key the attacker chose. Supabase's HS256 check uses the
    project secret — random-key sigs are statistically impossible to
    match → 401."""
    from playwright.sync_api import sync_playwright

    def tamper(session, context, base_name, domain):
        header, payload, _ = _decode_jwt(session["access_token"])
        attacker_key = secrets.token_bytes(32)
        payload["exp"] = int(time.time()) + 365 * 24 * 3600
        payload["iat"] = int(time.time())
        forged = _hs256_sign(header, payload, key=attacker_key)
        session["access_token"] = forged
        session["refresh_token"] = ""
        session["expires_at"] = payload["exp"]
        _replace_auth_cookie(context, base_name, _encode_session_value(session), domain)

    with sync_playwright() as p:
        status = _fresh_context_with_tamper(p, tamper)
    assert status == 401, f"1-year forged JWT not rejected: {status}"
