#!/usr/bin/env python3
"""CLI entry point for the webhook event sweeper.

Invoked by Render Cron every 2 minutes (see ``render.yaml``):

    python scripts/webhook_sweeper.py

Or with explicit overrides for local testing:

    python scripts/webhook_sweeper.py --batch-size 100 --grace-seconds 30

Exits:
  * 0 — tick ran cleanly (incl. "no stranded rows" no-op)
  * 1 — operator misconfig (db client unavailable)
  * 2 — runtime cap hit with rows still unprocessed

Stdout is a single JSON line so Render's log aggregator / grep
pipelines can compute per-tick tallies without parsing prose.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="LDS webhook event sweeper (Path A — Phase 14.X recovery)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Rows to claim per tick (default: env WEBHOOK_SWEEP_BATCH_SIZE or 50)",
    )
    parser.add_argument(
        "--grace-seconds",
        type=int,
        default=None,
        help="Skip rows newer than now - grace (default: env WEBHOOK_SWEEP_GRACE_SEC or 60)",
    )
    parser.add_argument(
        "--max-runtime-sec",
        type=int,
        default=None,
        help="Wall-clock cap (default: env WEBHOOK_SWEEP_MAX_RUNTIME_SEC or 50)",
    )
    return parser.parse_args(argv)


async def _amain(args: argparse.Namespace) -> int:
    from src.workers.webhook_sweeper import sweep_once

    result = await sweep_once(
        batch_size=args.batch_size,
        grace_seconds=args.grace_seconds,
        max_runtime_sec=args.max_runtime_sec,
    )
    print(json.dumps(result.as_dict(), default=str), flush=True)

    if "db_client_unavailable" in result.errors:
        return 1
    if "runtime_cap" in result.errors:
        return 2
    return 0


def main() -> int:
    args = _parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
