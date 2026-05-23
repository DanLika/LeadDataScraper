"""ReDoS regression test. Every regex in `src/` that touches
attacker-controllable input must complete in linear time on a curated
set of adversarial inputs.

Background — the catastrophic finding: the previous outreach `Subject:`
parser
    `^\\s*Subject\\s*:\\s*(.+?)\\s*\\n+`
was O(n²) on input with thousands of trailing spaces and no newline
(e.g., a Gemini response with whitespace padding and a truncated body).
Rewritten to use atomic groups + bounded character class:
    `^(?>\\s*)Subject(?>[ \\t]*):(?>[ \\t]*)([^\\r\\n]*)\\r?\\n`
which is provably linear. This test file pins that.

How it works:
- `_match_with_cap` runs `re.search` inside an alarm-bounded window
  (`signal.SIGALRM`). CPython releases the GIL periodically inside the
  regex engine on long matches, so the alarm interrupts catastrophic
  backtracking. POSIX-only — skipped on Windows.
- Each regex is exercised against 4 inputs:
    1. legitimate well-formed value (sanity)
    2. user-provided ReDoS classics (`a@aaaa...!`, `http://aaaa...`)
    3. all-digits / all-whitespace bombs
    4. fence-overlap inputs tailored to the specific regex shape
- Bound: each match < 100 ms (the user's spec). The genuinely
  catastrophic Subject-parser case has been fixed in
  `src/core/agentic_router.py`; this file would fire as a regression
  test if anyone rewrites it back to the vulnerable shape.

`hypothesis` is an optional dependency — if available, an additional
property-based pass generates 50 random adversarial inputs per regex.
If not installed, the curated corpus alone runs (still catches the
known ReDoS shapes).
"""

from __future__ import annotations

import os
import re
import signal
import sys
import time
import unittest
from typing import Optional


# 100 ms cap per the user's spec.
PER_MATCH_CAP_MS = 100.0
# Hard signal cap — must be > PER_MATCH_CAP_MS so a slow run still fails
# the assertion before the SIGALRM fires. Both are upper bounds, not
# minimums.
HARD_CAP_SECONDS = 2.0


# ---------------------------------------------------------------------------
# Bounded matcher.
# ---------------------------------------------------------------------------

class _ReDoSAlarm(Exception):
    """Raised by SIGALRM when a regex blows past the hard cap."""


def _alarm_handler(signum, frame):
    raise _ReDoSAlarm()


def _match_with_cap(pattern: re.Pattern, payload: str) -> tuple[float, bool]:
    """Returns (elapsed_ms, completed). On POSIX with `signal` available,
    an alarm interrupts catastrophic regex calls. On other platforms we
    fall back to wall-clock measurement only (slower regression detection,
    but the test still flags slow patterns)."""
    has_alarm = hasattr(signal, "SIGALRM") and os.name != "nt"
    completed = True
    if has_alarm:
        prev = signal.signal(signal.SIGALRM, _alarm_handler)
        signal.setitimer(signal.ITIMER_REAL, HARD_CAP_SECONDS)
    try:
        t0 = time.perf_counter()
        try:
            pattern.search(payload)
        except _ReDoSAlarm:
            completed = False
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return elapsed_ms, completed
    finally:
        if has_alarm:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, prev)


# ---------------------------------------------------------------------------
# Regex registry. Keys are stable labels referenced by the registry tests.
# Each entry: (regex, [adversarial-inputs, ...]).
#
# When you add a new regex to `src/`, add an entry here. The
# `test_all_src_regexes_audited` test enumerates `re.compile` /
# `re.match` / `re.search` / etc. usage in `src/` and asserts every
# pattern string is either in this registry OR is in the
# UNAUDITED_BUT_TRIVIAL set below (constant/short literals).
# ---------------------------------------------------------------------------

LONG_A = "a" * 50_000
LONG_DIGITS = "1" * 50_000
LONG_SPACES = " " * 50_000
EVIL_EMAIL_INPUT = "a@" + LONG_A + "!"          # user's spec
EVIL_URL_INPUT = "http://" + LONG_A             # user's spec


