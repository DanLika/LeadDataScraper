"""Unit tests for src/utils/dispatch_policy.py.

Env-parsing fallback behaviour: every malformed value collapses to a
safe default rather than booting a broken policy. Window/day-of-week
checks must be correct on the boundary.
"""

from __future__ import annotations

import unittest

from src.utils.dispatch_policy import (
    DispatchPolicy,
    load_dispatch_policy,
)


class TestEnvParsing(unittest.TestCase):
    def test_defaults_when_env_empty(self) -> None:
        p = load_dispatch_policy({})
        self.assertEqual(p.daily_cap_per_mailbox, 30)
        self.assertEqual(p.warmup_per_mailbox, 10)
        self.assertEqual(p.send_window_start, "09:00")
        self.assertEqual(p.send_window_end, "17:00")
        self.assertEqual(p.send_days, ("mon", "tue", "wed", "thu", "fri"))
        self.assertEqual(p.timezone_mode, "lead")

    def test_explicit_values_roundtrip(self) -> None:
        p = load_dispatch_policy(
            {
                "EMAIL_DAILY_CAP_PER_MAILBOX": "50",
                "EMAIL_WARMUP_PER_MAILBOX": "20",
                "SEND_WINDOW_START": "08:30",
                "SEND_WINDOW_END": "18:00",
                "SEND_DAYS": "mon,wed,fri",
                "SEND_TIMEZONE_MODE": "UTC",
            }
        )
        self.assertEqual(p.daily_cap_per_mailbox, 50)
        self.assertEqual(p.warmup_per_mailbox, 20)
        self.assertEqual(p.send_window_start, "08:30")
        self.assertEqual(p.send_window_end, "18:00")
        self.assertEqual(p.send_days, ("mon", "wed", "fri"))
        self.assertEqual(p.timezone_mode, "UTC")

    def test_malformed_window_falls_back(self) -> None:
        p = load_dispatch_policy({"SEND_WINDOW_START": "garbage"})
        self.assertEqual(p.send_window_start, "09:00")
        p = load_dispatch_policy({"SEND_WINDOW_START": "25:00"})  # invalid hour
        self.assertEqual(p.send_window_start, "09:00")
        p = load_dispatch_policy({"SEND_WINDOW_START": "9"})  # missing colon
        self.assertEqual(p.send_window_start, "09:00")

    def test_malformed_send_days_falls_back(self) -> None:
        # Empty after filtering → default Mon-Fri.
        p = load_dispatch_policy({"SEND_DAYS": "garbage,zzz"})
        self.assertEqual(p.send_days, ("mon", "tue", "wed", "thu", "fri"))

    def test_unknown_tz_mode_falls_back_to_lead(self) -> None:
        p = load_dispatch_policy({"SEND_TIMEZONE_MODE": "guess"})
        self.assertEqual(p.timezone_mode, "lead")


class TestPolicyMethods(unittest.TestCase):
    def test_is_send_day_default_mon_fri(self) -> None:
        p = DispatchPolicy()
        for d in ("mon", "tue", "wed", "thu", "fri"):
            self.assertTrue(p.is_send_day(d), f"{d} should be a send day")
        for d in ("sat", "sun"):
            self.assertFalse(p.is_send_day(d), f"{d} should NOT be a send day")

    def test_is_send_day_case_insensitive(self) -> None:
        p = DispatchPolicy()
        self.assertTrue(p.is_send_day("Mon"))
        self.assertTrue(p.is_send_day("MON"))

    def test_is_in_window_inclusive_start_exclusive_end(self) -> None:
        p = DispatchPolicy()  # 09:00 - 17:00
        self.assertTrue(p.is_in_window("09:00"))
        self.assertTrue(p.is_in_window("12:00"))
        self.assertTrue(p.is_in_window("16:59"))
        self.assertFalse(p.is_in_window("17:00"))  # end is exclusive
        self.assertFalse(p.is_in_window("08:59"))
        self.assertFalse(p.is_in_window("23:00"))


if __name__ == "__main__":
    unittest.main()
