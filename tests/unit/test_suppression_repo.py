"""Unit tests for src/repositories/suppression_repo.py.

No live DB — every supabase-py call is mocked. Covers:
- is_suppressed: hit, miss, channel widening for 'all' rows
- filter_suppressed: 10-in / 3-suppressed batch, ONE round trip, order preserved
- add: returns row id; duplicate (23505) returns None instead of raising
- bulk_import: upsert(...ignore_duplicates=True) shape; inserted vs skipped count
"""
from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest.mock import MagicMock

from src.repositories.suppression_repo import (
    BulkImportResult,
    SuppressionAdd,
    SuppressionRepository,
    _is_unique_violation,
)


def _build_fake_client(rows_to_return: list[dict[str, Any]] | None = None) -> tuple[Any, MagicMock]:
    """Build a chainable supabase-py mock that records the call sequence.

    Returns (client, table_mock). The terminal ``.execute()`` returns an
    object whose ``.data`` is ``rows_to_return`` (or empty list if None).
    """
    table_mock = MagicMock(name="table")
    # Make every chained call return the same mock so we can record any
    # depth of chained API calls. .execute() returns a Result object.
    table_mock.select.return_value = table_mock
    table_mock.eq.return_value = table_mock
    table_mock.in_.return_value = table_mock
    table_mock.limit.return_value = table_mock
    table_mock.insert.return_value = table_mock
    table_mock.upsert.return_value = table_mock

    class _Result:
        def __init__(self, data: list[dict[str, Any]]) -> None:
            self.data = data

    table_mock.execute.return_value = _Result(rows_to_return or [])
    client = MagicMock(name="client")
    client.table.return_value = table_mock
    return client, table_mock


class TestIsSuppressed(unittest.TestCase):
    def test_returns_true_when_row_exists(self) -> None:
        client, table = _build_fake_client([{"id": 1}])
        repo = SuppressionRepository(client)
        result = asyncio.run(repo.is_suppressed("foo@bar.com", channel="email"))
        self.assertTrue(result)
        # One single chained call, terminal .limit(1).execute().
        client.table.assert_called_once_with("suppressions")
        table.eq.assert_called_with("identifier_value", "foo@bar.com")
        # channel predicate widens 'email' → ['email', 'all']
        table.in_.assert_called_with("channel", ["email", "all"])
        table.limit.assert_called_with(1)

    def test_returns_false_when_no_row(self) -> None:
        client, _ = _build_fake_client([])
        repo = SuppressionRepository(client)
        self.assertFalse(asyncio.run(repo.is_suppressed("nobody@x.com")))

    def test_returns_false_on_empty_identifier(self) -> None:
        client, _ = _build_fake_client([{"id": 99}])  # row would match if called
        repo = SuppressionRepository(client)
        self.assertFalse(asyncio.run(repo.is_suppressed("")))
        # Short-circuit — no PostgREST call.
        client.table.assert_not_called()

    def test_all_channel_does_not_widen(self) -> None:
        """A query for 'all' must match ONLY globally-applicable rows.

        Symmetric widening would over-match — a webhook asking "is this
        identifier suppressed everywhere" must not be answered yes by
        the existence of a channel-specific suppression.
        """
        client, table = _build_fake_client([])
        repo = SuppressionRepository(client)
        asyncio.run(repo.is_suppressed("x@y.com", channel="all"))
        table.in_.assert_called_with("channel", ["all"])


class TestFilterSuppressed(unittest.TestCase):
    def test_10_in_3_suppressed_one_round_trip(self) -> None:
        inputs = [f"u{i}@example.com" for i in range(10)]
        suppressed = [{"identifier_value": "u2@example.com"},
                      {"identifier_value": "u5@example.com"},
                      {"identifier_value": "u9@example.com"}]
        client, table = _build_fake_client(suppressed)
        repo = SuppressionRepository(client)

        allowed, blocked = asyncio.run(repo.filter_suppressed(inputs, "email"))

        self.assertEqual(len(allowed), 7)
        self.assertEqual(blocked, ["u2@example.com", "u5@example.com", "u9@example.com"])
        # Order preserved in allowed.
        self.assertEqual(allowed,
                         ["u0@example.com", "u1@example.com", "u3@example.com",
                          "u4@example.com", "u6@example.com", "u7@example.com",
                          "u8@example.com"])
        # Exactly one .execute() = one round trip regardless of batch size.
        self.assertEqual(table.execute.call_count, 1)

    def test_dedupes_inputs(self) -> None:
        client, table = _build_fake_client([])
        repo = SuppressionRepository(client)
        asyncio.run(repo.filter_suppressed(["a@x.com", "a@x.com", "b@x.com"], "email"))
        # IN clause built from deduped list.
        in_calls = [c for c in table.in_.call_args_list if c.args[0] == "identifier_value"]
        self.assertEqual(in_calls[0].args[1], ["a@x.com", "b@x.com"])

    def test_empty_input_returns_empty_tuples(self) -> None:
        client, _ = _build_fake_client([])
        repo = SuppressionRepository(client)
        allowed, blocked = asyncio.run(repo.filter_suppressed([], "email"))
        self.assertEqual((allowed, blocked), ([], []))
        client.table.assert_not_called()


