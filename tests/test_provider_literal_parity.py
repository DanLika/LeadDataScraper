"""DB CHECK ↔ Python Literal parity.

The DB enforces the constraint at runtime; this test enforces it at
CI time. If a constraint is widened (new provider added) without
updating the matching `Literal`, the test goes red and the
inline-dict writers stop being mypy-protected.
"""

from typing import get_args

from src.types.providers import (
    LedgerProvider,
    SuppressionProvider,
    WebhookProvider,
)

WEBHOOK_DB = {"heyreach", "instantly", "resend"}
LEDGER_DB = {"heyreach", "instantly", "resend", "smtp"}
SUPPRESSION_DB = {"heyreach", "instantly", "manual", "resend", "smtp"}


def test_webhook_provider_parity() -> None:
    assert set(get_args(WebhookProvider)) == WEBHOOK_DB


def test_ledger_provider_parity() -> None:
    assert set(get_args(LedgerProvider)) == LEDGER_DB


def test_suppression_provider_parity() -> None:
    assert set(get_args(SuppressionProvider)) == SUPPRESSION_DB