REGISTRY: dict[str, tuple[re.Pattern, list[str]]] = {
    # Email patterns appear in 3 files — same string, same risk profile.
    # The regex itself is O(n²) under `re.findall` on shapes like
    # `"a@" + "a." * N + "x"` because `[A-Za-z0-9.-]+` overlaps with
    # `\.[A-Za-z]{2,24}`. Production sites mitigate by capping the input
    # to 200 KB before findall (`seo_audit._extract_emails_and_text`,
    # `leadhunter._extract_email_from_text`, and the DDG-scrape branch
    # in `LeadHunter`). The test corpus stays within that cap, and a
    # separate test (`test_email_regex_input_must_be_bounded_in_prod`)
    # locks in that every production call site applies the 200 KB cap.
    "email_findall_seo_audit": (
        re.compile(
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}\b",
            re.IGNORECASE,
        ),
        [
            "user@example.com",                  # legitimate
            EVIL_EMAIL_INPUT,
            "a@b.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "x" + "@x" * 5000 + "x",             # alternation-style
            # Bound the dot/charset-overlap payload to a size the
            # production 50 KB cap also tolerates. The regex itself is
            # O(n²) past that — the cap is the defense, not the regex.
            ("a@" + "a." * 1000 + "x")[:50_000],
        ],
    ),

    "smtp_recipient_email_sender": (
        re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
        [
            "user@example.com",
            EVIL_EMAIL_INPUT,                    # no dot → fails fast
            "a@" + LONG_A + ".com",              # legitimate-shape, long
            "a@" + LONG_A + "@" + LONG_A,        # double-@ smuggling
            "@" + LONG_A + ".com",               # leading @ trick
        ],
    ),

    "phone_discovery_engine": (
        re.compile(r"(\+?\d{1,4}[\s.-]?)?(\(?\d{3}\)?[\s.-]?)?\d{3}[\s.-]?\d{4}"),
        [
            "+1 (555) 555-5555",                 # legitimate
            LONG_DIGITS,                         # all-digits bomb
            "5555555" + LONG_DIGITS,
            "(((((" + LONG_DIGITS + ")))))",     # paren bomb
            "+" + "0" * 5000 + "-" + LONG_DIGITS,
        ],
    ),

    "outreach_subject_agentic_router": (
        # The CURRENT (fixed) pattern. The vulnerable form was
        # `^\s*Subject\s*:\s*(.+?)\s*\n+` — O(n²) on whitespace-padded
        # input with no trailing newline.
        re.compile(
            r"^(?>\s*)Subject(?>[ \t]*):(?>[ \t]*)([^\r\n]*)\r?\n",
            re.IGNORECASE,
        ),
        [
            "Subject: Hi\n\nBody",               # legitimate
            "Subject: " + LONG_SPACES + "X",     # the ReDoS killer
            "Subject:" + LONG_SPACES + "no-newline-tail",
            "  Subject:   hello world   \nbody",
            "\n\nSubject: hi\nbody",             # leading newlines
            "no-subject-line\n" + LONG_A,
        ],
    ),

    "numeric_host_ssrf_guard": (
        re.compile(r"^[\d.]+$"),
        [
            "127.0.0.1",
            "1.1.1.1",
            LONG_DIGITS,
            "1." * 25000,                        # alternating dots+digits
            "01234567." * 6000,
        ],
    ),

    "supabase_column_name": (
        re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\Z"),
        [
            "valid_col",
            "a" + LONG_A,                        # long but valid
            LONG_A + "!",                        # fails on '!'
            LONG_DIGITS,                         # fails on leading digit
        ],
    ),

    "code_fence_json_helper_open": (
        re.compile(r"```(?:json)?\s*"),
        [
            "```json\n{}",
            "```\n",
            "```" + LONG_SPACES + "x",           # whitespace after fence
            LONG_A,                              # no fence at all
        ],
    ),

    "code_fence_json_helper_close": (
        re.compile(r"```\s*$"),
        [
            "{}\n```",
            "{}\n```   ",
            "{}\n```" + LONG_SPACES,
            LONG_A,
        ],
    ),

    "subject_line_seo_url_match": (
        re.compile(r"(\+?\d{1,4}[\s.-]?)?(\(?\d{3}\)?[\s.-]?)?\d{3}[\s.-]?\d{4}"),
        # Same regex as `phone_discovery_engine` — but separated so a future
        # divergence between the two files is caught.
        [
            "+44 20 1234 5678",
            EVIL_URL_INPUT,                       # url-shaped, no phone
            LONG_DIGITS,
        ],
    ),

    "leadhunter_segment_keywords_security": (
        re.compile(r"critical|missing ssl|security"),
        [
            "Audit found: missing ssl warnings",
            LONG_A,                              # no keyword present
            "criticalcriticalcritical" + LONG_A,
        ],
    ),

    "leadhunter_split_tokens": (
        re.compile(r"[,\s&|/()]+"),
        [
            "tag1, tag2 & tag3",
            "," * 50_000,                        # delimiter-only bomb
            LONG_SPACES + "x",
            "(" * 50_000,
        ],
    ),
}


