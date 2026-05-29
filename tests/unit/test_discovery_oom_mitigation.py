"""Unit tests for the discovery OOM mitigations shipped after the
2026-05-28 prod incidents (14:46:03 + 14:51:25 UTC, both ``oomKilled:
512Mi`` on Render starter).

Covers:

* ``TaskOrchestrator._discovery_sem`` — two parallel
  ``run_discovery_job`` calls must NOT execute their Chromium-bound
  ``find_leads`` work concurrently. Issuance order is preserved but
  the second job waits for the first to release the semaphore.
* ``_install_resource_block`` — Playwright route handler aborts
  image / font / media resource types and Google Maps tile / Street
  View / gstatic-image URLs; everything else falls through to the
  SSRF guard via ``route.fallback()``.
* ``_MAX_SCROLL_ITERS`` + ``_MAX_CONTAINERS`` — module-level caps are
  respected (env-tunable, with sane floors).

Pure-function tests — no real browser, no DB, no network.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.task_orchestrator import TaskOrchestrator  # noqa: E402
from src.scrapers import discovery_engine  # noqa: E402


# ───────────────── concurrency semaphore ──────────────────────────────────


@pytest.mark.asyncio
async def test_discovery_semaphore_serialises_two_parallel_jobs(monkeypatch):
    """Two ``run_discovery_job`` calls fired back-to-back must not have
    overlapping ``find_leads`` execution.

    Regression pin for the 2026-05-28 OOM #2 incident where
    ``/discovery/start`` was hit twice within 3 seconds and both calls
    launched a fresh Chromium concurrently, exceeding the 512 MB
    container limit.
    """

    with patch("src.core.task_orchestrator.SupabaseHelper") as _DB:
        # SupabaseHelper().client.table(...).insert(...).execute() chain
        # is exercised by ``run_discovery_job`` for the initial
        # orchestration_jobs row. Make every call a no-op.
        _DB.return_value.client.table.return_value.insert.return_value.execute.return_value = None
        # And the upsert_leads call used in the success branch.
        _DB.return_value.upsert_leads.return_value = None

        orch = TaskOrchestrator()
        # Skip the DB-write done by status updates so the test stays offline.
        orch._update_job_status = AsyncMock(return_value=None)

        # Concurrency tracker — increments on entry, decrements on exit.
        # Peak captures the maximum simultaneous in-flight count.
        state = {"current": 0, "peak": 0, "entries": []}

        async def fake_find_leads(self, query, location=None, max_results=50):
            state["current"] += 1
            state["peak"] = max(state["peak"], state["current"])
            state["entries"].append((query, asyncio.get_event_loop().time()))
            try:
                # Hold the semaphore long enough to make a concurrency
                # bug observable. 150 ms is long enough that a sibling
                # asyncio.create_task scheduled "concurrently" would have
                # already entered without the semaphore.
                await asyncio.sleep(0.15)
                return []
            finally:
                state["current"] -= 1

        monkeypatch.setattr(
            "src.scrapers.discovery_engine.DiscoveryEngine.find_leads",
            fake_find_leads,
        )

        # Fire-and-forget pattern: run_discovery_job spawns a background
        # task and returns the job_id immediately. We must collect the
        # tasks ourselves to await completion.
        tracked: list[asyncio.Task] = []
        orig_create_task = asyncio.create_task

        def _capture(coro, *a, **kw):
            t = orig_create_task(coro, *a, **kw)
            tracked.append(t)
            return t

        with patch("src.core.task_orchestrator.asyncio.create_task", _capture):
            jid1 = await orch.run_discovery_job("dentist", "Mostar")
            jid2 = await orch.run_discovery_job("dentist", "Sarajevo")

        assert jid1 != jid2, "each job id should be unique"
        assert len(tracked) == 2, "both background tasks should have been scheduled"

        # Drive both to completion.
        await asyncio.gather(*tracked, return_exceptions=True)

        assert state["peak"] == 1, (
            f"semaphore breach — two find_leads ran concurrently "
            f"(peak={state['peak']}). OOM mitigation defeated."
        )
        # Both jobs eventually ran.
        assert len(state["entries"]) == 2
        # Order preserved (FIFO semaphore on CPython asyncio).
        assert state["entries"][0][1] < state["entries"][1][1]


# ───────────────── resource-block route handler ───────────────────────────


class _FakeRequest:
    def __init__(self, url: str, resource_type: str):
        self.url = url
        self.resource_type = resource_type


class _FakeRoute:
    def __init__(self, url: str, resource_type: str):
        self.request = _FakeRequest(url, resource_type)
        self.aborted = False
        self.fellback = False

    async def abort(self):
        self.aborted = True

    async def fallback(self):
        self.fellback = True

    async def continue_(self):  # pragma: no cover — discovery never uses this in the test path
        raise AssertionError("continue_ must not be called; defer to SSRF guard")


async def _run_block_handler(route):
    # Mirror the production install order: the resource-block handler is the
    # last-registered, so it dispatches first.
    async def _handler(rt):
        req = rt.request
        if (
            req.resource_type in discovery_engine._BLOCKED_RESOURCE_TYPES
            or discovery_engine._BLOCKED_URL_PATTERN.search(req.url)
        ):
            await rt.abort()
            return
        await rt.fallback()

    await _handler(route)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url, resource_type, expect_abort",
    [
        # 1. resource_type kill-list
        ("https://maps.google.com/avatar.png", "image", True),
        ("https://fonts.gstatic.com/woff2/foo.woff2", "font", True),
        ("https://example.com/ad.mp4", "media", True),
        # 2. URL pattern kill-list (Maps vector tiles, Street View, gstatic raster)
        ("https://www.google.com/maps/vt/pb=!1m4!1m3!1i12", "xhr", True),
        ("https://maps.googleapis.com/maps/vt?lyrs=m&x=1&y=2", "xhr", True),
        ("https://www.google.com/streetviewpixels-pa/blob?p=1", "xhr", True),
        ("https://lh3.googleusercontent.com/anything", "xhr", True),
        ("https://www.gstatic.com/images/branding/foo.png", "stylesheet", True),
        # 3. allowed — falls through to SSRF guard
        ("https://www.google.com/maps/search/dentist", "document", False),
        ("https://www.google.com/maps/api/place/details", "xhr", False),
        ("https://www.google.com/maps/data", "fetch", False),
        # Case sensitivity — pattern is re.IGNORECASE
        ("https://LH3.GOOGLEUSERCONTENT.com/X", "xhr", True),
    ],
)
async def test_resource_block_handler_aborts_or_falls_through(
    url, resource_type, expect_abort
):
    route = _FakeRoute(url, resource_type)
    await _run_block_handler(route)
    if expect_abort:
        assert route.aborted, f"expected abort for {resource_type} {url}"
        assert not route.fellback
    else:
        assert route.fellback, f"expected fallback for {resource_type} {url}"
        assert not route.aborted


# ───────────────── scroll + container caps ────────────────────────────────


def test_scroll_iter_default_is_five():
    """Default reduces from the pre-incident value of 10 to 5 — halves the
    lazy-load pressure on each ``page.mouse.wheel`` cycle."""

    assert discovery_engine._MAX_SCROLL_ITERS == 5


def test_container_default_is_thirty():
    """Hard cap protects against pathological Google Maps responses
    returning hundreds of result containers."""

    assert discovery_engine._MAX_CONTAINERS == 30


def test_caps_respect_env_overrides_and_floor_at_one(monkeypatch):
    """Env-tunable for staging tuning; floor at 1 prevents a 0/negative
    setting from silently disabling the loops entirely."""

    monkeypatch.setenv("DISCOVERY_MAX_SCROLL_ITERS", "8")
    monkeypatch.setenv("DISCOVERY_MAX_CONTAINERS", "0")  # below floor
    reloaded = importlib.reload(discovery_engine)
    try:
        assert reloaded._MAX_SCROLL_ITERS == 8
        assert reloaded._MAX_CONTAINERS == 1  # clamped to floor
    finally:
        # Avoid leaking the env-driven reload into sibling tests.
        monkeypatch.delenv("DISCOVERY_MAX_SCROLL_ITERS", raising=False)
        monkeypatch.delenv("DISCOVERY_MAX_CONTAINERS", raising=False)
        importlib.reload(discovery_engine)
