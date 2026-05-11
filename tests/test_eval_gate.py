"""Regression gate: run the eval harness in dry mode against the on-disk fixtures
and assert that the rule layer and overall routing accuracy stay above floor
values. This is the CI guard for silent routing drift.

We never run with --live here -- live mode would need provider API keys and
real money. Cost thresholds are deliberately skipped (the gate function does
that itself in dry mode).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.run_eval import gate, run

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "eval" / "fixtures.json"
GATE_FILE = REPO_ROOT / "eval" / "gate.json"

MIN_FIXTURES = 50


def _load_fixture_count() -> int:
    return len(json.loads(FIXTURES.read_text()))


def test_eval_gate_dry_run_accuracy() -> None:
    """Dry-mode eval must hit the routing-accuracy floors in eval/gate.json."""
    if _load_fixture_count() < MIN_FIXTURES:
        pytest.skip(f"fixtures.json has fewer than {MIN_FIXTURES} fixtures")

    results = run(policy_path=str(REPO_ROOT / "policy.yaml"), live=False)

    assert results["fixtures"] >= MIN_FIXTURES
    assert results["overall_accuracy_pct"] >= 75, (
        f"overall_accuracy_pct regressed: {results['overall_accuracy_pct']:.1f}% < 75%"
    )
    assert results["rule_accuracy_pct"] >= 90, (
        f"rule_accuracy_pct regressed: {results['rule_accuracy_pct']:.1f}% < 90%"
    )
    # Rule layer must actually be exercised; if it isn't, the gate is meaningless.
    assert results["rule_hit_count"] >= MIN_FIXTURES * 0.5


def test_eval_gate_against_on_disk_thresholds() -> None:
    """The shipped eval/gate.json must pass against a fresh dry run."""
    if _load_fixture_count() < MIN_FIXTURES:
        pytest.skip(f"fixtures.json has fewer than {MIN_FIXTURES} fixtures")

    results = run(policy_path=str(REPO_ROOT / "policy.yaml"), live=False)
    thresholds = json.loads(GATE_FILE.read_text())
    ok, failures = gate(results, thresholds, live=False)
    assert ok, "eval/gate.json thresholds failed in dry mode: " + "; ".join(failures)


def test_eval_gate_function_detects_regression() -> None:
    """A pathological threshold (100% rule accuracy required) should be caught."""
    if _load_fixture_count() < MIN_FIXTURES:
        pytest.skip(f"fixtures.json has fewer than {MIN_FIXTURES} fixtures")

    results = run(policy_path=str(REPO_ROOT / "policy.yaml"), live=False)
    # Force a failure by demanding an impossible accuracy.
    bad = {"min_overall_accuracy_pct": 999.0}
    ok, failures = gate(results, bad, live=False)
    assert not ok
    assert any("overall_accuracy_pct" in m for m in failures)
