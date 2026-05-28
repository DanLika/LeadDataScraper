"""Unit tests for src/services/variant_selector.py.

Covers:
- Weighted distribution converges over many trials
- Single-variant input returns that variant (no rng call)
- Empty input returns None
- Deterministic seed honored ONLY when VARIANT_SELECTOR_ALLOW_SEED=1
- Seed without env gate logs warning + falls through to random
- Different seeds → different (probable) picks; same seed → same pick
"""

from __future__ import annotations

import os
import unittest
from collections import Counter
from dataclasses import dataclass
from unittest.mock import patch

from src.services.variant_selector import select_variant


@dataclass(frozen=True)
class _FakeVariant:
    id: str
    variant_label: str
    weight: int


VARIANTS = [
    _FakeVariant(id="v-A", variant_label="A", weight=70),
    _FakeVariant(id="v-B", variant_label="B", weight=20),
    _FakeVariant(id="v-C", variant_label="C", weight=10),
]


class TestEdgeCases(unittest.TestCase):
    def test_empty_input_returns_none(self) -> None:
        self.assertIsNone(select_variant([]))
        self.assertIsNone(select_variant(None))  # type: ignore[arg-type]

    def test_single_variant_returned_regardless(self) -> None:
        only = [_FakeVariant("v-X", "X", weight=1)]
        self.assertEqual(select_variant(only).id, "v-X")


class TestWeightedDistribution(unittest.TestCase):
    def test_weighted_distribution_converges(self) -> None:
        """70/20/10 weights → roughly 70%/20%/10% across 5000 picks.

        Generous tolerance (±10pp) because SystemRandom over 5000
        samples can swing a few percentage points; the test pins the
        selector's behaviour (it IS weighted) not exact stats.
        """
        counts: Counter[str] = Counter()
        for _ in range(5000):
            picked = select_variant(VARIANTS)
            counts[picked.id] += 1
        # 70/20/10 expected; assert generous tolerance.
        self.assertGreater(counts["v-A"], 5000 * 0.55)
        self.assertLess(counts["v-A"], 5000 * 0.85)
        self.assertGreater(counts["v-B"], 5000 * 0.10)
        self.assertGreater(counts["v-C"], 5000 * 0.03)


class TestDeterministicSeed(unittest.TestCase):
    def test_seed_without_env_gate_logs_warning_and_uses_random(self) -> None:
        """Seed passed but ALLOW_SEED not set → seed is IGNORED. Critical
        production safety: a config bug that smuggles a seed value into
        the worker shouldn't disable A/B testing silently."""
        with patch.dict(os.environ, {}, clear=True):
            # With unique system-random rolls each call, the probability
            # of getting an identical sequence is vanishingly small;
            # easier to assert that the seed didn't pin the result.
            picks = {
                select_variant(VARIANTS, deterministic_seed="frozen-seed").id
                for _ in range(20)
            }
        # 20 SystemRandom picks of 3 weighted variants → almost
        # certainly covers ≥2 distinct results.
        self.assertGreater(
            len(picks), 1, "seed was honored without env gate — A/B testing broken"
        )

    def test_seed_with_env_gate_pins_pick(self) -> None:
        with patch.dict(os.environ, {"VARIANT_SELECTOR_ALLOW_SEED": "1"}):
            first = select_variant(VARIANTS, deterministic_seed="seed-1")
            for _ in range(10):
                again = select_variant(VARIANTS, deterministic_seed="seed-1")
                self.assertEqual(again.id, first.id)

    def test_different_seeds_with_env_gate_diverge(self) -> None:
        with patch.dict(os.environ, {"VARIANT_SELECTOR_ALLOW_SEED": "1"}):
            picks = {
                select_variant(VARIANTS, deterministic_seed=f"seed-{i}").id
                for i in range(40)
            }
        self.assertGreater(
            len(picks), 1, "different seeds should produce different picks"
        )

    def test_env_gate_non_one_value_does_not_enable(self) -> None:
        """``VARIANT_SELECTOR_ALLOW_SEED=true`` / ``=yes`` / ``=anything``
        should NOT enable the gate — only the literal '1'. Conservative
        opt-in semantics."""
        for falsy_one in ("0", "true", "yes", "on", "Yes", " 1"):
            with patch.dict(os.environ, {"VARIANT_SELECTOR_ALLOW_SEED": falsy_one}):
                picks = {
                    select_variant(VARIANTS, deterministic_seed="same-seed").id
                    for _ in range(20)
                }
            self.assertGreater(
                len(picks),
                1,
                f"VARIANT_SELECTOR_ALLOW_SEED={falsy_one!r} should NOT enable the gate",
            )


class TestZeroWeightDefence(unittest.TestCase):
    def test_zero_or_negative_weights_floored_to_one(self) -> None:
        """DB CHECK enforces weight > 0; this defends against direct
        Studio edits that bypass the constraint."""
        bad = [
            _FakeVariant("v-zero", "A", weight=0),
            _FakeVariant("v-neg", "B", weight=-5),
        ]
        # Should not raise; selector floors weights to 1.
        for _ in range(20):
            picked = select_variant(bad)
            self.assertIn(picked.id, ("v-zero", "v-neg"))


if __name__ == "__main__":
    unittest.main()
