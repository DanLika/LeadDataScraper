"""Shared provider-name `Literal` types.

Each `Literal` mirrors a DB CHECK constraint allowlist on a Phase 14/15
table. Producers that write the corresponding column must annotate
their value with the matching `Literal` so mypy catches typos before
the DB CHECK does at runtime.

The allowlists are intentionally asymmetric:

* `WebhookProvider` excludes `smtp` — SMTP can't deliver webhooks.
* `LedgerProvider` excludes `manual` — manual suppressions never spawn
  a ledger row.
* `SuppressionProvider` is the widest superset — manual ops + every
  delivery channel can land a row in `suppressions`.

When adding a new provider, edit both the DB CHECK and the matching
`Literal` here in the same PR. `tests/test_provider_literal_parity.py`
locks the contract.
"""

from __future__ import annotations

from typing import Literal

# Mirrors webhook_events_provider_allowed CHECK in supabase_schema.sql.
WebhookProvider = Literal["heyreach", "instantly", "resend"]

# Mirrors email_send_ledger_provider_allowed CHECK in supabase_schema.sql.
LedgerProvider = Literal["heyreach", "instantly", "resend", "smtp"]

# Mirrors suppressions_provider_allowed CHECK in supabase_schema.sql.
SuppressionProvider = Literal[
    "heyreach",
    "instantly",
    "manual",
    "resend",
    "smtp",
]

__all__ = ["WebhookProvider", "LedgerProvider", "SuppressionProvider"]
