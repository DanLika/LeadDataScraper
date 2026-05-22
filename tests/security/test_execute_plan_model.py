"""Tests for the /execute Pydantic surface added in commit 3b51772.

Locks in the security invariants of ExecutePlanRequest + ExecutePlanParams:

- Task must be one of the 12 names in the AgenticRouter handler allowlist.
  Any other string is rejected at the model boundary before reaching
  `router.execute_task`, so an authed caller cannot invoke a privileged or
  unknown handler by hand-crafting a plan.
- Params is a closed schema (`extra='forbid'`). Unknown keys are rejected,
  so adding a new handler-read key in `agentic_router.py` requires a
  corresponding model update — surfaces the surface change in code review.
- Bounded `constr` lengths on every accepted key — keeps an attacker from
  shovelling 10 MB of payload through the AI router.
- `model_dump(exclude_none=True)` is the only safe way to feed the dict
  to handlers, because handlers use `params.get(key, default)` patterns
  (`_generate_campaign_strategy` defaults `filters` to `"high-risk"`).
  Without `exclude_none`, the explicit `None` would shadow the default.

Regression guard: if anyone later replaces `ExecutableTask` with `str` or
removes `extra='forbid'`, these tests fail.
"""

import unittest

from pydantic import ValidationError

from backend.main import ExecutePlanParams, ExecutePlanRequest


# Mirror of the Literal in backend/main.py — duplicated on purpose so a
# silent rename of the live tuple trips this test.
EXPECTED_TASKS = frozenset({
    "DATABASE_QUERY",
    "STATUS_CHECK",
    "SEO_AUDIT",
    "OUTREACH_DRAFT",
    "GET_INSIGHTS",
    "DATA_MERGE",
    "DEEP_HUNT",
    "RUN_MASSIVE_PIPELINE",
    "LINKEDIN_DRAFT",
    "DISCOVERY_SEARCH",
    "DEEP_ENRICHMENT",
    "CAMPAIGN_STRATEGY",
})


class TestExecutableTaskAllowlist(unittest.TestCase):
    def test_all_known_tasks_accepted(self):
        for task in EXPECTED_TASKS:
            with self.subTest(task=task):
                req = ExecutePlanRequest(task=task)
                self.assertEqual(req.task, task)

    def test_unknown_task_rejected(self):
        for bad in ("RM_RF", "delete_all", "", "database_query", "ADMIN"):
            with self.subTest(task=bad):
                with self.assertRaises(ValidationError):
                    ExecutePlanRequest(task=bad)

    def test_case_sensitive(self):
        # Allowlist is uppercase Literal — lowercase variants must fail.
        with self.assertRaises(ValidationError):
            ExecutePlanRequest(task="deep_hunt")


class TestExecutePlanParamsClosedSchema(unittest.TestCase):
    def test_unknown_param_key_rejected(self):
        with self.assertRaises(ValidationError):
            ExecutePlanRequest(
                task="DEEP_HUNT",
                params={"evil": "payload"},
            )

    def test_unknown_param_alongside_known_still_rejected(self):
        # extra='forbid' must reject ANY unknown key, even if known keys
        # are present alongside.
        with self.assertRaises(ValidationError):
            ExecutePlanRequest(
                task="DEEP_HUNT",
                params={"unique_key": "abc", "evil": "payload"},
            )

    def test_empty_params_allowed(self):
        req = ExecutePlanRequest(task="GET_INSIGHTS")
        self.assertIsNone(req.params)

    def test_all_known_keys_accepted(self):
        req = ExecutePlanRequest(
            task="DISCOVERY_SEARCH",
            params={
                "unique_key": "abc",
                "query": "dental clinic",
                "location": "Belgrade",
                "query_text": "How many leads in segment X?",
                "filters": "high-risk",
                "type": "default",
            },
        )
        self.assertEqual(req.params.unique_key, "abc")
        self.assertEqual(req.params.query, "dental clinic")


class TestExecutePlanParamsBoundedLengths(unittest.TestCase):
    def test_unique_key_max_length(self):
        ExecutePlanRequest(task="DEEP_HUNT", params={"unique_key": "a" * 128})
        with self.assertRaises(ValidationError):
            ExecutePlanRequest(task="DEEP_HUNT", params={"unique_key": "a" * 129})

    def test_unique_key_min_length(self):
        # constr(min_length=1) — empty string must fail.
        with self.assertRaises(ValidationError):
            ExecutePlanRequest(task="DEEP_HUNT", params={"unique_key": ""})

    def test_query_max_length(self):
        ExecutePlanRequest(task="DISCOVERY_SEARCH", params={"query": "x" * 500})
        with self.assertRaises(ValidationError):
            ExecutePlanRequest(task="DISCOVERY_SEARCH", params={"query": "x" * 501})

    def test_query_text_max_length(self):
        # Caps billing-per-request on the AI sub-question path.
        ExecutePlanRequest(task="DATABASE_QUERY", params={"query_text": "y" * 4000})
        with self.assertRaises(ValidationError):
            ExecutePlanRequest(task="DATABASE_QUERY", params={"query_text": "y" * 4001})

    def test_filters_max_length(self):
        with self.assertRaises(ValidationError):
            ExecutePlanRequest(task="CAMPAIGN_STRATEGY", params={"filters": "z" * 65})


class TestExcludeNonePreservesHandlerDefaults(unittest.TestCase):
    """`_generate_campaign_strategy` does `params.get("filters", "high-risk")`.
    If model_dump emits `filters=None` for an unset field, that None wins
    over the default and changes handler behaviour. The /execute endpoint
    MUST call `model_dump(exclude_none=True)`."""

    def test_dump_drops_unset_optional_fields(self):
        req = ExecutePlanRequest(task="CAMPAIGN_STRATEGY", params={})
        dumped = req.model_dump(exclude_none=True)
        # `params` itself is None-equivalent ({} dumps to {} with exclude_none
        # applied — Pydantic still keeps the empty container). Either shape
        # is fine; what matters is no key=None leaks through.
        params = dumped.get("params") or {}
        self.assertNotIn("filters", params)
        self.assertNotIn("query", params)
        self.assertNotIn("unique_key", params)

    def test_dump_keeps_explicitly_set_fields(self):
        req = ExecutePlanRequest(
            task="CAMPAIGN_STRATEGY",
            params={"filters": "high-risk"},
        )
        dumped = req.model_dump(exclude_none=True)
        self.assertEqual(dumped["params"]["filters"], "high-risk")

    def test_dump_drops_top_level_none_params(self):
        req = ExecutePlanRequest(task="GET_INSIGHTS")
        dumped = req.model_dump(exclude_none=True)
        # `router.execute_task` reads `plan.get("params", {})` — so absence
        # is functionally equivalent to {}.
        self.assertNotIn("params", dumped)


class TestExecutePlanRequestRootSchema(unittest.TestCase):
    def test_extra_key_at_root_rejected(self):
        with self.assertRaises(ValidationError):
            ExecutePlanRequest(task="DEEP_HUNT", extra_field="anything")

    def test_task_required(self):
        with self.assertRaises(ValidationError):
            ExecutePlanRequest()


if __name__ == "__main__":
    unittest.main()
