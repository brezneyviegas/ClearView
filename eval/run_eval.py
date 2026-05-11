"""Eval harness: route fixtures, compare cost vs always-frontier baseline.

Usage:
    python -m eval.run_eval                        # routing-only (no provider call)
    python -m eval.run_eval --live                 # actually call providers
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import litellm

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import load_policy  # noqa: E402
from app.pricing import cost_for, drift_pct  # noqa: E402
from app.router import route  # noqa: E402

FIXTURES = ROOT / "eval" / "fixtures.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="call providers (uses real money)")
    ap.add_argument("--policy", default=os.environ.get("CLEARVIEW_POLICY_PATH", str(ROOT / "policy.yaml")))
    args = ap.parse_args()

    pol = load_policy(args.policy)
    fixtures = json.loads(FIXTURES.read_text())
    baseline = pol.baseline_model

    correct = 0
    native_total = 0.0
    plan_equiv_total = 0.0
    rows = []

    for f in fixtures:
        decision = route(f["prompt"], pol)
        ok = decision.tier == f["expected_tier"]
        correct += int(ok)

        tokens_in = max(1, len(f["prompt"]) // 4)
        tokens_out = 200  # estimate for cost compare
        native_call_cost = 0.0
        latency_ms = 0

        if args.live:
            t0 = time.perf_counter()
            resp = litellm.completion(
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
            "id": f["id"], "expected": f["expected_tier"], "got": decision.tier,
            "ok": ok, "model": decision.model, "reason": decision.reason,
            "native_$": round(native_call_cost, 4), "plan_$": round(plan_call_cost, 4),
            "latency_ms": latency_ms,
        })

    print(f"\nFixtures: {len(fixtures)}")
    print(f"Routing accuracy: {correct}/{len(fixtures)}  ({correct/len(fixtures)*100:.0f}%)")
    print(f"Native total:     ${native_total:.4f}")
    print(f"Plan-equiv total: ${plan_equiv_total:.4f}")
    print(f"Drift / savings:  {drift_pct(native_total, plan_equiv_total):.1f}%")
    print()
    for r in rows:
        mark = "OK" if r["ok"] else "MISS"
        print(f"  [{mark:4}] {r['id']:12} got={r['got']:8} model={r['model']:40} "
              f"native=${r['native_$']:.4f} plan=${r['plan_$']:.4f} reason={r['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