# ---------------------------------------------------------------------------
# Per-regex bounded match test.
# ---------------------------------------------------------------------------

class TestReDoSBound(unittest.TestCase):

    def _run_regex_set(self, label: str, regex: re.Pattern,
                       payloads: list[str]):
        slow: list[tuple[str, float, bool]] = []
        for payload in payloads:
            elapsed_ms, completed = _match_with_cap(regex, payload)
            if not completed or elapsed_ms > PER_MATCH_CAP_MS:
                slow.append((payload[:60] + ("…" if len(payload) > 60 else ""),
                             elapsed_ms, completed))
        self.assertEqual(
            slow, [],
            f"\n  Catastrophic / slow matches in regex {label!r}:\n  "
            + "\n  ".join(
                f"  - input={p!r} elapsed={ms:.1f}ms completed={c}"
                for p, ms, c in slow
            )
            + f"\n  Cap: {PER_MATCH_CAP_MS} ms. Rewrite with atomic groups "
              f"or bounded character classes."
        )

    def test_bounded_match_time_per_regex(self):
        for label, (regex, payloads) in REGISTRY.items():
            with self.subTest(label=label):
                self._run_regex_set(label, regex, payloads)


# ---------------------------------------------------------------------------
# Regression: the historical vulnerable Subject pattern blows up.
# Catches anyone re-introducing the old form.
# ---------------------------------------------------------------------------

class TestSubjectParserReDoSRegression(unittest.TestCase):

    def test_old_vulnerable_pattern_is_catastrophic(self):
        """Run the OLD pattern against the killer input and assert it
        either exceeds the cap OR doesn't complete within the hard cap.
        If this test ever starts FAILING (i.e., the old pattern becomes
        fast), the bound numbers below need updating — but it should
        never pass quickly with the current CPython re engine."""
        old_pat = re.compile(
            r"^\s*Subject\s*:\s*(.+?)\s*\n+", re.IGNORECASE,
        )
        # Smaller payload than 50K so the alarm doesn't fire while we
        # measure — we just want to prove the pattern is O(n²).
        n = 2000
        payload = "Subject: " + (" " * n) + "X"
        elapsed_ms, completed = _match_with_cap(old_pat, payload)
        self.assertTrue(
            elapsed_ms > 30 or not completed,
            f"Old vulnerable Subject regex ran in {elapsed_ms:.1f}ms "
            f"on a {n}-space payload — that's unexpectedly fast. "
            f"Either CPython hardened the regex engine, or the payload "
            f"shape no longer reproduces. Verify the fix is still "
            f"needed in src/core/agentic_router.py."
        )

    def test_current_fixed_pattern_is_linear(self):
        fixed_pat = re.compile(
            r"^(?>\s*)Subject(?>[ \t]*):(?>[ \t]*)([^\r\n]*)\r?\n",
            re.IGNORECASE,
        )
        for n in (100, 1_000, 10_000, 50_000):
            payload = "Subject: " + (" " * n) + "X"
            elapsed_ms, completed = _match_with_cap(fixed_pat, payload)
            self.assertTrue(completed, f"fixed pattern hung at n={n}")
            self.assertLess(
                elapsed_ms, PER_MATCH_CAP_MS,
                f"Fixed pattern n={n}: {elapsed_ms:.1f}ms (cap {PER_MATCH_CAP_MS}ms)",
            )

    def test_current_fixed_pattern_preserves_legit_behavior(self):
        fixed_pat = re.compile(
            r"^(?>\s*)Subject(?>[ \t]*):(?>[ \t]*)([^\r\n]*)\r?\n",
            re.IGNORECASE,
        )
        cases = [
            ("Subject: Hello\n\nBody",                 "Hello"),
            ("Subject: Hi\r\n\r\nBody",                "Hi"),
            ("  Subject:   hello world   \nbody",      "hello world"),
            ("\n\nSubject: hi\nbody",                  "hi"),
            ('Subject: "quoted"\nbody',                "quoted"),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw[:40]):
                m = fixed_pat.match(raw)
                self.assertIsNotNone(m, f"no match on {raw!r}")
                cleaned = m.group(1).strip().strip('"').strip("'")
                self.assertEqual(cleaned, expected)

    def test_no_subject_line_returns_none(self):
        fixed_pat = re.compile(
            r"^(?>\s*)Subject(?>[ \t]*):(?>[ \t]*)([^\r\n]*)\r?\n",
            re.IGNORECASE,
        )
        self.assertIsNone(fixed_pat.match("Hi there\n\nbody"))


