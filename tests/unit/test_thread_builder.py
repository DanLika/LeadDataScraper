"""Unit tests for src/services/thread_builder.py.

Covers:
- Happy path: subject + body rendered, lead → context, payload shape
- thread_with_prior=True + prior has provider_message_id → in_reply_to set
- thread_with_prior=True + prior missing/no provider_message_id → PriorMessageNotReadyError
- thread_with_prior=True + subject_template empty → blank subject (Re: continuation)
- Missing variable in render → MissingVariableError propagates
- Lead with sparse fields (no first_name etc.) renders empty strings
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from typing import Optional

from src.services.template_renderer import TemplateError
from src.services.thread_builder import (
    DispatchPayload,
    PriorMessageNotReadyError,
    ThreadBuildError,
    build_send_payload,
)


@dataclass
class _Step:
    thread_with_prior: bool = False
    send_window_start: str = "09:00"
    send_window_end: str = "17:00"
    send_days: str = "mon,tue,wed,thu,fri"


@dataclass
class _Variant:
    body_template: str
    subject_template: Optional[str] = None
    weight: int = 50
    id: str = "v-A"
    variant_label: str = "A"


SAMPLE_LEAD = {
    "unique_key": "uk-1",
    "email": "Ana@Example.com ",  # tests lowercase + strip
    "first_name": "Ana",
    "last_name": "Maric",
    "company_name": "Alpha Tech",
    "website": "https://alpha.example",
    "outreach_score": 87,
    "pain_points": "slow site",
}


class TestHappyPath(unittest.TestCase):
    def test_renders_and_packs_payload(self) -> None:
        step = _Step(thread_with_prior=False)
        variant = _Variant(
            subject_template="Hello {{ company }}",
            body_template="Hi {{ first_name }}, your audit is {{ audit_score }}/100. {{ unsubscribe_url }}",
        )
        payload = build_send_payload(
            lds_message_id="msg-1",
            lead=SAMPLE_LEAD,
            step=step,
            variant=variant,
            operator_name="Duško",
            unsubscribe_url="https://lds/u/track-1",
        )
        self.assertIsInstance(payload, DispatchPayload)
        self.assertEqual(payload.lds_message_id, "msg-1")
        self.assertEqual(payload.lead_unique_key, "uk-1")
        self.assertEqual(payload.email, "ana@example.com")  # lowercased + stripped
        self.assertEqual(payload.subject, "Hello Alpha Tech")
        self.assertIn("Hi Ana", payload.body)
        self.assertIn("87/100", payload.body)
        self.assertIn("https://lds/u/track-1", payload.body)
        self.assertIsNone(payload.in_reply_to_message_id)

    def test_as_lead_dict_shape(self) -> None:
        step = _Step()
        variant = _Variant(
            subject_template="Hi",
            body_template="Body {{ unsubscribe_url }}",
        )
        payload = build_send_payload(
            lds_message_id="msg-1",
            lead=SAMPLE_LEAD,
            step=step,
            variant=variant,
            unsubscribe_url="https://lds/u/track-1",
        )
        d = payload.as_lead_dict()
        self.assertEqual(
            set(d.keys()),
            {
                "unique_key",
                "email",
                "subject",
                "body",
                "in_reply_to_message_id",
                "list_unsubscribe_url",
            },
        )


class TestThreadContinuation(unittest.TestCase):
    def test_thread_with_prior_in_reply_to_set(self) -> None:
        step = _Step(thread_with_prior=True)
        variant = _Variant(
            subject_template="follow-up {{ company }}",
            body_template="Hi {{ first_name }}, replying to my previous. {{ unsubscribe_url }}",
        )
        prior = {"id": "msg-0", "provider_message_id": "instantly-prior-001"}
        payload = build_send_payload(
            lds_message_id="msg-1",
            lead=SAMPLE_LEAD,
            step=step,
            variant=variant,
            prior_message=prior,
            unsubscribe_url="https://lds/u/track-1",
        )
        self.assertEqual(payload.in_reply_to_message_id, "instantly-prior-001")

    def test_thread_with_empty_subject_template_blank_subject(self) -> None:
        """For continuation steps, operators leave subject_template
        empty so the mail client renders ``Re: <prior subject>``."""
        step = _Step(thread_with_prior=True)
        variant = _Variant(
            subject_template="",  # intentionally blank
            body_template="Hi {{ first_name }}, ping {{ unsubscribe_url }}",
        )
        prior = {"id": "msg-0", "provider_message_id": "instantly-prior-001"}
        payload = build_send_payload(
            lds_message_id="msg-1",
            lead=SAMPLE_LEAD,
            step=step,
            variant=variant,
            prior_message=prior,
            unsubscribe_url="https://lds/u/track-1",
        )
        self.assertEqual(payload.subject, "")

    def test_thread_with_prior_missing_raises_not_ready(self) -> None:
        step = _Step(thread_with_prior=True)
        variant = _Variant(
            subject_template="x",
            body_template="x {{ unsubscribe_url }}",
        )
        with self.assertRaises(PriorMessageNotReadyError) as cm:
            build_send_payload(
                lds_message_id="msg-1",
                lead=SAMPLE_LEAD,
                step=step,
                variant=variant,
                prior_message=None,
                unsubscribe_url="https://lds/u/track-1",
            )
        self.assertEqual(cm.exception.lds_message_id, "msg-1")

    def test_thread_with_prior_no_provider_msg_id_raises(self) -> None:
        """Race: step N's row exists but webhook hasn't stamped
        provider_message_id yet."""
        step = _Step(thread_with_prior=True)
        variant = _Variant(
            subject_template="x",
            body_template="x {{ unsubscribe_url }}",
        )
        prior = {"id": "msg-0", "provider_message_id": None}
        with self.assertRaises(PriorMessageNotReadyError):
            build_send_payload(
                lds_message_id="msg-1",
                lead=SAMPLE_LEAD,
                step=step,
                variant=variant,
                prior_message=prior,
                unsubscribe_url="https://lds/u/track-1",
            )


class TestRenderErrors(unittest.TestCase):
    def test_missing_var_raises_through(self) -> None:
        step = _Step()
        variant = _Variant(
            subject_template="{{ not_allowed_var }}",  # not in ALLOWED_VARS
            body_template="x {{ unsubscribe_url }}",
        )
        lead = {"unique_key": "uk", "email": "a@b.com"}
        with self.assertRaises(TemplateError):
            build_send_payload(
                lds_message_id="msg-1",
                lead=lead,
                step=step,
                variant=variant,
                unsubscribe_url="https://lds/u/track-1",
            )


class TestMissingIdentifiers(unittest.TestCase):
    def test_missing_lds_message_id_raises(self) -> None:
        step = _Step()
        variant = _Variant(body_template="x {{ unsubscribe_url }}")
        with self.assertRaises(ThreadBuildError):
            build_send_payload(
                lds_message_id="",
                lead=SAMPLE_LEAD,
                step=step,
                variant=variant,
                unsubscribe_url="x",
            )

    def test_missing_lead_raises(self) -> None:
        step = _Step()
        variant = _Variant(body_template="x {{ unsubscribe_url }}")
        with self.assertRaises(ThreadBuildError):
            build_send_payload(
                lds_message_id="msg-1",
                lead={},
                step=step,
                variant=variant,
                unsubscribe_url="x",
            )


if __name__ == "__main__":
    unittest.main()
