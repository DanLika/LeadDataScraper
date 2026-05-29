# PEP 562 trap — lazy singletons missing in cron / standalone entrypoints

**Status**: RESOLVED for `webhook_sweeper`. PR #394 primed `db` at handler
entry. Pattern documented; every new cron / standalone script that imports
from `backend.main` must apply it.

## Symptom

Sweeper cron `webhook_sweeper` ticks every 2 min on Render. Render dashboard
shows green ticks. Internally, every tick:

- Picks up 2 unprocessed `webhook_events` rows.
- Calls `backend.main._process_instantly_event(payload)` directly.
- Bare-name reference `db.client.table("campaign_messages")` inside the
  handler raises `NameError: name 'db' is not defined`.
- Outer `try/except Exception` swallows the NameError, logs nothing useful.
- `processed_at` stays `NULL` → next tick re-fetches the same 2 rows →
  infinite replay every 2 min for 6+ hours.

Diagnosis only surfaced via `webhook_events` table audit showing
`processed_at IS NULL` for 100% of rows older than the sweeper deploy.

## Root cause

`backend/main.py` uses module-level `__getattr__` (PEP 562) to lazy-load heavy
singletons (`db`, `router`, `auditor`, `orchestrator`):

```python
def __getattr__(name):
    if name == "db":
        return _init_db()
    ...
```

PEP 562 fires ONLY on `module.attr` access from OUTSIDE the module. It does
NOT fire on bare-name `LOAD_GLOBAL` inside same-module functions or lambdas.

Inside FastAPI HTTP boot, the lifespan handler runs a priming loop that walks
`sys.modules[__name__]` for each lazy name — this populates `globals()` once,
then all subsequent bare-name `db.client.table(...)` references inside any
nested function resolve normally.

Cron entrypoints (`scripts/<x>.py` that `import backend.main` and call a
handler directly) bypass the lifespan. `globals()["db"]` stays absent.
First bare-name `db` reference raises `NameError`.

## Fix recipe

Single-line prime at handler entry — one attribute access populates `globals()`
for every subsequent reference in the same handler chain:

```python
import sys as _sys

async def _process_instantly_event(payload: dict) -> None:
    _self_mod = _sys.modules[__name__]
    _self_mod.db  # noqa — side-effect: primes globals()["db"] via PEP-562 __getattr__
    # ... rest of handler can use bare-name `db.client.table(...)` normally
```

**MUST apply to**: any handler in `backend/main.py` that may be invoked from
a non-lifespan path — Render cron, standalone script, `python -m`, test fixture
that imports `backend.main` directly without invoking the FastAPI app
lifecycle.

## Regression test pattern

Subprocess isolation is REQUIRED. In-process pytest masks the bug because
any earlier test that touched `backend.main.db` (most do) populates globals
for the rest of the session.

```python
# tests/test_webhook_cron_pep562.py (reference)
import subprocess, sys, textwrap

def test_handler_primes_db_under_fresh_import():
    script = textwrap.dedent("""
        import backend.main
        # Do NOT run lifespan. Call handler directly.
        import asyncio
        from backend.main import _process_instantly_event
        asyncio.run(_process_instantly_event({"event_type": "ping"}))
    """)
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=10
    )
    assert "NameError" not in result.stderr
    assert result.returncode == 0
```

## Audit checklist on new lazy singleton

When adding any new lazy global to `backend/main.py`:

1. Add the name to the priming loop in the lifespan handler
   (per CLAUDE.md "Cold-start lazy imports > PEP 562 trap" section).
2. Audit every `backend/main.py` handler that's exported as a cron
   entrypoint or callable from `scripts/`. Add the one-line prime to each
   handler that references the new global.
3. Add a subprocess-isolated regression test like
   `tests/test_webhook_cron_pep562.py`.

## Recurrence guard

- **CLAUDE.md invariant** — "Cold-start lazy imports" section warns about
  this exact failure mode. Future lazy singletons MUST land in the lifespan
  priming loop AND cron-callable handlers MUST land the per-handler prime.
- **`tests/test_webhook_cron_pep562.py`** (subprocess isolation) — pins the
  sweeper handler. Add a parallel test for every new cron handler.

## Related

- Memory: `feedback_pep562_cron_path.md`, `session_2026-05-28_final_arc.md`
- PR: #394 (`23482ba` admin-merged 2026-05-28 in final-arc bundle)
- Code: `backend/main.py` lifespan priming loop, `_process_instantly_event`,
  `src/workers/dispatch_tick.py`, `tests/test_webhook_cron_pep562.py`
- Related runbook: [webhook-burst-stranded-rows](./webhook-burst-stranded-rows.md)
