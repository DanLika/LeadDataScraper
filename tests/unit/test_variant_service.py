"""Unit tests for src/services/variant_service.py.

Covers the create_variant validation pipeline:
- Syntax error → SYNTAX code
- Disallowed vars → DISALLOWED_VARS code + listed vars in result
- Missing unsubscribe_url on email channel → MISSING_UNSUBSCRIBE code
- LinkedIn channel skips unsubscribe check
- UNIQUE collision (repo returns None) → DUPLICATE code
- Happy path → ok=True + variant returned
"""

from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from src.repositories.sequence_variant_repo import SequenceVariant
from src.services.variant_service import (
    ErrorCodes,
    VariantService,
)


def _build_repo(create_result: Any) -> MagicMock:
    repo = MagicMock()
    repo.create = AsyncMock(return_value=create_result)
    return repo


def _sample_variant() -> SequenceVariant:
    return SequenceVariant(
        id="v-A",
        step_id="step-1",
        variant_label="A",
        subject_template="Hi {{ first_name }}",
        body_template="Hi {{ first_name }} {{ unsubscribe_url }}",
        weight=50,
        ai_model_used=None,
        ai_prompt_version=None,
        created_at="",
    )


class TestSyntaxValidation(unittest.TestCase):
    def test_body_syntax_error_returns_syntax_code(self) -> None:
        repo = _build_repo(_sample_variant())
        service = VariantService(repo)
        result = asyncio.run(
            service.create_variant(
                step_id="step-1",
                step_channel="email",
                variant_label="A",
                body_template="Hi {{ unclosed",
            )
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, ErrorCodes.SYNTAX)
        repo.create.assert_not_called()


class TestAllowlist(unittest.TestCase):
    def test_disallowed_vars_returns_error(self) -> None:
        repo = _build_repo(_sample_variant())
        service = VariantService(repo)
        result = asyncio.run(
            service.create_variant(
                step_id="step-1",
                step_channel="email",
                variant_label="A",
                body_template="Hi {{ first_name }} {{ api_key }} {{ unsubscribe_url }}",
            )
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, ErrorCodes.DISALLOWED_VARS)
        self.assertEqual(result.disallowed_vars, ("api_key",))
        repo.create.assert_not_called()

    def test_disallowed_vars_in_subject_also_caught(self) -> None:
        repo = _build_repo(_sample_variant())
        service = VariantService(repo)
        result = asyncio.run(
            service.create_variant(
                step_id="step-1",
                step_channel="email",
                variant_label="A",
                subject_template="{{ admin_token }}",
                body_template="Hi {{ first_name }} {{ unsubscribe_url }}",
            )
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, ErrorCodes.DISALLOWED_VARS)
        self.assertEqual(result.disallowed_vars, ("admin_token",))


class TestColdUnsubscribeEnforcement(unittest.TestCase):
    def test_email_without_unsubscribe_rejected(self) -> None:
        repo = _build_repo(_sample_variant())
        service = VariantService(repo)
        result = asyncio.run(
            service.create_variant(
                step_id="step-1",
                step_channel="email",
                variant_label="A",
                body_template="Hi {{ first_name }}, no opt-out here.",
            )
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, ErrorCodes.MISSING_UNSUBSCRIBE)
        repo.create.assert_not_called()

    def test_linkedin_channel_skips_unsubscribe_check(self) -> None:
        repo = _build_repo(_sample_variant())
        service = VariantService(repo)
        result = asyncio.run(
            service.create_variant(
                step_id="step-1",
                step_channel="linkedin",
                variant_label="A",
                body_template="Hi {{ first_name }}, would love to connect.",
            )
        )
        self.assertTrue(result.ok)
        repo.create.assert_called_once()


class TestDuplicate(unittest.TestCase):
    def test_repo_returns_none_maps_to_duplicate(self) -> None:
        repo = _build_repo(None)  # UNIQUE collision returns None
        service = VariantService(repo)
        result = asyncio.run(
            service.create_variant(
                step_id="step-1",
                step_channel="email",
                variant_label="A",
                body_template="Hi {{ first_name }} {{ unsubscribe_url }}",
            )
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, ErrorCodes.DUPLICATE)


class TestHappyPath(unittest.TestCase):
    def test_valid_variant_persisted(self) -> None:
        variant = _sample_variant()
        repo = _build_repo(variant)
        service = VariantService(repo)
        result = asyncio.run(
            service.create_variant(
                step_id="step-1",
                step_channel="email",
                variant_label="A",
                subject_template="Hi {{ first_name }}",
                body_template="Hi {{ first_name }}, see {{ unsubscribe_url }}",
                weight=70,
                ai_model_used="gemini-flash-latest",
                ai_prompt_version="v3",
            )
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.variant.id, "v-A")
        self.assertIsNone(result.error_code)
        # Repo got the full payload.
        call_kwargs = repo.create.call_args.kwargs
        self.assertEqual(call_kwargs["weight"], 70)
        self.assertEqual(call_kwargs["ai_model_used"], "gemini-flash-latest")


if __name__ == "__main__":
    unittest.main()
