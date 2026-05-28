"""N+1 prevention test for the Phase 15.3 batch-fetch path.

Pins the contract: a 100-message dispatch tick claim batch should
result in ≤4 PostgREST SELECT round trips for the lead/step/variant/
prior-message joins (one ``fetch_many`` per repo, not one per row).

The test instruments the supabase-py mock to count ``execute()`` calls
that pertain to SELECT (vs UPDATE / INSERT). If a future refactor
reintroduces an N+1 path, this trips loudly.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock

from src.repositories.campaign_message_repo import CampaignMessageRepository
from src.repositories.lead_repo import LeadRepository
from src.repositories.sequence_step_repo import SequenceStepRepository
from src.repositories.sequence_variant_repo import SequenceVariantRepository


class _InstrumentedDb:
    """Counts SELECT execute() calls per table.

    Tables tracked: leads, sequence_steps, sequence_variants,
    campaign_messages.
    """

    def __init__(self) -> None:
        self.client = MagicMock()
        self.select_calls: dict[str, int] = {}
        self.client.table.side_effect = self._table

    def _table(self, name: str) -> MagicMock:
        recorder = self
        chain = MagicMock()
        chain._is_select = False

        def select(*_args, **_kwargs):
            chain._is_select = True
            return chain

        chain.select.side_effect = select
        chain.eq.return_value = chain
        chain.in_.return_value = chain
        chain.lt.return_value = chain
        chain.lte.return_value = chain
        chain.order.return_value = chain
        chain.limit.return_value = chain

        def execute():
            if chain._is_select:
                recorder.select_calls[name] = recorder.select_calls.get(name, 0) + 1
                chain._is_select = False
            # Return rows shaped for the table being queried so the
            # _row_to_* dataclass constructors don't KeyError.
            if name == "sequence_steps":
                row = {
                    "id": "step-x",
                    "sequence_id": "seq-x",
                    "step_index": 0,
                    "channel": "email",
                    "delay_days": 0,
                    "delay_hours": 0,
                    "thread_with_prior": False,
                    "branch_condition": "always",
                    "send_window_start": "09:00:00",
                    "send_window_end": "17:00:00",
                    "send_days": "mon,tue,wed,thu,fri",
                    "created_at": "",
                }
            elif name == "sequence_variants":
                row = {
                    "id": "var-x",
                    "step_id": "step-x",
                    "variant_label": "A",
                    "subject_template": None,
                    "body_template": "hi",
                    "weight": 50,
                    "ai_model_used": None,
                    "ai_prompt_version": None,
                    "created_at": "",
                }
            else:
                row = {"id": "x", "unique_key": "x", "step_id": "x"}
            return MagicMock(data=[row])

        chain.execute.side_effect = execute
        return chain


class TestN1Prevention(unittest.TestCase):
    def test_100_message_batch_uses_4_selects(self) -> None:
        """A simulated 100-message claim: each repo's fetch_many is one
        round trip regardless of input size."""
        db = _InstrumentedDb()
        lead_repo = LeadRepository(db.client)
        step_repo = SequenceStepRepository(db.client)
        variant_repo = SequenceVariantRepository(db.client)
        msg_repo = CampaignMessageRepository(db.client)

        # Simulate 100 distinct claim rows feeding into the 4 batch fetches.
        lead_uks = {f"uk-{i}" for i in range(100)}
        step_ids = {f"step-{i}" for i in range(100)}
        prior_ids = {f"prior-{i}" for i in range(100)}

        async def _run():
            await lead_repo.fetch_many(lead_uks)
            await step_repo.fetch_many(step_ids)
            await variant_repo.fetch_many_for_steps(step_ids)
            await msg_repo.fetch_many(prior_ids)

        asyncio.run(_run())

        # Exactly one SELECT per repo invocation.
        self.assertEqual(db.select_calls.get("leads"), 1)
        self.assertEqual(db.select_calls.get("sequence_steps"), 1)
        self.assertEqual(db.select_calls.get("sequence_variants"), 1)
        self.assertEqual(db.select_calls.get("campaign_messages"), 1)

        # Total SELECTs across the whole batch-fetch phase: 4.
        # (Test would catch e.g. a per-id loop inside fetch_many.)
        self.assertEqual(sum(db.select_calls.values()), 4)

    def test_empty_inputs_zero_selects(self) -> None:
        db = _InstrumentedDb()
        lead_repo = LeadRepository(db.client)

        async def _run():
            await lead_repo.fetch_many([])
            await lead_repo.fetch_many(set())
            await lead_repo.fetch_many(None)  # type: ignore[arg-type]

        asyncio.run(_run())
        self.assertEqual(db.select_calls.get("leads", 0), 0)


if __name__ == "__main__":
    unittest.main()
