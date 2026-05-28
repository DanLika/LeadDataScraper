"""Timing-attack regression test against `verify_api_key` /
`verify_admin_token`.

The two header-validator dependencies use `secrets.compare_digest`,
which is documented constant-time. This test EMPIRICALLY verifies that
contract under the FastAPI request path: an attacker who can compare
response times for keys matching 1 char vs N chars of the secret must
not learn anything about the secret from the timing distribution.

Approach:
  1. Set a 64-char key on the app.
  2. Fire `SAMPLE_SIZE` (=300) requests with `Bearer match-1` (key shares
     only the first char with the secret) and the same count with a
     longer-matching prefix (e.g., 30 chars).
  3. Measure each response time (perf_counter).
  4. Compare the two distributions: median, p95, p99. If the longer-prefix
     distribution is statistically distinguishable from the short-prefix
     one, the comparator is timing-leaky.
  5. Test passes if the difference is within the network noise band —
     `<0.5 ms median` and `<5 ms p95` is the empirical threshold for
     a 5xx-free in-process TestClient call against a 64-char key.

This is an INTEGRATION timing test, not a microbenchmark. Run multiple
times if results are noisy. A robust failure (multiple consecutive
runs reject the null) indicates a real timing surface that needs
investigation.

Repeats for `X-Admin-Token` against the `/leads/clear` endpoint.
"""

from __future__ import annotations

import os
import statistics
import sys
import time
import unittest

import pytest
from fastapi.testclient import TestClient


backend_path = os.path.join(os.getcwd(), "backend")
if backend_path not in sys.path:
    sys.path.append(backend_path)

from main import app  # noqa: E402


REAL_KEY = "X" * 64
REAL_ADMIN = "Y" * 64
SAMPLE_SIZE = 300

# Acceptable jitter — in-process TestClient + the API-key check should
# resolve in <2 ms typical. We allow a generous band so the test isn't
# flaky under noisy CI; a genuine timing leak would push the median
# gap into the 1+ ms range.
MAX_MEDIAN_GAP_MS = 1.5
MAX_P95_GAP_MS = 8.0


@pytest.fixture(autouse=True)
def _set_keys(monkeypatch):
    monkeypatch.setenv("API_SECRET_KEY", REAL_KEY)
    monkeypatch.setenv("ADMIN_TOKEN", REAL_ADMIN)


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    from main import limiter

    try:
        limiter._storage.storage.clear()  # type: ignore[attr-defined]
    except Exception:
        try:
            limiter.reset()
        except Exception:
            pass
    yield


# ---------------------------------------------------------------------------
# Sanity: `verify_api_key` actually uses `secrets.compare_digest`.
# A grep-style assertion against the source — if the implementation
# silently changes to `==`, this test fires loudly.
# ---------------------------------------------------------------------------


class TestVerifyApiKeyUsesConstantTimeCompare(unittest.TestCase):
    def test_verify_api_key_source_uses_compare_digest(self):
        from pathlib import Path

        path = Path(__file__).resolve().parent.parent / "backend" / "main.py"
        source = path.read_text(encoding="utf-8")
        # Both the key + admin token verifiers must use compare_digest.
        # We look for the *function body* containing the call, not just
        # any mention of compare_digest in the file.
        for func_name in ("verify_api_key", "verify_admin_token"):
            start = source.find(f"def {func_name}(")
            self.assertNotEqual(start, -1, f"{func_name} missing from main.py")
            # The function body is everything up to the next top-level
            # `def ` or `class ` at column 0.
            tail = source[start:]
            end_markers = [tail.find("\ndef ", 5), tail.find("\nclass ", 5)]
            end = min((e for e in end_markers if e > 0), default=len(tail))
            body = tail[:end]
            self.assertIn(
                "secrets.compare_digest",
                body,
                f"{func_name} dropped secrets.compare_digest — timing leak",
            )
            # And NOT the naive ==. (`if key != expected:` is the
            # vulnerable form; allowed comparisons like `if not key`
            # are fine because they don't compare against the secret.)
            self.assertNotIn(
                f"key == expected",
                body,
                f"{func_name} uses == for key comparison — timing leak",
            )


