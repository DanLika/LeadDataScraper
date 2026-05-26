"""Unit tests for src/repositories/webhook_event_repo.py.

No live DB — supabase-py is mocked. Covers:
- insert_event happy path: row shape + InsertResult(inserted=True)
- insert_event duplicate: 23505 via .code attribute → InsertResult(duplicate=True)
- insert_event duplicate: 23505 via stringified body → same outcome
- insert_event other exception: propagates unchanged
- _is_unique_violation: code attr, body substring, irrelevant exceptions
"""
from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest.mock import MagicMock

from src.repositories.webhook_event_repo import (
    InsertResult,
    WebhookEventRepository,
    _is_unique_violation,
)


def _build_fake_client() -> tuple[Any, MagicMock]:
    """Chainable supabase-py mock recording the call sequence."""
    table_mock = MagicMock(name="table")
    table_mock.insert.return_value = table_mock

    class _Result:
        def __init__(self) -> None:
            self.data: list[dict[str, Any]] = []

    table_mock.execute.return_value = _Result()
    client = MagicMock(name="client")
    client.table.return_value = table_mock
    return client, table_mock


class TestInsertEvent(unittest.TestCase):
    def test_happy_path_row_shape(self) -> None:
        client, table = _build_fake_client()
        repo = WebhookEventRepository(client)
        result = asyncio.run(repo.insert_event(
            provider="instantly",
            event_id="evt_123",
            event_type="email_sent",
            payload={"any": "json"},
        ))
        self.assertTrue(result.inserted)
        self.assertFalse(result.duplicate)
        client.table.assert_called_once_with("webhook_events")
        table.insert.assert_called_once_with({
            "provider": "instantly",
            "event_id": "evt_123",
            "event_type": "email_sent",
            "payload": {"any": "json"},
        })

    def test_duplicate_via_code_attribute(self) -> None:
        client, table = _build_fake_client()

        class APIErrorWithCode(Exception):
            code = "23505"

        table.execute.side_effect = APIErrorWithCode("duplicate key")
        repo = WebhookEventRepository(client)
        result = asyncio.run(repo.insert_event(
            provider="instantly",
            event_id="evt_123",
            event_type="email_sent",
            payload={"k": "v"},
        ))
        self.assertFalse(result.inserted)
        self.assertTrue(result.duplicate)

    def test_duplicate_via_body_substring(self) -> None:
        client, table = _build_fake_client()
        table.execute.side_effect = RuntimeError(
            'PostgREST 409: {"code":"23505","message":"duplicate key value violates"}'
        )
        repo = WebhookEventRepository(client)
        result = asyncio.run(repo.insert_event(
            provider="instantly",
            event_id="evt_123",
            event_type="email_sent",
            payload={},
        ))
        self.assertFalse(result.inserted)
        self.assertTrue(result.duplicate)

    def test_other_exception_propagates(self) -> None:
        client, table = _build_fake_client()
        table.execute.side_effect = RuntimeError("connection reset")
        repo = WebhookEventRepository(client)
        with self.assertRaises(RuntimeError) as cm:
            asyncio.run(repo.insert_event(
                provider="instantly",
                event_id="evt_123",
                event_type="email_sent",
                payload={},
            ))
        self.assertIn("connection reset", str(cm.exception))


class TestIsUniqueViolation(unittest.TestCase):
    def test_code_attribute(self) -> None:
        class APIError(Exception):
            code = "23505"
        self.assertTrue(_is_unique_violation(APIError("anything")))

    def test_code_attribute_other(self) -> None:
        class APIError(Exception):
            code = "42P01"  # undefined_table
        self.assertFalse(_is_unique_violation(APIError("anything")))

    def test_body_substring_23505(self) -> None:
        self.assertTrue(_is_unique_violation(RuntimeError('"code":"23505"')))

    def test_body_substring_duplicate_key(self) -> None:
        self.assertTrue(_is_unique_violation(
            RuntimeError("duplicate key value violates unique constraint")
        ))

    def test_irrelevant_exception(self) -> None:
        self.assertFalse(_is_unique_violation(RuntimeError("connection reset")))


if __name__ == "__main__":
    unittest.main()
