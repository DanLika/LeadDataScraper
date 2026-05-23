"""Shared helpers for Phase 16-T2 backend matrix.

Loads creds from repo root .env. Provides an httpx.AsyncClient factory
with X-API-Key default header, a results sink that appends JSON-line
records to results.jsonl, and small assertion helpers.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = ROOT / ".env"
REPORT_DIR = Path(__file__).resolve().parent
RESULTS_FILE = REPORT_DIR / "results.jsonl"

BACKEND = os.getenv("BACKEND_URL_T2", "http://localhost:8000")

_env_cache: dict[str, str] | None = None


def env() -> dict[str, str]:
    global _env_cache
    if _env_cache is not None:
        return _env_cache
    out: dict[str, str] = {}
    for line in ENV_FILE.read_text().splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    _env_cache = out
    return out


def api_key() -> str:
    return env()["API_SECRET_KEY"]


def admin_token() -> str:
    return env()["ADMIN_TOKEN"]


def client(**extra: Any):
    import httpx
    headers = {"X-API-Key": api_key(), "User-Agent": "phase16-t2/1.0"}
    headers.update(extra.pop("headers", {}) or {})
    timeout = extra.pop("timeout", httpx.Timeout(30.0, connect=5.0))
    return httpx.AsyncClient(
        base_url=BACKEND,
        timeout=timeout,
        headers=headers,
        **extra,
    )


def record(test_id: str, name: str, **fields: Any) -> None:
    """Append one result row as JSON-line."""
    row = {
        "ts": time.time(),
        "test_id": test_id,
        "name": name,
        **fields,
    }
    with RESULTS_FILE.open("a") as f:
        f.write(json.dumps(row, default=str) + "\n")


def reset_results() -> None:
    if RESULTS_FILE.exists():
        RESULTS_FILE.unlink()


def synth_id() -> str:
    return uuid.uuid4().hex[:12]
