"""Eval harness: route fixtures, compare cost vs always-frontier baseline, optionally gate CI on accuracy / cost regressions.

Usage:
    python -m eval.run_eval                                    # routing-only (no provider call)
    python -m eval.run_eval --live                             # actually call providers (uses real money)
    python -m eval.run_eval --out results.json                 # dump structured results to disk
    python -m eval.run_eval --gate eval/gate.json              # exit 1 if any threshold fails
    python -m eval.run_eval --gate eval/gate.json --live       # gate including cost thresholds

Public API (for pytest and other tooling):
    run(policy_path=None, live=False) -> dict
    gate(results, thresholds, *, live) -> tuple[bool, list[str]]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import load_policy  # noqa: E402
from app.pricing import cost_for, drift_pct  # noqa: E402
from app import router as _router_mod  # noqa: E402
from app.router import build_availability, route  # noqa: E402

FIXTURES = ROOT / "eval" / "fixtures.json"
DEFAULT_GATE = ROOT / "eval" / "gate.json"


def _pct(num: int, denom: int) -> float:
    return (num / denom * 100.0) if denom else 0.0


def run(policy_path: str | None = None, live: bool = False) -> dict[str, Any]:
    """Run the eval harness and return structured results.

    In dry mode (live=False) the classifier is never invoked (it would need an API
    key). Fixtures whose rule layer would fall through to the classifier are
    counted under `classifier_skipped` and excluded from classifier accuracy.
    """
    pol = load_policy(policy_path or os.environ.get("CLEARVIEW_POLICY_PATH", str(ROOT / "policy.yaml")))
    # Populate availability so _pick_model does not silently use the configured
    # fallback when no provider keys are present. In dry mode we accept whatever
    # the env produces -- model identity is not load-bearing for tier accuracy.
    # Snapshot the module-level cache first; we restore it before returning so
    # we don't leak state into other tests that share the process.
    _prev_avail = dict(_router_mod._AVAILABLE)
    build_availability(pol)

    fixtures = json.loads(FIXTURES.read_text())
    baseline = pol.baseline_model

    rule_hit = 0
    rule_correct = 0
    classifier_hit = 0
    classifier_correct = 0
    classifier_skipped = 0  # fixtures that would invoke classifier in dry mode

    correct = 0
    native_total = 0.0
    plan_equiv_total = 0.0
    rows: list[dict[str, Any]] = []

    # Litellm import deferred so dry-mode `run()` works in test envs that lack
    # provider keys -- import is still cheap because run_eval imports app.router
    # which already pulls litellm.
    import litellm  # noqa: F401

    try:
        for f in fixtures:
            decision = route(f["prompt"], pol)
            ok = decision.tier == f["expected_tier"]
            correct += int(ok)

            is_classifier = decision.reason.startswith("classifier:")
            is_rule = decision.reason.startswith("rule:")

            # In dry mode the classifier still returns a result (it catches its
            # own exception and falls back to score=3 -> mid). That result is
            # not a true classifier evaluation, so we exclude it from
            # classifier accuracy numbers when --live is off.
            if is_classifier:
                if live:
                    classifier_hit += 1
                    classifier_correct += int(ok)
                else:
                    classifier_skipped += 1
            elif is_rule:
                rule_hit += 1
                rule_correct += int(ok)
            # default:cheap path (no rule, classifier disabled) is rare; we
            # leave it out of both buckets.

            tokens_in = max(1, len(f["prompt"]) // 4)
            tokens_out = 200  # estimate for cost compare in dry mode
            latency_ms = 0

            if live:
                t0 = time.perf_counter()
                import litellm as _ll
                resp = _ll.completion(
                    model=decision.model,
                    messages=[{"role": "user", "content": f["prompt"]}],
                    max_tokens=400,
                )
                latency_ms = int((time.perf_counter() - t0) * 1000)
                usage = resp.get("usage") if isinstance(resp, dict) else getattr(resp, "usage", {}) or {}
                if hasattr(usage, "model_dump"):
                    usage = usage.model_dump()
                tokens_in = int(usage.get("prompt_tokens", tokens_in) or tokens_in)
                tokens_out = int(usage.get("completion_tokens", tokens_out) or tokens_out)

            native_call_cost = cost_for(decision.model, tokens_in, tokens_out)
            plan_call_cost = cost_for(baseline, tokens_in, tokens_out)
            native_total += native_call_cost
            plan_equiv_total += plan_call_cost
            rows.append({
                "id": f["id"],
                "expected": f["expected_tier"],
                "got": decision.tier,
                "ok": ok,
                "model": decision.model,
                "reason": decision.reason,
                "native_$": round(native_call_cost, 6),
                "plan_$": round(plan_call_cost, 6),
                "latency_ms": latency_ms,
            })
    finally:
        # Restore the pre-run availability snapshot so callers that share the
        # process (pytest) do not see leaked state from this run.
        _router_mod._AVAILABLE = _prev_avail

    n = len(fixtures)
    classifier_seen = classifier_hit  # only counts live classifier hits
    results: dict[str, Any] = {
        "fixtures": n,
        "live": bool(live),
        "overall_correct": correct,
        "overall_accuracy_pct": _pct(correct, n),
        "rule_hit_count": rule_hit,
        "rule_correct": rule_correct,
        "rule_accuracy_pct": _pct(rule_correct, rule_hit),
        "classifier_hit_count": classifier_seen,
        "classifier_correct": classifier_correct,
        "classifier_accuracy_pct": _pct(classifier_correct, classifier_seen),
        "classifier_skipped": classifier_skipped,
        "native_total_usd": round(native_total, 6),
        "plan_equiv_total_usd": round(plan_equiv_total, 6),
        "drift_pct": round(drift_pct(native_total, plan_equiv_total), 2),
        "rows": rows,
    }
    return results


def _print_results(r: dict[str, Any]) -> None:
    n = r["fixtures"]
    print(f"\nFixtures: {n}  (live={r['live']})")
    print(f"Overall accuracy:    {r['overall_correct']}/{n}  ({r['overall_accuracy_pct']:.1f}%)")
    rh = r["rule_hit_count"]
    print(f"Rule-layer hits:     {rh}   correct={r['rule_correct']}   "
          f"accuracy={r['rule_accuracy_pct']:.1f}%")
    ch = r["classifier_hit_count"]
    if r["live"]:
        print(f"Classifier hits:     {ch}   correct={r['classifier_correct']}   "
              f"accuracy={r['classifier_accuracy_pct']:.1f}%")
    else:
        print(f"Classifier hits:     0   (dry mode: {r['classifier_skipped']} fixtures "
              f"deferred -- use --live for classifier accuracy)")
    print(f"Native total:        ${r['native_total_usd']:.4f}")
    print(f"Plan-equiv total:    ${r['plan_equiv_total_usd']:.4f}")
    print(f"Drift / savings:     {r['drift_pct']:.1f}%")
    print()
    for row in r["rows"]:
        mark = "OK" if row["ok"] else "MISS"
        print(f"  [{mark:4}] {row['id']:26} got={row['got']:8} "
              f"model={row['model']:40} native=${row['native_$']:.4f} "
              f"plan=${row['plan_$']:.4f} reason={row['reason']}")


def gate(results: dict[str, Any], thresholds: dict[str, Any], *, live: bool) -> tuple[bool, list[str]]:
    """Compare results against a thresholds dict.

    Returns (ok, failures). `failures` is a list of human-readable FAIL strings.

    Routing thresholds (always evaluated when present):
      - min_overall_accuracy_pct
      - min_rule_accuracy_pct
      - min_classifier_accuracy_pct  (skipped if not live)

    Cost thresholds (only evaluated when live=True):
      - max_native_total_usd
      - min_drift_pct
    """
    failures: list[str] = []

    def _check_min(key: str, actual: float, label: str) -> None:
        if key in thresholds:
            want = float(thresholds[key])
            if actual < want:
                failures.append(f"FAIL {label}: actual={actual:.2f} < min={want:.2f}")

    def _check_max(key: str, actual: float, label: str) -> None:
        if key in thresholds:
            want = float(thresholds[key])
            if actual > want:
                failures.append(f"FAIL {label}: actual={actual:.4f} > max={want:.4f}")

    _check_min("min_overall_accuracy_pct", results["overall_accuracy_pct"], "overall_accuracy_pct")
    _check_min("min_rule_accuracy_pct", results["rule_accuracy_pct"], "rule_accuracy_pct")

    if live:
        if results["classifier_hit_count"] > 0:
            _check_min("min_classifier_accuracy_pct",
                       results["classifier_accuracy_pct"], "classifier_accuracy_pct")
        _check_max("max_native_total_usd", results["native_total_usd"], "native_total_usd")
        _check_min("min_drift_pct", results["drift_pct"], "drift_pct")

        # Quality regression thresholds (only when --quality was run AND the
        # gate file includes the keys). Skipped silently otherwise so existing
        # gate.json files keep working unchanged.
        quality = results.get("quality") or {}
        if quality:
            if "min_avg_quality_score" in thresholds:
                _check_min("min_avg_quality_score",
                           float(quality.get("avg_score", 0.0)),
                           "avg_quality_score")
            if "max_quality_drift_pct" in thresholds:
                _check_max("max_quality_drift_pct",
                           float(quality.get("quality_drift_pct", 0.0)),
                           "quality_drift_pct")
    else:
        # In dry mode skip cost thresholds entirely -- dry costs come from
        # approx-token estimates and are not meaningful for regression.
        skipped = []
        if "max_native_total_usd" in thresholds:
            skipped.append("max_native_total_usd")
        if "min_drift_pct" in thresholds:
            skipped.append("min_drift_pct")
        if "min_classifier_accuracy_pct" in thresholds:
            skipped.append("min_classifier_accuracy_pct (no classifier in dry mode)")
        if skipped:
            print(f"[gate] dry mode: skipping cost/classifier thresholds: {', '.join(skipped)}")

    return (len(failures) == 0, failures)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="call providers (uses real money)")
    ap.add_argument("--policy", default=os.environ.get("CLEARVIEW_POLICY_PATH", str(ROOT / "policy.yaml")))
    ap.add_argument("--out", default=None, help="write structured results JSON to this path")
    ap.add_argument("--gate", default=None, help="path to a thresholds JSON; exits non-zero on failure")
    ap.add_argument("--quality", action="store_true",
                    help="LLM-as-judge quality eval — also calls baseline + judge models. Requires --live.")
    ap.add_argument("--quality-fixtures", default=None,
                    help="comma-separated fixture ids to limit the quality eval to (saves $$)")
    ap.add_argument("--judge-model", default=None,
                    help="model id to use as the quality judge; defaults to policy.baseline_model")
    args = ap.parse_args()

    results = run(policy_path=args.policy, live=args.live)
    _print_results(results)

    if args.quality:
        if not args.live:
            print("\n[quality] --quality requires --live (it calls providers). Skipping.")
        else:
            from eval.quality_eval import (
                run_quality, load_fixtures, filter_fixtures,
            )
            pol = load_policy(args.policy)
            fixtures = load_fixtures(FIXTURES)
            subset_ids = (
                [s.strip() for s in args.quality_fixtures.split(",") if s.strip()]
                if args.quality_fixtures else None
            )
            fixtures = filter_fixtures(fixtures, subset_ids)
            judge = args.judge_model or pol.baseline_model
            q = run_quality(pol, fixtures, judge_model=judge)
            results["quality"] = q
            print(f"\nQuality eval: judge={q['judge_model']} baseline={q['baseline_model']}")
            print(f"  fixtures={q['fixtures']}  skipped_same_model={q['skipped_same_model']}")
            print(f"  avg_score={q['avg_score']}  quality_drift_pct={q['quality_drift_pct']}%")
            print(f"  below_floor (score<{int(__import__('eval.quality_eval', fromlist=['DEFAULT_MIN_AVG_SCORE']).DEFAULT_MIN_AVG_SCORE)})="
                  f"{q['below_floor_count']}")

    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2) + "\n")
        print(f"\n[out] wrote {args.out}")

    if args.gate:
        thresholds = json.loads(Path(args.gate).read_text())
        ok, failures = gate(results, thresholds, live=args.live)
        print()
        if ok:
            print(f"[gate] PASS  ({args.gate})")
            return 0
        for f in failures:
            print(f"[gate] {f}")
        print(f"[gate] {len(failures)} threshold(s) failed ({args.gate})")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
