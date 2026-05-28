"""Regression: PEP-562 cron-path NameError on backend.main.db.

Prod logs 2026-05-28 (Render webhook_sweeper cron, every 2 min):

    NameError: name 'db' is not defined
      File "/app/backend/main.py", line 1554, in <lambda>
        db.client.table("webhook_events")

The webhook_sweeper.py CLI entrypoint imports `backend.main` and calls
`_process_instantly_event` directly — no FastAPI lifespan runs, so the
lazy `db` / `router` / `auditor` / `orchestrator` singletons in
`backend.main.globals()` are still unset. Bare-name `db` references
inside nested functions/lambdas use LOAD_GLOBAL, which raises NameError
without consulting module `__getattr__` (PEP-562 only fires on
`module.attr` access, not bare-name LOAD_GLOBAL — see CLAUDE.md
"Cold-start lazy imports" + the `Lifespan attribute-accesses each name
via sys.modules[__name__]` paragraph).

The fix is a single attribute-access prime at the top of
`_process_instantly_event` that triggers `__getattr__` to populate
`globals()["db"]`. After that, every nested bare-name `db.client`
reference (incl. the webhook_events checkpoint UPDATE at line ~1610
and the entire `_instantly_handle_{sent,bounced,unsubscribed,replied}`
chain) resolves normally.

This test isolates the cron-path entry by spawning a subprocess: in a
fresh Python interpreter, `backend.main` is imported, `db` is popped
from globals to simulate the post-import-pre-lifespan state, and
`_process_instantly_event` is invoked. Before the fix the call raises
NameError; after the fix it returns cleanly (or fails on stubbed DB —
both fine; we only assert the absence of NameError).

Subprocess isolation is required because pytest's shared process state
gets `db` primed by any earlier test that touched it, masking the bug
in-process.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap


def test_process_instantly_event_does_not_raise_nameerror_on_db() -> None:
    code = textwrap.dedent(
        """
        import asyncio, sys, traceback
        import backend.main as m

        # Simulate cron-worker state: lifespan never ran, so the lazy
        # singletons are not in globals(). Pop them defensively in case
        # an import side-effect primed any of them.
        for _name in ("db", "router", "auditor", "orchestrator"):
            m.__dict__.pop(_name, None)

        # Stub SupabaseHelper to avoid hitting a real DB at __getattr__
        # prime time. The handler's downstream branches all gate on
        # `db.client` truthiness; a stub with client=None makes them
        # bail fast WITHOUT raising NameError, which is what we're
        # actually asserting.
        import src.utils.supabase_helper as _sh

        class _Stub:
            client = None

            def check_schema(self):
                return []

        _sh.SupabaseHelper = lambda: _Stub()

        try:
            asyncio.run(
                m._process_instantly_event(
                    "evt-pep562-regression",
                    {"event_type": "unknown-noop-type"},
                )
            )
        except NameError as exc:
            print(f"NAMEERROR: {exc}", file=sys.stderr)
            traceback.print_exc()
            sys.exit(2)
        except Exception:
            # Other exceptions (stubbed DB attribute errors, network
            # absence, etc.) are acceptable — we are only locking the
            # contract that the PEP-562 prime at function entry stops
            # the bare-name `db` NameError chain.
            pass

        # After the prime, the lazy singleton MUST be in globals.
        assert "db" in m.__dict__, "db not primed in globals() after handler entry"
        print("OK")
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, (
        "subprocess returned non-zero — webhook cron PEP-562 regression "
        "is live. stdout=" + result.stdout + " stderr=" + result.stderr
    )
    assert "OK" in result.stdout, result.stdout + result.stderr
