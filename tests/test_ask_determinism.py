"""
Determinism test for /ask routing.

Sends the same natural-language instruction 20 times to AgenticRouter
.route_instruction (the workhorse behind /ask) and asserts Gemini routes
consistently:

  1. All 20 runs return the same task name (single bucket, not split).
  2. The task is one of the expected candidates for a discover-and-audit
     prompt: DISCOVERY_SEARCH or RUN_MASSIVE_PIPELINE.
  3. params.query is semantically stable — pairwise cosine of Gemini
     text embeddings >= 0.9 (only enforced when the task carries a query
     param, i.e. DISCOVERY_SEARCH).
  4. params.limit (and common synonyms) presence-pattern + value is
     identical across all 20 runs. NOTE: neither tool declares `limit`
     in its schema (agentic_router.py:103-145), so Gemini either omits
     it or invents a free-form arg — the determinism contract is "same
     thing 20 times", whatever the model decides.

Catches the most common Gemini-instability symptoms: split routing
(7 runs DISCOVERY_SEARCH, 13 RUN_MASSIVE_PIPELINE), drifting query
phrasing, and limit-extraction flakiness.

Live test — requires GEMINI_API_KEY. Skipped otherwise. Supabase is
mocked (empty lead_index path).
"""

import asyncio
import math
import os
import sys
import unittest
import pytest
from collections import Counter
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

GEMINI_KEY = os.getenv("GEMINI_API_KEY")

INSTRUCTION = "find 5 dentists in Sarajevo and audit them"
N_RUNS = 20
ALLOWED_TASKS = {"DISCOVERY_SEARCH", "RUN_MASSIVE_PIPELINE"}
QUERY_SIMILARITY_THRESHOLD = 0.90
EMBED_MODEL = "text-embedding-004"
# Tool schema only declares `query`/`location`/`filters`; Gemini occasionally
# tacks on a numeric arg under one of these names — accept any one and demand
# the same one (and same value) across all 20 runs.
LIMIT_KEYS = ("limit", "count", "num_results", "num", "max_results")


def _cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _extract_limit(params: dict):
    """Return (key_used, value) for the first matching synonym, else (None, None)."""
    for k in LIMIT_KEYS:
        if k in params and params[k] is not None:
            return k, params[k]
    return None, None


async def _route_one(router, instruction: str):
    return await router.route_instruction(instruction)


@pytest.mark.live
@unittest.skipUnless(GEMINI_KEY, "Requires GEMINI_API_KEY for live Gemini calls")
class TestAskDeterminism(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env_patcher = patch.dict(
            os.environ,
            {
                "GEMINI_API_KEY": GEMINI_KEY or "",
            },
        )
        self.env_patcher.start()

        # Mock Supabase so route_instruction's lead-index lookup short-circuits.
        # An empty lead_index keeps prompt text identical across runs (no row
        # ordering variance to bleed into Gemini's routing).
        self.sb_patcher = patch("src.core.agentic_router.SupabaseHelper")
        sb_mock = self.sb_patcher.start()
        sb_mock.return_value.client = None

        from src.core.agentic_router import AgenticRouter

        self.router = AgenticRouter()
        self.assertIsNotNone(self.router.client, "Gemini client must initialize")

        # Fire all 20 routings in parallel. gemini-flash-latest RPM ceiling is
        # comfortably above 20; if rate-limiting becomes a problem, swap to a
        # serial loop or chunked gather.
        self.plans = await asyncio.gather(
            *(_route_one(self.router, INSTRUCTION) for _ in range(N_RUNS))
        )

    async def asyncTearDown(self):
        self.sb_patcher.stop()
        self.env_patcher.stop()

    def test_no_routing_errors(self):
        failures = [
            f"run {i}: {p.get('error') or p}"
            for i, p in enumerate(self.plans)
            if "error" in p
        ]
        self.assertFalse(failures, "Routing errors:\n" + "\n".join(failures))

    def test_all_runs_same_task(self):
        tasks = [p.get("task") for p in self.plans]
        dist = Counter(tasks)
        self.assertEqual(
            len(dist), 1, f"Task routing unstable across {N_RUNS} runs: {dict(dist)}"
        )
        chosen = next(iter(dist))
        self.assertIn(
            chosen,
            ALLOWED_TASKS,
            f"Routed to unexpected task {chosen!r} (allowed: {ALLOWED_TASKS})",
        )

    def test_query_param_semantically_similar(self):
        """params.query stability — pairwise cosine >= 0.9 (DISCOVERY_SEARCH only)."""
        task = self.plans[0].get("task")
        if task != "DISCOVERY_SEARCH":
            self.skipTest(f"task={task} carries no params.query")

        queries = []
        for i, p in enumerate(self.plans):
            params = dict(p.get("params") or {})
            q = params.get("query")
            self.assertIsInstance(
                q,
                str,
                f"run {i}: params.query missing or non-string. Got params={params}",
            )
            queries.append(q)

        # Batch all 20 into a single embedding call.
        result = self.router.client.models.embed_content(
            model=EMBED_MODEL,
            contents=queries,
        )
        vecs = [list(e.values) for e in result.embeddings]
        self.assertEqual(len(vecs), N_RUNS, "Embedding count mismatch")

        failures = []
        for i in range(N_RUNS):
            for j in range(i + 1, N_RUNS):
                sim = _cosine(vecs[i], vecs[j])
                if sim < QUERY_SIMILARITY_THRESHOLD:
                    failures.append(
                        f"cos({i},{j})={sim:.3f}  {queries[i]!r} vs {queries[j]!r}"
                    )
        # Surface distinct phrasings even on success — helps regression triage
        distinct = sorted(set(queries))
        self.assertFalse(
            failures,
            f"Query embedding drift below {QUERY_SIMILARITY_THRESHOLD}:\n"
            + "\n".join(failures)
            + f"\nDistinct phrasings observed: {distinct}",
        )

    def test_limit_param_consistent(self):
        """Same key (or none) and same value across all 20 runs."""
        extracted = [_extract_limit(dict(p.get("params") or {})) for p in self.plans]
        # Bucket by (key, value) tuple. None,None means Gemini omitted it.
        shapes = Counter(extracted)
        self.assertEqual(
            len(shapes),
            1,
            f"limit param shape unstable across {N_RUNS} runs: {dict(shapes)}",
        )

        key, value = next(iter(shapes))
        if key is None:
            # Gemini didn't emit a limit at all — that's deterministic too.
            return

        # If a limit was emitted, it should match the "5" in the prompt.
        try:
            self.assertEqual(
                int(value),
                5,
                f"limit extracted as {value!r} via key {key!r}, expected 5",
            )
        except (TypeError, ValueError):
            self.fail(f"limit value not coercible to int: key={key!r} value={value!r}")


if __name__ == "__main__":
    unittest.main()
