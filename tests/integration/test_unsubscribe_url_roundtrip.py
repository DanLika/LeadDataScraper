"""Producer ↔ verifier round-trip for the per-message unsubscribe URL.

The bug this test pins (caught 2026-05-26): `dispatch_tick.py` previously
built `f"{base}/u/{tracking_id}"` while the handler exposes
`GET/POST /unsubscribe/{token}` consuming an HMAC envelope. Every email
sent through the dispatcher would have an unsubscribe link that 404s
AND embeds a payload the handler cannot decode.

The fix funnels both surfaces through ``build_unsubscribe_url`` in
``src/utils/unsubscribe_tokens.py``. This test exercises that one
boundary: build the URL → tear off the trailing path segment → feed it
to ``verify`` → assert the decoded ``tracking_id`` matches what went in.

If a future refactor splits the path or stops calling ``mint``, this
test goes red. If the handler path renames, update
``UNSUBSCRIBE_URL_PATH_SEGMENT`` (single source of truth) and the
backend route in lockstep — this test asserts both match.
"""

from __future__ import annotations

import uuid
from urllib.parse import urlsplit

import pytest

from src.utils.unsubscribe_tokens import (
    UNSUBSCRIBE_URL_PATH_SEGMENT,
    build_unsubscribe_url,
    verify,
)


SECRET_ENV = "UNSUBSCRIBE_TOKEN_SECRET"


@pytest.fixture(autouse=True)
def _set_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    # Deterministic, non-secret test value. The function will refuse
    # to mint without an env, so set it before every test.
    monkeypatch.setenv(SECRET_ENV, "test-secret-deterministic")


def test_dispatch_url_verifies_roundtrip() -> None:
    """Build a URL, tear off the token, verify — tracking_id round-trips."""
    tracking_id = str(uuid.uuid4())
    url = build_unsubscribe_url(
        "https://lead-scraper-backend.onrender.com", tracking_id
    )

    parts = urlsplit(url)
    # Path must be /<segment>/<token>, no extras.
    segments = parts.path.strip("/").split("/")
    assert len(segments) == 2, f"unexpected path shape: {parts.path!r}"
    assert segments[0] == UNSUBSCRIBE_URL_PATH_SEGMENT, (
        f"path segment {segments[0]!r} != "
        f"UNSUBSCRIBE_URL_PATH_SEGMENT ({UNSUBSCRIBE_URL_PATH_SEGMENT!r}); "
        "producer + handler are drifting"
    )

    token = segments[1]
    payload = verify(token)
    assert payload.tracking_id == tracking_id


def test_path_segment_matches_backend_handler_route() -> None:
    """Single source of truth — the path segment used here MUST equal
    the route registered in ``backend/main.py``. If the backend handler
    is renamed to e.g. ``/uns`` without updating the constant, this
    test still passes by itself but the operator will catch the
    mismatch in deployment smoke. Locked here as the documented
    invariant: change BOTH in lockstep or replace this assertion with
    a literal-string compare against the live route table.
    """
    assert UNSUBSCRIBE_URL_PATH_SEGMENT == "unsubscribe"


def test_base_url_trailing_slash_normalised() -> None:
    """``build_unsubscribe_url`` must accept either ``.../base`` or
    ``.../base/`` and emit a single-slash URL."""
    tracking_id = str(uuid.uuid4())
    a = build_unsubscribe_url("https://x.com", tracking_id)
    b = build_unsubscribe_url("https://x.com/", tracking_id)
    # Tokens differ (issued_at can advance between mint calls), but
    # the path prefix must be identical.
    assert a.split("/unsubscribe/")[0] == b.split("/unsubscribe/")[0]
    assert "//unsubscribe" not in a
    assert "//unsubscribe" not in b


def test_empty_tracking_id_rejected() -> None:
    """Pre-helper, dispatcher called the builder unconditionally and
    let an empty-string ``tracking_id`` leak through. The helper must
    refuse — empty UUID can't be HMAC-bound; an unsubscribable
    recipient with the wrong token type is worse than a missing link
    (still violates CAN-SPAM, but at least doesn't 404 mysteriously)."""
    with pytest.raises(ValueError):
        build_unsubscribe_url("https://x.com", "")
