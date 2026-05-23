"""Structured JSON logging for the LeadDataScraper backend.

Output shape — one JSON object per line — fixed canonical schema plus
arbitrary domain fields passed via ``extra={...}``::

    {
      "timestamp":   "2026-05-22T14:30:15.123Z",
      "level":       "INFO" | "WARNING" | "ERROR" | "DEBUG" | "CRITICAL",
      "logger":      "backend.main",
      "message":     "Lead Data Scraper Backend Starting...",
      "request_id":  "ab12cd34..." | null,
      "user_id":     "operator@example.com" | null,
      "route":       "/leads" | null,
      "duration_ms": 142.7 | null,
      "<domain>":    ...   # any extra={} keys land at the top level
    }

Render's logs UI is grep-only, but JSON lines stay greppable
(``grep '"level":"ERROR"' app.log | jq``) while remaining parseable by
Sentry / Logtail / Loki / any JSON-aware shipper. This is the only
transport-agnostic format the pipeline needs.

**CRLF-scrubbing security guarantee preserved.** ``_CRLFScrubFilter``
runs at the FILTER stage (before the formatter), so attacker-
controllable args (lead names, websites, pain-points scraped from CSV
uploads + Google Maps) can't smuggle a fake log line. Locked in by
``tests/test_crlf_injection.py::TestLoggingCRLFScrub``. The filter
now also scrubs domain values passed via ``extra={...}`` — those land
in ``record.__dict__`` as arbitrary keys, and the JsonFormatter writes
them straight into the envelope. Without that pass, an attacker-
controlled extra value would still ride into the JSON output.

**Per-request context** comes from three ContextVars set by
``backend.main._request_context_middleware`` on every HTTP request:
``request_id_var``, ``user_id_var``, ``route_var``. Use
``bind_request_context()`` / ``clear_request_context()`` to drive
them from background tasks (the orchestrator binds job_id-derived
request_ids so background work logs correlate to its parent job).
"""

import json
import logging
import logging.handlers
import os
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Tuple


# ContextVars propagate per asyncio task — middleware sets these per
# request; every logger call within the task automatically inherits
# them via the formatter.
request_id_var: ContextVar[Optional[str]] = ContextVar("request_id", default=None)
user_id_var: ContextVar[Optional[str]] = ContextVar("user_id", default=None)
route_var: ContextVar[Optional[str]] = ContextVar("route", default=None)


