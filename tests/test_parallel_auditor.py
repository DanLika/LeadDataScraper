import pytest
from src.core.parallel_auditor import ParallelAuditor

def test_parallel_auditor_init():
    auditor = ParallelAuditor()
    assert auditor is not None
