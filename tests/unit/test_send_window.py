"""Unit tests for src/utils/send_window.py.

Covers:
- In-window happy path (weekday + time inside)
- Out-of-window day (Sat / Sun by default)
- Out-of-window time (after 17:00 → next morning)
- Out-of-window time (before 09:00 → today at 09:00)
- Friday after-hours → Monday morning
- Custom send_days subset (mon, wed, fri)
- Custom window 08:00–18:00
- Malformed inputs fall back to defaults
- TZ resolution: explicit > env > UTC
- HH:MM:SS shape (PostgREST TIME column)
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from src.utils.send_window import is_within_window


# Default test step config — matches the schema defaults.
DEFAULT_KW = dict(
    step_send_window_start="09:00",
    step_send_window_end="17:00",
    step_send_days="mon,tue,wed,thu,fri",
)


class TestInWindow(unittest.TestCase):
    def test_tuesday_noon_utc_in_window(self) -> None:
        # 2026-05-26 is a Tuesday; 12:00 UTC = inside 09-17 in UTC.
        now = datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc)
        result = is_within_window(timezone_name="UTC", now_utc=now, **DEFAULT_KW)
        self.assertTrue(result.in_window)
        self.assertIsNone(result.next_window_start_utc)

    def test_saturday_returns_monday(self) -> None:
        # 2026-05-30 is Saturday. Next valid = 2026-06-01 (Mon) 09:00 UTC.
        now = datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)
        result = is_within_window(timezone_name="UTC", now_utc=now, **DEFAULT_KW)
        self.assertFalse(result.in_window)
        nxt = result.next_window_start_utc
        self.assertIsNotNone(nxt)
        self.assertEqual(nxt.weekday(), 0)  # Monday
        self.assertEqual(nxt.hour, 9)
        self.assertEqual(nxt.day, 1)

    def test_friday_after_hours_returns_monday(self) -> None:
        # 2026-05-29 Fri 18:30 UTC → after window → Mon 2026-06-01 09:00.
        now = datetime(2026, 5, 29, 18, 30, tzinfo=timezone.utc)
        result = is_within_window(timezone_name="UTC", now_utc=now, **DEFAULT_KW)
        self.assertFalse(result.in_window)
        nxt = result.next_window_start_utc
        self.assertEqual(nxt.weekday(), 0)  # Monday
        self.assertEqual(nxt.day, 1)

    def test_weekday_before_window_returns_today(self) -> None:
        # 2026-05-26 Tue 07:30 UTC → before 09:00, same-day open.
        now = datetime(2026, 5, 26, 7, 30, tzinfo=timezone.utc)
        result = is_within_window(timezone_name="UTC", now_utc=now, **DEFAULT_KW)
        self.assertFalse(result.in_window)
        nxt = result.next_window_start_utc
        self.assertEqual(nxt.weekday(), 1)  # Tuesday
        self.assertEqual(nxt.day, 26)
        self.assertEqual(nxt.hour, 9)
        self.assertEqual(nxt.minute, 0)

    def test_window_exclusive_at_end(self) -> None:
        # Exact 17:00 → exclusive, NOT in window. Matches DispatchPolicy semantics.
        now = datetime(2026, 5, 26, 17, 0, tzinfo=timezone.utc)
        result = is_within_window(timezone_name="UTC", now_utc=now, **DEFAULT_KW)
        self.assertFalse(result.in_window)

    def test_window_inclusive_at_start(self) -> None:
        now = datetime(2026, 5, 26, 9, 0, tzinfo=timezone.utc)
        result = is_within_window(timezone_name="UTC", now_utc=now, **DEFAULT_KW)
        self.assertTrue(result.in_window)


class TestCustomConfigs(unittest.TestCase):
    def test_mon_wed_fri_skips_tuesday(self) -> None:
        # Tuesday 12:00 with send_days='mon,wed,fri' → out of window.
        now = datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc)
        result = is_within_window(
            step_send_window_start="09:00",
            step_send_window_end="17:00",
            step_send_days="mon,wed,fri",
            timezone_name="UTC",
            now_utc=now,
        )
        self.assertFalse(result.in_window)
        nxt = result.next_window_start_utc
        self.assertEqual(nxt.weekday(), 2)  # Wednesday

    def test_extended_window_08_to_19(self) -> None:
        # Tue 18:30 with 08-19 window → in window.
        now = datetime(2026, 5, 26, 18, 30, tzinfo=timezone.utc)
        result = is_within_window(
            step_send_window_start="08:00",
            step_send_window_end="19:00",
            step_send_days="mon,tue,wed,thu,fri",
            timezone_name="UTC",
            now_utc=now,
        )
        self.assertTrue(result.in_window)

    def test_hhmmss_shape(self) -> None:
        """PostgREST surfaces TIME columns as ``HH:MM:SS``; parser
        accepts either form."""
        now = datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc)
        result = is_within_window(
            step_send_window_start="09:00:00",
            step_send_window_end="17:00:00",
            step_send_days="mon,tue,wed,thu,fri",
            timezone_name="UTC",
            now_utc=now,
        )
        self.assertTrue(result.in_window)


class TestTimezoneResolution(unittest.TestCase):
    def test_europe_sarajevo_shifts_by_2h(self) -> None:
        # 2026-05-26 Tue 06:30 UTC = 08:30 Sarajevo (CEST, UTC+2).
        # Sarajevo window opens at 09:00 → out-of-window in Sarajevo,
        # next window = Sarajevo 09:00 = UTC 07:00.
        now = datetime(2026, 5, 26, 6, 30, tzinfo=timezone.utc)
        result = is_within_window(
            timezone_name="Europe/Sarajevo",
            now_utc=now,
            **DEFAULT_KW,
        )
        self.assertFalse(result.in_window)
        nxt = result.next_window_start_utc
        self.assertEqual(nxt.hour, 7)  # 9 Sarajevo - 2 = 7 UTC

    def test_invalid_tz_falls_back_to_env(self) -> None:
        with patch.dict(os.environ, {"SEND_WINDOW_DEFAULT_TZ": "UTC"}):
            now = datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc)
            result = is_within_window(
                timezone_name="Mars/Olympus_Mons",
                now_utc=now,
                **DEFAULT_KW,
            )
            self.assertTrue(result.in_window)  # UTC fallback puts us in window

    def test_no_tz_no_env_falls_back_to_utc(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            now = datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc)
            result = is_within_window(timezone_name=None, now_utc=now, **DEFAULT_KW)
            self.assertTrue(result.in_window)


class TestMalformedInputs(unittest.TestCase):
    def test_bad_send_window_falls_back_to_defaults(self) -> None:
        # Tue 12:00 with garbage window → falls back to 09-17 → in window.
        now = datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc)
        result = is_within_window(
            step_send_window_start="garbage",
            step_send_window_end="also-garbage",
            step_send_days="mon,tue,wed,thu,fri",
            timezone_name="UTC",
            now_utc=now,
        )
        self.assertTrue(result.in_window)

    def test_empty_send_days_falls_back_to_mon_fri(self) -> None:
        # Tue with empty send_days → defaults to Mon-Fri → Tue qualifies.
        now = datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc)
        result = is_within_window(
            step_send_window_start="09:00",
            step_send_window_end="17:00",
            step_send_days="",
            timezone_name="UTC",
            now_utc=now,
        )
        self.assertTrue(result.in_window)


if __name__ == "__main__":
    unittest.main()