# Standard LogRecord attributes — anything else in record.__dict__ is a
# domain field passed via `extra={...}` and goes into the envelope.
_RESERVED = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "module", "msecs",
    "message", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "thread", "threadName", "taskName",
})


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per record. Stable canonical field names so
    downstream log shippers (Sentry / Logtail / Loki) can rely on the
    schema; domain fields passed via
    ``logger.info(msg, extra={"job_id": "...", "lead_unique_key": "..."})``
    get merged at the top level.

    Values that aren't JSON-serializable fall through to ``repr()``
    rather than crashing the log emit — better an ugly line than a
    silent drop.
    """

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc)
        envelope: dict[str, Any] = {
            "timestamp": ts.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
            "user_id": user_id_var.get(),
            "route": route_var.get(),
        }
        # Merge in any domain fields passed via extra={...}.
        for key, value in record.__dict__.items():
            if key in _RESERVED or key in envelope:
                continue
            try:
                json.dumps(value)
            except (TypeError, ValueError):
                value = repr(value)
            envelope[key] = value
        if record.exc_info:
            envelope["exception"] = self.formatException(record.exc_info)
        # default=str catches stragglers (datetime, UUID, Decimal);
        # ensure_ascii=False keeps non-ASCII names readable in Render UI.
        return json.dumps(envelope, default=str, ensure_ascii=False)


class _CRLFScrubFilter(logging.Filter):
    """Replace raw CR / LF / VT / FF in ``record.msg``, every entry of
    ``record.args``, and every ``extra={...}`` value with their printable
    escape forms (``\\r``, ``\\n``, etc).

    Lead names, websites, and pain-points pass through
    ``logger.error("... %s ...", lead_name, ...)`` — if the lead's
    name contains ``"\\r\\nINFO  forged log line ..."`` the file
    handler would emit two log lines, the second under attacker-chosen
    levels + contents. The filter mutates ``record.msg``,
    ``record.args``, and the extra-dict so the final formatted line
    stays on one row regardless of formatter (text OR JSON).
    """

    _CRLF_MAP = str.maketrans({"\r": "\\r", "\n": "\\n", "\x0b": "\\x0b",
                                "\x0c": "\\x0c"})

    @classmethod
    def _scrub(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.translate(cls._CRLF_MAP)
        if isinstance(value, tuple):
            return tuple(cls._scrub(v) for v in value)
        if isinstance(value, list):
            return [cls._scrub(v) for v in value]
        if isinstance(value, dict):
            return {k: cls._scrub(v) for k, v in value.items()}
        return value

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._scrub(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._scrub(v) for k, v in record.args.items()}
            else:
                record.args = tuple(self._scrub(a) for a in record.args)
        # Scrub anything passed via extra={...} that landed in
        # record.__dict__ as a non-reserved attribute. The JsonFormatter
        # writes those straight into the envelope, so without scrubbing
        # them the CRLF guarantee breaks at the structured-log layer.
        for key in list(record.__dict__.keys()):
            if key in _RESERVED:
                continue
            record.__dict__[key] = self._scrub(record.__dict__[key])
        return True


def setup_logging() -> None:
    """Configure the root logger with the JSON formatter + CRLF scrub
    filter. Idempotent: removes existing handlers so a re-init from
    tests / uvicorn-reload doesn't double-emit.

    Console (stdout) is always added — Render captures stdout, the
    Dockerfile keeps ``PYTHONUNBUFFERED=1``, and the local dev workflow
    already pipes uvicorn stdout to the terminal.

    Optional ``RotatingFileHandler`` when ``LOG_FILE`` env points at a
    path (handy for ``tail -f`` workflows in local dev). Skipped in
    production unless the operator explicitly sets it.

    Log level via ``LOG_LEVEL`` env (default ``INFO``).
    """
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)

    formatter = JsonFormatter()
    scrub = _CRLFScrubFilter()

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(scrub)
    root.addHandler(console)

    log_file = os.getenv("LOG_FILE", "").strip()
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=10 * 1024 * 1024, backupCount=5
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(scrub)
        root.addHandler(file_handler)

    level = os.getenv("LOG_LEVEL", "INFO").upper()
    root.setLevel(getattr(logging, level, logging.INFO))

    # Tame chatty third-party loggers.
    for name in ("httpx", "httpcore", "urllib3", "playwright", "aiohttp"):
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Convenience accessor — matches the legacy API so existing
    ``from src.utils.logging_config import get_logger`` callers keep
    working."""
    return logging.getLogger(name)


# ---------------------------------------------------------------------------
# Per-request context binding helpers.
#
# Used by `backend.main._request_context_middleware` on every HTTP
# request. Also intended for background tasks (TaskOrchestrator,
# post-deploy smoke probes) that want a synthetic request_id derived
# from job_id, so background log lines correlate to their parent job.
# ---------------------------------------------------------------------------

ContextTokens = Tuple[Any, Any, Any]


def bind_request_context(
    request_id: str,
    user_id: Optional[str] = None,
    route: Optional[str] = None,
) -> ContextTokens:
    """Set request_id / user_id / route ContextVars, return reset
    tokens. **Caller MUST** call ``clear_request_context(tokens)`` in
    a ``finally`` to avoid leaking state to other coroutines that share
    the task — leaking would mis-attribute log lines to the wrong
    request."""
    tok_r = request_id_var.set(request_id)
    tok_u = user_id_var.set(user_id)
    tok_p = route_var.set(route)
    return tok_r, tok_u, tok_p


def clear_request_context(tokens: ContextTokens) -> None:
    """Reverse the bindings from ``bind_request_context``. Pass the
    exact tuple returned by ``bind_request_context``."""
    request_id_var.reset(tokens[0])
    user_id_var.reset(tokens[1])
    route_var.reset(tokens[2])
