#!/usr/bin/env python3
"""CLI entry point for the dispatch tick worker.

Invoked by Render Cron (see ``docs/runbooks/dispatch-cron.md``):

    python scripts/dispatch_tick.py

Or with explicit overrides for local testing / debug:

    python scripts/dispatch_tick.py --batch-size 50 --max-runtime-sec 30

Exits:
  * 0 — tick ran cleanly (incl. "no due messages" no-op)
  * 1 — operator misconfig (no DB client / no dispatcher)
  * 2 — runtime exceeded with non-empty error list

Stdout is a single JSON line so Render's log aggregator / any grep
pipeline can compute per-stage tallies without parsing prose.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

# Self-locate the repo root so the script works under Render Cron
# (which sets cwd to /opt/render/project/src) AND under local
# `python scripts/dispatch_tick.py` from the repo root. Walks up
# from the script path to find the directory containing supabase_schema.sql
# as a stable repo-root marker.
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="LDS dispatch tick worker (Phase 15.2)",
    )
    parser.add_argument(
        "--batch-size", type=int, default=None,
        help="How many due messages to claim per tick (default: env DISPATCH_TICK_BATCH_SIZE or 100)",
    )
    parser.add_argument(
        "--claim-timeout-min", type=int, default=None,
        help="Minutes after which a stuck 'dispatching' row resets to 'pending' (default: env DISPATCH_CLAIM_TIMEOUT_MIN or 15)",
    )
    parser.add_argument(
        "--max-runtime-sec", type=int, default=None,
        help="Bail-out wall-clock cap (default: env DISPATCH_TICK_MAX_RUNTIME_SEC or 50)",
    )
    return parser.parse_args(argv)


async def _amain(args: argparse.Namespace) -> int:
    # Defer the import so a syntax error / import failure in the worker
    # surfaces with the right traceback rather than from argparse setup.
    from src.workers.dispatch_tick import run_tick

    result = await run_tick(
        batch_size=args.batch_size,
        claim_timeout_min=args.claim_timeout_min,
        max_runtime_sec=args.max_runtime_sec,
    )
    print(json.dumps(result.as_dict(), default=str), flush=True)
    if any(e.startswith("db_client_unavailable") or e.startswith("dispatcher_unavailable")
           for e in result.errors):
        return 1
    if result.errors and any(e.startswith("runtime_cap") for e in result.errors):
        return 2
    return 0


def main() -> int:
    args = _parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