class TestAdd(unittest.TestCase):
    def test_returns_row_id_on_insert(self) -> None:
        client, table = _build_fake_client([{"id": 42}])
        repo = SuppressionRepository(client)
        result = asyncio.run(
            repo.add("email", "new@x.com", "manual", channel="email")
        )
        self.assertEqual(result, 42)
        # Verify insert payload.
        sent = table.insert.call_args.args[0]
        self.assertEqual(sent["identifier_type"], "email")
        self.assertEqual(sent["identifier_value"], "new@x.com")
        self.assertEqual(sent["reason"], "manual")
        self.assertEqual(sent["channel"], "email")
        # None-valued optional fields are stripped so DB defaults apply.
        self.assertNotIn("source_campaign_id", sent)
        self.assertNotIn("notes", sent)

    def test_duplicate_returns_none(self) -> None:
        client, table = _build_fake_client([])

        class _DupError(Exception):
            code = "23505"

        table.execute.side_effect = _DupError("duplicate key value")
        repo = SuppressionRepository(client)
        result = asyncio.run(repo.add("email", "dupe@x.com", "manual"))
        self.assertIsNone(result)

    def test_non_duplicate_error_reraises(self) -> None:
        client, table = _build_fake_client([])
        table.execute.side_effect = RuntimeError("connection refused")
        repo = SuppressionRepository(client)
        with self.assertRaises(RuntimeError):
            asyncio.run(repo.add("email", "x@y.com", "manual"))


class TestBulkImport(unittest.TestCase):
    def test_upsert_with_ignore_duplicates(self) -> None:
        # 5 inserted (full data echoed), 2 had collisions (not echoed).
        client, table = _build_fake_client([
            {"id": i} for i in range(5)
        ])
        repo = SuppressionRepository(client)
        items = [
            SuppressionAdd("email", f"u{i}@x.com", "bounce_hard", source_provider="instantly")
            for i in range(7)
        ]
        result = asyncio.run(repo.bulk_import(items))

        self.assertIsInstance(result, BulkImportResult)
        self.assertEqual(result.inserted, 5)
        self.assertEqual(result.skipped_duplicate, 2)
        self.assertEqual(result.failed, 0)

        # Upsert called with ignore_duplicates + correct conflict columns.
        kwargs = table.upsert.call_args.kwargs
        self.assertTrue(kwargs.get("ignore_duplicates"))
        self.assertEqual(kwargs.get("on_conflict"),
                         "identifier_type,identifier_value,channel")

    def test_empty_iterable_short_circuits(self) -> None:
        client, _ = _build_fake_client([])
        repo = SuppressionRepository(client)
        result = asyncio.run(repo.bulk_import([]))
        self.assertEqual(result, BulkImportResult(0, 0, 0))
        client.table.assert_not_called()


class TestUniqueViolationDetector(unittest.TestCase):
    """The hot-path duplicate-detection helper must accept both code= and
    message-only error shapes since supabase-py's error type varies by
    version + test mocks.
    """

    def test_code_attr(self) -> None:
        class _E(Exception):
            code = "23505"
        self.assertTrue(_is_unique_violation(_E("x")))

    def test_message_substring(self) -> None:
        self.assertTrue(_is_unique_violation(Exception("postgrest 23505 duplicate key value")))

    def test_unrelated_error(self) -> None:
        self.assertFalse(_is_unique_violation(Exception("connection refused")))


if __name__ == "__main__":
    unittest.main()
