"""Snapshot test pinning the PR #359 bounce-type discrimination policy.

If any cell in the policy table below changes, this test fails loud
— forcing a coordinated update of the snapshot AND a code review of
the new policy. The taxonomy mirrors
``suppressions_reason_allowed`` in supabase_schema.sql.

Run targeted: ``pytest tests/unit/test_bounce_policy.py -v``
"""
from __future__ import annotations

import logging
import unittest

from src.integrations.instantly_webhook_handler import (
    BounceAction,
    SOFT_COUNTER_WINDOW_DAYS,
    SOFT_THRESHOLD,
    decide_bounce_action,
)


class TestBouncePolicySnapshot(unittest.TestCase):
    """Pin every cell of the (bounce_type x prior_soft_count) policy table."""

    # The full table — adding a row here without an accompanying change
    # to ``decide_bounce_action`` is the desired friction.
    POLICY_TABLE: list[tuple[str | None, int, BounceAction]] = [
        # --- hard family: always immediate suppression ---
        ("hard", 0, "suppress_hard"),
        ("hard", 99, "suppress_hard"),
        ("HARD", 0, "suppress_hard"),          # case-insensitive
        ("  hard  ", 0, "suppress_hard"),      # whitespace-trimmed
        ("permanent", 0, "suppress_hard"),     # Instantly synonym
        ("blocked", 0, "suppress_hard"),       # 550 recipient refusal
        ("rejected", 0, "suppress_hard"),      # spam-block etc.

        # --- soft family: noop under threshold, escalate at/over ---
        ("soft", 0, "noop_soft"),
        ("soft", 1, "noop_soft"),
        ("soft", 2, "noop_soft"),
        ("soft", 3, "suppress_soft_3x"),       # threshold reached (this is strike 3+)
        ("soft", 4, "suppress_soft_3x"),
        ("soft", 99, "suppress_soft_3x"),
        ("SOFT", 3, "suppress_soft_3x"),       # case-insensitive
        ("Transient", 3, "suppress_soft_3x"),  # synonym
        ("temporary", 3, "suppress_soft_3x"),
        ("deferred", 0, "noop_soft"),
        ("deferred", 3, "suppress_soft_3x"),

        # --- absent / empty: defensive → suppress_hard ---
        (None, 0, "suppress_hard"),
        ("", 0, "suppress_hard"),
        ("   ", 0, "suppress_hard"),

        # --- unknown values: defensive → suppress_hard (+ WARN logged) ---
        ("mystery", 0, "suppress_hard"),
        ("greylisted", 99, "suppress_hard"),   # not in _SOFT_TYPES yet
        ("delayed", 0, "suppress_hard"),
    ]

    def test_policy_table(self) -> None:
        """Every (bounce_type, count) → BounceAction mapping must hold."""
        failures: list[str] = []
        for bounce_type, count, expected in self.POLICY_TABLE:
            actual = decide_bounce_action(bounce_type, count)
            if actual != expected:
                failures.append(
                    f"  bounce_type={bounce_type!r:<15} count={count:<3} "
                    f"expected={expected!r:<20} got={actual!r}"
                )
        if failures:
            self.fail(
                "Bounce policy snapshot drift:\n" + "\n".join(failures)
                + "\n\nIf this change is intentional, update POLICY_TABLE "
                "in this test AND `decide_bounce_action` in lockstep, "
                "then resnapshot."
            )

    def test_threshold_constant_matches_reason_taxonomy(self) -> None:
        """SOFT_THRESHOLD=3 must align with `bounce_soft_3x` reason name."""
        self.assertEqual(SOFT_THRESHOLD, 3,
                         "SOFT_THRESHOLD drift would orphan the bounce_soft_3x "
                         "suppression-reason taxonomy slot.")

    def test_counter_window_default(self) -> None:
        """30-day window is the documented default. Catches silent change."""
        self.assertEqual(SOFT_COUNTER_WINDOW_DAYS, 30)


class TestBoundaryConditions(unittest.TestCase):
    """Edge cases not in the main snapshot table."""

    def test_explicit_threshold_override(self) -> None:
        # Caller can pin a stricter or looser threshold per call site.
        self.assertEqual(decide_bounce_action("soft", 2, threshold=2), "suppress_soft_3x")
        self.assertEqual(decide_bounce_action("soft", 1, threshold=2), "noop_soft")
        self.assertEqual(decide_bounce_action("soft", 4, threshold=5), "noop_soft")
        self.assertEqual(decide_bounce_action("soft", 5, threshold=5), "suppress_soft_3x")

    def test_zero_threshold_always_escalates_softs(self) -> None:
        # Degenerate but well-defined. Useful for runbook/incident toggles.
        self.assertEqual(decide_bounce_action("soft", 0, threshold=0), "suppress_soft_3x")

    def test_hard_ignores_threshold(self) -> None:
        # threshold only meaningful for the soft path.
        self.assertEqual(decide_bounce_action("hard", 0, threshold=999), "suppress_hard")
        self.assertEqual(decide_bounce_action("hard", 999, threshold=0), "suppress_hard")

    def test_negative_count_treated_as_zero_path(self) -> None:
        # Defensive: count source could return a negative on edge math; soft
        # branch's < threshold check still holds (no escalation).
        self.assertEqual(decide_bounce_action("soft", -5), "noop_soft")


class TestUnknownTypeLogsWarning(unittest.TestCase):
    """Unknown bounce_type values must emit a WARN — that's the operator
    signal to widen the allowlist."""

    def test_unknown_type_logs_warning(self) -> None:
        with self.assertLogs(
            "src.integrations.instantly_webhook_handler",
            level=logging.WARNING,
        ) as cm:
            result = decide_bounce_action("alien-bounce-class", 0)
        self.assertEqual(result, "suppress_hard")
        self.assertTrue(any("unknown bounce_type" in line for line in cm.output),
                        f"expected unknown bounce_type warn, got: {cm.output}")

    def test_known_types_do_not_warn(self) -> None:
        # Sanity: don't spam WARN on every normal event.
        logger = logging.getLogger("src.integrations.instantly_webhook_handler")
        with self.assertNoLogs(logger.name, level=logging.WARNING):
            decide_bounce_action("hard", 0)
            decide_bounce_action("soft", 0)
            decide_bounce_action(None, 0)
            decide_bounce_action("", 0)


if __name__ == "__main__":
    unittest.main()
