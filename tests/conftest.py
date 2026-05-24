"""Pytest collection conftest.

Adds the project root to ``sys.path`` so test modules at any depth
under ``tests/`` can ``from src.core...`` (or ``from src.utils...``
etc.) without each test file re-doing the ``sys.path.insert`` dance.

The 2026-05-22 test reorg moved a chunk of unit tests from
``tests/test_X.py`` to ``tests/unit/test_X.py``. The old siblings
had top-of-file ``sys.path.insert(0, str(Path(__file__).resolve()
.parents[2]))`` lines that ran *after* the first ``from src...``
import — fine when the import was at depth 1 because pytest's
rootdir-discovery already had project root on the path, but broken
at depth 2 because rootdir-relative imports don't promote subdir
packages without an ``__init__.py`` chain. Locked in by 2026-05-24
smoke run hitting ``ModuleNotFoundError: No module named 'src'``
on ``tests/unit/test_agentic_router.py``.

Placing the ``sys.path`` patch in a ``tests/`` conftest fires
before any test module is collected, so every test file from this
directory or below resolves ``from src...`` cleanly without
per-file boilerplate.
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Test-suite-wide neutering of the Gemini budget gate.
#
# `src.utils.gemini_call.guarded_generate_content[_async]` runs a SQLite
# budget gate (`check_budget`) before every Gemini call and a counter
# bookkeeping step (`record_usage`) after. The gate is correct for prod
# but pathological for tests: the SQLite file at
# ``<repo>/data/gemini_budget.db`` accumulates across pytest invocations,
# every test that exercises a Gemini-shaped code path increments the
# counter (even when the real client is mocked, because the *wrapper*
# always pays the gate), and after a few hundred runs the counter
# saturates at 4_999_xxx / 5_000_000 and EVERY subsequent test that
# routes through the wrapper fails with ``BudgetExceededError``.
#
# Verified live in the 2026-05-24 smoke session: the counter sat at
# 4_998_700 / 5_000_000 after a single pytest run, causing
# `test_prompt_snapshots`, `test_agentic_router`,
# `test_prompt_injection_corpus`, and `test_agentic_router_behavior` to
# all fail on the same trace path. Per-file mocking (see the
# `test_prompt_snapshots` asyncSetUp patch) is whack-a-mole; the right
# fix is to neuter the gate at import time for the entire test suite.
#
# Patching the names at `src.utils.gemini_call` covers both the sync and
# async wrappers because they both call `check_budget` / `record_usage`
# as bare names (post-`from .gemini_budget import ...`), which resolve
# to the `gemini_call` module's globals — not the original module.
# Tests that want the real gate behavior can override per-test with
# ``monkeypatch.setattr("src.utils.gemini_call.check_budget", real_fn)``.
# ---------------------------------------------------------------------------
def _neuter_gemini_budget() -> None:
    try:
        import src.utils.gemini_call as _gc
    except ImportError:
        # Some tests don't pull `src.*` into the import graph; that's
        # fine — they won't hit the gate either.
        return
    # Stash originals on the module so the gate-specific test file
    # (tests/unit/test_guarded_generate_content.py) can pull them back
    # in via an autouse `monkeypatch.setattr` fixture. Without this
    # hand-off the budget-gate tests can't exercise the real behavior
    # they exist to verify.
    _gc._real_check_budget = _gc.check_budget  # type: ignore[attr-defined]
    _gc._real_record_usage = _gc.record_usage  # type: ignore[attr-defined]
    _gc.check_budget = lambda *_a, **_kw: None
    _gc.record_usage = lambda *_a, **_kw: None


_neuter_gemini_budget()