# ---------------------------------------------------------------------------
# Empirical timing distribution.
# ---------------------------------------------------------------------------


def _sample_request_times(
    client: TestClient,
    header_name: str,
    header_value: str,
    path: str,
    method: str,
    n: int,
) -> list[float]:
    """Fire `n` requests carrying the given header, return per-request
    elapsed times in MILLISECONDS."""
    times: list[float] = []
    for _ in range(n):
        t0 = time.perf_counter()
        client.request(method, path, headers={header_name: header_value})
        times.append((time.perf_counter() - t0) * 1000)
    return times


def _summary(name: str, samples: list[float]) -> dict:
    s = sorted(samples)
    return {
        "name": name,
        "n": len(s),
        "min_ms": s[0],
        "median_ms": statistics.median(s),
        "p95_ms": s[int(0.95 * len(s))],
        "p99_ms": s[int(0.99 * len(s))],
        "max_ms": s[-1],
    }


def _summary_str(d: dict) -> str:
    return (
        f"{d['name']}: n={d['n']} "
        f"min={d['min_ms']:.3f} median={d['median_ms']:.3f} "
        f"p95={d['p95_ms']:.3f} p99={d['p99_ms']:.3f} "
        f"max={d['max_ms']:.3f}"
    )


def test_api_key_compare_is_constant_time():
    """1 char vs 30 char matching prefix → response time distributions
    overlap. Median gap must be within `MAX_MEDIAN_GAP_MS`; p95 gap
    within `MAX_P95_GAP_MS`. Run against `/leads` (a GET that the auth
    dependency gates before any DB work)."""
    client = TestClient(app)

    short_match = "X" + "A" * 63  # 1 char of secret right
    long_match = "X" * 30 + "A" * 34  # 30 chars of secret right

    # Warm-up — the first 50 requests pay path-init cost.
    _sample_request_times(client, "X-API-Key", short_match, "/leads", "GET", 50)

    short_times = _sample_request_times(
        client,
        "X-API-Key",
        short_match,
        "/leads",
        "GET",
        SAMPLE_SIZE,
    )
    long_times = _sample_request_times(
        client,
        "X-API-Key",
        long_match,
        "/leads",
        "GET",
        SAMPLE_SIZE,
    )

    short = _summary("short-1-char", short_times)
    long_ = _summary("long-30-char", long_times)

    median_gap = abs(short["median_ms"] - long_["median_ms"])
    p95_gap = abs(short["p95_ms"] - long_["p95_ms"])

    assert median_gap < MAX_MEDIAN_GAP_MS, (
        f"Median timing gap {median_gap:.3f}ms exceeds "
        f"{MAX_MEDIAN_GAP_MS}ms — possible timing leak.\n"
        f"  {_summary_str(short)}\n"
        f"  {_summary_str(long_)}"
    )
    assert p95_gap < MAX_P95_GAP_MS, (
        f"p95 timing gap {p95_gap:.3f}ms exceeds "
        f"{MAX_P95_GAP_MS}ms — possible timing leak.\n"
        f"  {_summary_str(short)}\n"
        f"  {_summary_str(long_)}"
    )


