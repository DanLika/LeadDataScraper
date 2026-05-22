"""Backward-compatibility shim. The canonical hierarchy now lives in
`src/errors.py` — see the module docstring there for the rules and
the full hierarchy diagram.

Imports from `src.services.exceptions` continue to resolve so that
in-flight PRs and downstream callers don't have to update in lockstep.

New code MUST import from `src.errors` directly. This shim can be
removed once every reference to `src.services.exceptions` has been
migrated.
"""
from src.errors import (
    CampaignNotFoundError,
    CampaignTableMissingError,
    DomainError,
    NoCampaignMessagesError,
    NoMatchingLeadsError,
    NotFoundError,
)

__all__ = [
    "CampaignNotFoundError",
    "CampaignTableMissingError",
    "DomainError",
    "NoCampaignMessagesError",
    "NoMatchingLeadsError",
    "NotFoundError",
]
