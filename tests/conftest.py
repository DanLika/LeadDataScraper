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
