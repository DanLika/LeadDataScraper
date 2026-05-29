"""Static-scan guard: every ISO-8601 parse must route through
``src.utils.datetime_helper.parse_iso_timestamp``.

CI runs Python 3.12, but the prod container is Python 3.10 (Microsoft
Playwright ``v1.60.0-jammy``). Bare ``datetime.fromisoformat`` accepts
5-digit microseconds on 3.12 and rejects them on 3.10, so a regressed
call site would pass unit tests in CI and crash only at prod cold-start
— the exact failure mode that masked startup errors on 2026-05-28.

Mirror of the existing ``[:N]`` email-extraction static-scan guard.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = ("backend", "src")
ALLOWED_FILES = {
    # The helper itself uses ``isoparse``, not ``fromisoformat``. If a
    # future helper variant re-introduces the bare call, it must own the
    # allowlist entry explicitly.
}
PATTERN = re.compile(r"\bdatetime\.fromisoformat\s*\(")


def test_no_bare_fromisoformat_in_production_code() -> None:
    """Fails if any production module still parses ISO-8601 timestamps
    via the bare ``datetime.fromisoformat`` API."""
    offenders: list[str] = []
    for scan_dir in SCAN_DIRS:
        for py_file in (REPO_ROOT / scan_dir).rglob("*.py"):
            rel = py_file.relative_to(REPO_ROOT).as_posix()
            if rel in ALLOWED_FILES:
                continue
            try:
                source = py_file.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for lineno, line in enumerate(source.splitlines(), start=1):
                if PATTERN.search(line):
                    offenders.append(f"{rel}:{lineno}: {line.strip()}")

    assert not offenders, (
        "Bare datetime.fromisoformat call(s) detected. Route through "
        "src.utils.datetime_helper.parse_iso_timestamp instead — "
        "Py3.10 production rejects non-3/6-digit microseconds:\n  "
        + "\n  ".join(offenders)
    )
