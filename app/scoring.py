"""Stock-market composite provider scoring (checklist #13).

Ranks providers per prompt bucket on a single 0-1 "multiplier" that blends:
  - quality  — judge win-rate from provider_score (higher = better)
  - cost     — avg $ per call           (lower = better)
  - latency  — avg ms per call          (lower = better)
  - burn     — avg output tokens/call   (lower = better; verbosity proxy)

Each metric is min-max normalized ACROSS the providers in the bucket so the
best scores 1.0 and the worst 0.0 (lower-is-better metrics inverted). The
composite is a weighted sum; weights are env-tunable and renormalized to sum 1.

The router uses the top composite as the route — like picking the best ticker.
When only one provider has data (nothing to compare), normalization is
degenerate, so the caller falls back to win-rate / first-listed (cold start).

Env:
    CLEARVIEW_PROVIDER_SCORING=1     enable composite ranking in routing
    CLEARVIEW_SCORE_W_QUALITY=0.5
    CLEARVIEW_SCORE_W_COST=0.25
    CLEARVIEW_SCORE_W_LATENCY=0.15
    CLEARVIEW_SCORE_W_BURN=0.10
"""
from __future__ import annotations

import os

from . import telemetry

_DEFAULT_WEIGHTS = {"quality": 0.5, "cost": 0.25, "latency": 0.15, "burn": 0.10}


def enabled() -> bool:
    return os.environ.get("CLEARVIEW_PROVIDER_SCORING", "0").strip() == "1"


def weights() -> dict[str, float]:
    w = {}
    for k, default in _DEFAULT_WEIGHTS.items():
        try:
            w[k] = max(0.0, float(os.environ.get(f"CLEARVIEW_SCORE_W_{k.upper()}", default)))
        except ValueError:
            w[k] = default
    total = sum(w.values()) or 1.0
    return {k: v / total for k, v in w.items()}


def _winrate(ps: dict) -> float:
    n = int(ps.get("n") or 0)
    if n <= 0:
        return 0.0
    return (int(ps.get("wins") or 0) + 0.5 * int(ps.get("ties") or 0)) / n


def _norm(values: dict[str, float], *, higher_better: bool) -> dict[str, float]:
    """Min-max normalize to 0-1. higher_better=False inverts (low value → 1.0).
    All-equal (incl. single provider) → 0.5 for everyone (no signal)."""
    if not values:
        return {}
    lo, hi = min(values.values()), max(values.values())
    if hi - lo < 1e-12:
        return {k: 0.5 for k in values}
    out = {}
    for k, v in values.items():
        unit = (v - lo) / (hi - lo)          # 0 at min, 1 at max
        out[k] = unit if higher_better else (1.0 - unit)
    return out


def composite_scores(bucket: str) -> dict[str, dict]:
    """Return {provider: {score, quality, cost, latency, burn, n}} for a bucket.

    `score` is the composite multiplier (0-1). The sub-fields are the NORMALIZED
    components (0-1) so a UI can show the breakdown. All inputs come from the
    self-contained provider_score row (wins/ties + cost/latency/burn sums),
    averaged over n — so the shadow provider's cost/latency count too.
    """
    rows = {r["provider"]: r for r in telemetry.provider_scores(bucket=bucket)}
    providers = set(rows)
    if not providers:
        return {}

    def _avg(r, key):
        n = int(r.get("n") or 0)
        return (float(r.get(key) or 0.0) / n) if n else 0.0

    quality_raw = {p: _winrate(rows[p]) for p in providers}
    cost_raw = {p: _avg(rows[p], "sum_cost") for p in providers}
    lat_raw = {p: _avg(rows[p], "sum_latency_ms") for p in providers}
    burn_raw = {p: _avg(rows[p], "sum_tokens_out") for p in providers}

    q = _norm(quality_raw, higher_better=True)
    c = _norm(cost_raw, higher_better=False)
    l = _norm(lat_raw, higher_better=False)
    b = _norm(burn_raw, higher_better=False)
    w = weights()

    out: dict[str, dict] = {}
    for p in providers:
        score = (w["quality"] * q[p] + w["cost"] * c[p]
                 + w["latency"] * l[p] + w["burn"] * b[p])
        out[p] = {
            "score": round(score, 4),
            "quality": round(q[p], 3), "cost": round(c[p], 3),
            "latency": round(l[p], 3), "burn": round(b[p], 3),
            "n": int(rows[p].get("n") or 0),
        }
    return out


def best_by_composite(bucket: str, candidates: list[str], min_n: int) -> str | None:
    """Top composite-score provider among `candidates` with >= min_n samples.
    None when fewer than two candidates qualify (nothing to rank → cold start)."""
    if not bucket or len(candidates) < 2:
        return None
    scored = composite_scores(bucket)
    eligible = [(p, scored[p]["score"]) for p in candidates
                if p in scored and scored[p]["n"] >= min_n]
    if len(eligible) < 2:
        return None
    eligible.sort(key=lambda kv: kv[1], reverse=True)
    return eligible[0][0]
