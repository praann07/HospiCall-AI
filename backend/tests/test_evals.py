"""Quality gate: the scripted evaluation suite must meet its thresholds on every commit.

The suite itself lives in `evals/` (scenarios.json + run_evals.py) and can be run standalone with
`python -m evals.run_evals`; here it is wired into pytest so CI fails when behaviour regresses.
"""

import pytest

from evals.run_evals import GATES, Report, run


@pytest.fixture(scope="module")
def report() -> Report:
    return run()


def test_every_gate_passes(report):
    details = "\n".join(f"[{f.suite}] {f.case}: {f.detail}" for f in report.failures)
    assert report.passed, f"eval gates failed: {report.gate_results()}\n{details}"


def test_no_individual_failures(report):
    assert report.failures == []


def test_scenario_suite_is_not_hollow(report):
    # Guards against someone "fixing" a failing eval by deleting scenarios.
    assert report.intent_total >= 90
    assert report.dialogue_total >= 25
    assert report.safety_total >= 15


def test_latency_gate_is_enforced(report):
    assert report.p95_ms <= GATES["p95_latency_ms"]
