# Py3.10 `datetime.fromisoformat` fractional-seconds intolerance

**Status**: RESOLVED. PR #398 introduced `parse_iso_timestamp` helper +
`tests/quality/test_no_bare_fromisoformat.py` static-scan guard.

## Symptom

Prod cold-start log (2026-05-28 14:45 UTC):

```
ValueError: Invalid isoformat string: '2026-05-28T14:45:34.51428+00:00'
  at backend.main:<lifespan>
  ↳ src.core.task_orchestrator.recover_interrupted_jobs
  ↳ datetime.fromisoformat(row["started_at"])
```

Cascades into `Startup DB checks skipped` warning on every backend restart.
Lifespan handler bails on first `ValueError`, masking other startup failures.
CI Python 3.12 runner never reproduces — bug is invisible until prod.

## Root cause

`datetime.fromisoformat` on Python 3.10 only accepts microsecond components
of length **0, 3, or 6**. Supabase / PostgREST emits `timestamptz` strings
with whatever subsecond precision the row's `CURRENT_TIMESTAMP` capture
produced — empirically anywhere from 3 to 7 digits.

Python 3.11+ relaxed the rule so the bug is INVISIBLE on the CI Python
3.12 runner. Prod container is Python 3.10 (Microsoft Playwright
`mcr.microsoft.com/playwright/python:v1.60.0-jammy` ships 3.10).

Same CI-vs-prod Python version gap shape as
[pep562-cron-path-trap.md](./pep562-cron-path-trap.md) and the earlier
2026-05-24 Py3.10 lockfile drift.

## Fix recipe

```python
# OLD (breaks on prod):
from datetime import datetime
ts = datetime.fromisoformat(row["started_at"])

# NEW (PR #398):
from src.utils.datetime_helper import parse_iso_timestamp
ts = parse_iso_timestamp(row["started_at"])
```

Helper signature: `parse_iso_timestamp(value: str) -> datetime`. Accepts both
`Z` and `+00:00` UTC indicators so the legacy `.replace("Z", "+00:00")` shim
is dead code at every call site. Delegates to `dateutil.parser.isoparse`
which handles 1–9 digit fractional seconds on every supported Python release.

`python-dateutil` is already a direct dependency (`requirements.in`) — no
new package add.

## Audit existing call sites

```bash
grep -rn 'datetime\.fromisoformat\|fromisoformat' backend/ src/
# Expected after PR #398: ZERO production hits.
```

## Recurrence guard

`tests/quality/test_no_bare_fromisoformat.py` (PR #398) — static AST scan
fails CI if a new call site lands in `backend/` or `src/` without going
through `parse_iso_timestamp`. Test inputs that catch this:
`2026-05-28T14:45:34.51428+00:00` is the canonical 5-digit regression input.

## Why CI didn't catch it earlier

CI uses Python 3.12 (default Ubuntu image on GitHub Actions runners) for
fast pytest runs. Prod uses Python 3.10 (Playwright base image). Per
[cluster-1-lockfile-drift](./README.md#py3-10-vs-py3-12-drift),
SHOULD eventually wire a parallel Py3.10 pytest job to catch CI-vs-prod
stdlib gaps proactively. Not yet wired — pending operator decision on CI cost
budget.

## Related

- Memory: `py310_isoformat_5digit_microseconds_2026-05-29.md`,
  `feedback_pep562_cron_path.md`, `session_2026-05-24_final-sweep.md`
- PR: #398 (admin-merged 2026-05-29 as `02e99cc` bundle)
- Code: `src/utils/datetime_helper.py`,
  `tests/quality/test_no_bare_fromisoformat.py`
- Related runbook: [pep562-cron-path-trap](./pep562-cron-path-trap.md)
