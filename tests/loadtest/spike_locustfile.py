"""Spike (burst) load test for /leads.

Profile:

  T=0..10s    ramp linearly from 0 → 100 users
  T=10..70s   hold at 100 users
  T=70..80s   ramp linearly from 100 → 0 users
  T=80..90s   stay at 0 users (cool-down, lets p95 normalise)
  T=90s       exit

Why a spike (this shape, not the steady --users flag in `locustfile.py`):
  - Steady-state load proves the system can serve N rps once warm.
  - A spike proves the system survives the WARM-UP transient. The classic
    failure mode it catches is "rate limiter saturates → 429 storm →
    legitimate clients also fail" or "DB pool starves and never recovers".
  - "Does it recover?" is the second assertion: after the burst, p95
    should converge back toward baseline within 60s, not stay elevated.

Run:

  export LOAD_API_BASE=https://leaddata-backend.onrender.com
  export LOAD_API_KEY=<API_SECRET_KEY value>
  ./spike.sh                                # full run
  DURATION_FACTOR=0.5 ./spike.sh            # half-length smoke

Pass criteria (assert during analysis):
  - No 5xx responses anywhere in the run. 429s ARE allowed in the ramp
    if the per-IP rate-limit bucket saturates — though the synthetic-XFF
    trick in ReadUser should keep that rare.
  - p95 latency at T=85s (5s into cool-down) is within 2× of p95 at T=0..2s.
    Lock that comparison via the printed summary at shutdown.
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

from locust import LoadTestShape

# Reuse the existing user classes (ReadUser, StatsUser, OrchestratorUser)
# so the spike-shape file stays small. Tags filter to /leads only.
from tests.loadtest.locustfile import ReadUser  # noqa: F401 — imported for locust auto-discovery


class SpikeShape(LoadTestShape):
    """0 → 100 → 0 trapezoid as described in the module docstring."""

    _STAGES: List[Tuple[float, int, int]] = [
        # (end_time_seconds, target_users, spawn_rate)
        (10.0, 100, 100),   # ramp up: get to 100 quickly. spawn_rate matches users so locust doesn't dawdle.
        (70.0, 100, 100),   # plateau
        (80.0, 0, 100),     # ramp down. spawn_rate is users-killed-per-sec.
        (90.0, 0, 1),       # cool-down (idle window so p95 stats settle)
    ]

    def tick(self) -> Optional[Tuple[int, int]]:
        run_time = self.get_run_time()
        for end, users, rate in self._STAGES:
            if run_time < end:
                return users, rate
        return None
