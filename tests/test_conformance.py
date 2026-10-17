from fractions import Fraction

import pytest

from dvm.conformance import (
    CONFORMANCE_VECTORS,
    CV_001_CUT,
    CV_002_SMOOTH,
    CV_003_VFR,
    CV_004_SCALE,
    CV_005_CLIP,
    run_conformance_suite,
)
from dvm.timeline import Rect


def test_official_conformance_suite_passes():
    """Verify that DVM core passes 100% of all official conformance vectors."""
    report = run_conformance_suite()
    assert report.all_passed is True
    assert report.passed_vectors == len(CONFORMANCE_VECTORS)
    assert report.total_vectors == len(CONFORMANCE_VECTORS)


@pytest.mark.parametrize("vector", CONFORMANCE_VECTORS, ids=lambda v: v.id)
def test_individual_conformance_vector(vector):
    report = run_conformance_suite()
    res = next(r for r in report.results if r.vector_id == vector.id)
    assert res.passed is True
    assert res.failed_checks == 0
    assert len(res.failures) == 0


def test_conformance_suite_detects_arithmetic_deviations():
    """Verify that the conformance suite catches an incorrect evaluator (e.g. integer truncation)."""
    def buggy_evaluator(tl, pts):
        # Truncates instead of round-half-up
        r = Rect(0, 0, 100, 100)
        return r

    report = run_conformance_suite(evaluator_fn=buggy_evaluator)
    assert report.all_passed is False
    assert report.passed_vectors < report.total_vectors


def test_conformance_summary_format():
    report = run_conformance_suite()
    summary = report.format_summary()
    assert "DVM Conformance Suite Results: 5/5 vectors passed" in summary
    assert "[PASS] CV-001-CUT" in summary
    assert "[PASS] CV-002-SMOOTH" in summary
