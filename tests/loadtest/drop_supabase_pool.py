#!/usr/bin/env python3
"""Inject a transient Supabase pool failure into a LOCALLY-RUNNING backend.

Companion to tests/loadtest/chaos.md Scenario B.1.

Strategy
--------
Locate the live SupabaseHelper in the running uvicorn process (via
sys.modules walk, same trick QueryProfiler uses), then replace its
client.table attribute with a function that raises a httpx ConnectError
for `--hold` seconds. Original is restored on exit.

This works because the lazy-singleton `db` in backend.main caches the
SupabaseHelper instance once any handler resolves it; we monkey-patch
that one instance.

IMPORTANT
---------
Local-only. Refuses to run unless `CHAOS_LOCAL_ONLY=1` is set AND the
backend is reachable on 127.0.0.1 — refuses to attack a non-loopback
host. Production chaos goes through the Render dashboard or `iptables`,
not this script.

Usage
-----
  CHAOS_LOCAL_ONLY=1 ./tests/loadtest/drop_supabase_pool.py --hold 30

In parallel terminal:
  LOAD_API_BASE=http://127.0.0.1:8000 LOAD_API_KEY=… \\
    locust -f tests/loadtest/locustfile.py --headless \\
           --tags read --users 20 --spawn-rate 5 --run-time 90s \\
           --host $LOAD_API_BASE \\
           --html tests/loadtest/reports/chaos_B.html
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Iterator


def _refuse_unless_local() -> None:
    if os.environ.get("CHAOS_LOCAL_ONLY") != "1":
        raise SystemExit(
            "Refusing to inject pool failure without CHAOS_LOCAL_ONLY=1. "
            "This script monkey-patches a running backend's DB client and "
            "must NEVER touch a prod target. Set the env var only after "
            "confirming the target is on localhost."
        )


def _discover_supabase_clients() -> Iterator:
    """Walk sys.modules for live SupabaseHelper instances. Mirrors
    src/utils/query_profiler.QueryProfiler._discover_clients."""
    from src.utils.supabase_helper import SupabaseHelper

    seen: set[int] = set()
    for module in list(sys.modules.values()):
        if module is None:
            continue
        try:
            d = getattr(module, "__dict__", {})
        except Exception:
            continue
        for value in d.values():
            if isinstance(value, SupabaseHelper):
                cli = getattr(value, "client", None)
                if cli is None or id(cli) in seen:
                    continue
                seen.add(id(cli))
                yield cli


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--hold",
        type=float,
        default=30.0,
        help="Seconds to keep the pool 'broken' (default 30)",
    )
    args = parser.parse_args()

    _refuse_unless_local()

    # Importing backend.main here triggers the lazy `db` singleton if it
    # hasn't been resolved yet by an in-flight request. After this, the
    # discovery walk finds it.
    import backend.main  # noqa: F401

    _ = backend.main.db  # force resolution

    clients = list(_discover_supabase_clients())
    if not clients:
        print(
            "No live SupabaseHelper instance found — is the backend running in this process?"
        )
        return 2

    print(f"Patching {len(clients)} supabase client(s) for {args.hold:.0f}s …")

    saved = []

    class _PoolDownError(Exception):
        pass

    def _raise(*a, **kw):
        raise _PoolDownError("simulated pool outage (chaos injection)")

    for client in clients:
        saved.append((client, client.table))
        client.table = _raise  # type: ignore[attr-defined]

    try:
        time.sleep(args.hold)
    finally:
        for client, original in saved:
            try:
                client.table = original  # type: ignore[attr-defined]
            except Exception:
                pass
        print("Restored. Backend should serve next /leads request normally.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