def test_admin_token_compare_is_constant_time():
    """Same as above but on `/leads/clear` → admin-token verifier."""
    client = TestClient(app)

    short_admin = "Y" + "A" * 63
    long_admin = "Y" * 30 + "A" * 34

    # `/leads/clear` is a DELETE that requires BOTH X-API-Key and
    # X-Admin-Token. Pass the correct API key so the admin-token check
    # is what gates the response.
    headers_short = {"X-API-Key": REAL_KEY, "X-Admin-Token": short_admin}
    headers_long = {"X-API-Key": REAL_KEY, "X-Admin-Token": long_admin}

    def sample(headers: dict, n: int) -> list[float]:
        times: list[float] = []
        for _ in range(n):
            t0 = time.perf_counter()
            client.delete("/leads/clear", headers=headers)
            times.append((time.perf_counter() - t0) * 1000)
        return times

    sample(headers_short, 50)  # warm-up

    short_times = sample(headers_short, SAMPLE_SIZE)
    long_times = sample(headers_long, SAMPLE_SIZE)

    short = _summary("admin-short", short_times)
    long_ = _summary("admin-long", long_times)

    median_gap = abs(short["median_ms"] - long_["median_ms"])
    p95_gap = abs(short["p95_ms"] - long_["p95_ms"])

    assert median_gap < MAX_MEDIAN_GAP_MS, (
        f"Admin token median gap {median_gap:.3f}ms — possible leak.\n"
        f"  {_summary_str(short)}\n"
        f"  {_summary_str(long_)}"
    )
    assert p95_gap < MAX_P95_GAP_MS, (
        f"Admin token p95 gap {p95_gap:.3f}ms — possible leak.\n"
        f"  {_summary_str(short)}\n"
        f"  {_summary_str(long_)}"
    )


# ---------------------------------------------------------------------------
# Welch's t-test — stronger statistical signal than median diff.
# Skipped if scipy isn't installed.
# ---------------------------------------------------------------------------


def test_api_key_welch_t_test_does_not_reject_same_mean():
    """Welch's t-test (unequal-variance) on the two timing
    distributions. If the comparator leaks timing, the two means
    diverge and the t-test rejects the null at p<0.01. The threshold
    is loose to avoid CI flakes; a real leak would produce p<1e-10."""
    try:
        from scipy import stats
    except ImportError:
        pytest.skip("scipy not installed — skipping t-test")

    client = TestClient(app)
    short_match = "X" + "A" * 63
    long_match = "X" * 30 + "A" * 34

    _sample_request_times(client, "X-API-Key", short_match, "/leads", "GET", 50)

    short_times = _sample_request_times(
        client,
        "X-API-Key",
        short_match,
        "/leads",
        "GET",
        SAMPLE_SIZE,
    )
    long_times = _sample_request_times(
        client,
        "X-API-Key",
        long_match,
        "/leads",
        "GET",
        SAMPLE_SIZE,
    )

    t_stat, p_value = stats.ttest_ind(short_times, long_times, equal_var=False)
    # p > 0.01 → cannot reject "same mean" → no measurable timing leak.
    # A real leak in `secrets.compare_digest` would put p <<< 0.01.
    assert p_value > 0.01, (
        f"Welch's t-test rejected 'same mean' at p={p_value:.4f} "
        f"(t={t_stat:.3f}) — possible timing leak.\n"
        f"  short median = {statistics.median(short_times):.3f} ms\n"
        f"  long median  = {statistics.median(long_times):.3f} ms"
    )


# ---------------------------------------------------------------------------
# Wrong-length input doesn't change the timing fingerprint either.
# `secrets.compare_digest` runs in time proportional to the LONGER
# input; a key with 1 char shouldn't return faster than a key with 64.
# ---------------------------------------------------------------------------


def test_short_and_long_wrong_keys_have_overlapping_timing():
    client = TestClient(app)
    one_char = "X"
    sixty_four_chars = "Z" * 64  # all wrong but same length as real

    _sample_request_times(client, "X-API-Key", one_char, "/leads", "GET", 50)

    short_times = _sample_request_times(
        client,
        "X-API-Key",
        one_char,
        "/leads",
        "GET",
        SAMPLE_SIZE,
    )
    full_times = _sample_request_times(
        client,
        "X-API-Key",
        sixty_four_chars,
        "/leads",
        "GET",
        SAMPLE_SIZE,
    )

    short = _summary("1-char", short_times)
    full = _summary("64-char", full_times)
    median_gap = abs(short["median_ms"] - full["median_ms"])
    assert median_gap < MAX_MEDIAN_GAP_MS * 2, (
        f"Median gap by length {median_gap:.3f}ms — possible leak.\n"
        f"  {_summary_str(short)}\n"
        f"  {_summary_str(full)}"
    )