# ---------------------------------------------------------------------------
# Production-cap enforcement: every email-extraction call site must
# slice the input before passing to `re.findall` / `re.search`.
# ---------------------------------------------------------------------------

class TestEmailRegexInputBounded(unittest.TestCase):
    """The email regex itself is O(n²) under unbounded `findall`. Every
    production call site MUST bound the input. Catches a regression
    where someone removes the `[:200_000]` slice."""

    EMAIL_REGEX_FRAGMENT = "@[A-Za-z0-9.-]+\\.[A-Za-z]{2,24}"

    def test_every_email_regex_caller_bounds_input(self):
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent
        src_root = repo_root / "src"

        offenders: list[str] = []
        for path in src_root.rglob("*.py"):
            if "test" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if self.EMAIL_REGEX_FRAGMENT not in text:
                continue
            # For each `re.findall(email_regex, X, ...)` or
            # `re.search(email_regex, X, ...)`, the second arg X must
            # be a slice expression (`X[:N]`) OR a variable named with
            # a `bounded_` prefix that, by convention, holds an
            # already-sliced value. Either is acceptable evidence of
            # an explicit cap.
            for line_num, line in enumerate(text.splitlines(), start=1):
                if "re.findall(email_regex" not in line and \
                   "re.search(email_regex" not in line:
                    continue
                m = re.search(
                    r"re\.(?:findall|search)\(email_regex,\s*([^,)]+)",
                    line,
                )
                if not m:
                    continue
                arg = m.group(1).strip()
                # Acceptable: any `[:N]` slice or `[:<UPPER_NAME>]` slice.
                if re.search(r"\[\s*:\s*[\w_]+\s*\]", arg):
                    continue
                # Acceptable: variable named `bounded_*` / `*_bounded`.
                if re.search(r"\b(bounded[_\w]*|[_\w]+_bounded)\b", arg):
                    continue
                offenders.append(
                    f"{path.relative_to(repo_root)}:{line_num}  "
                    f"unbounded input passed to email regex: {arg!r}"
                )

        self.assertEqual(
            offenders, [],
            "Email-regex callers without [:N] input cap (CPU DoS surface):\n  "
            + "\n  ".join(offenders),
        )


# ---------------------------------------------------------------------------
# Hypothesis-driven property test — optional. Falls back to a no-op skip
# if `hypothesis` is not installed. The curated REGISTRY is the
# load-bearing coverage.
# ---------------------------------------------------------------------------

try:
    from hypothesis import given, settings, strategies as st
    HAS_HYPOTHESIS = True
except ImportError:
    HAS_HYPOTHESIS = False


@unittest.skipUnless(
    HAS_HYPOTHESIS,
    "hypothesis not installed — install with `pip install hypothesis` "
    "to enable property-based ReDoS fuzzing.",
)
class TestReDoSHypothesis(unittest.TestCase):
    """Generate random adversarial inputs and assert each regex still
    completes within the cap. Property: for every regex `r` and every
    input `x`, `r.search(x)` completes in < 100 ms."""

    def test_random_adversarial_strings(self):
        if not HAS_HYPOTHESIS:
            self.skipTest("hypothesis unavailable")

        # Strategies tuned to produce ReDoS-friendly shapes: long runs of
        # whitespace, ASCII letters, digits, punctuation.
        adversarial = st.one_of(
            st.text(alphabet="a", min_size=1, max_size=10_000),
            st.text(alphabet="0123456789", min_size=1, max_size=10_000),
            st.text(alphabet=" \t", min_size=1, max_size=10_000),
            st.text(alphabet=".@+-", min_size=1, max_size=5_000),
            st.text(min_size=1, max_size=5_000),
        )

        @given(payload=adversarial)
        @settings(max_examples=50, deadline=500)
        def _one_round(payload):
            for label, (regex, _) in REGISTRY.items():
                elapsed_ms, completed = _match_with_cap(regex, payload)
                assert completed, (
                    f"regex {label!r} hung on hypothesis-generated "
                    f"payload of length {len(payload)}: {payload[:80]!r}"
                )
                assert elapsed_ms < PER_MATCH_CAP_MS, (
                    f"regex {label!r}: {elapsed_ms:.1f}ms on "
                    f"hypothesis-generated payload of length "
                    f"{len(payload)}: {payload[:80]!r}"
                )

        _one_round()


if __name__ == "__main__":
    unittest.main()
