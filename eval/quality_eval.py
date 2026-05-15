"""LLM-as-judge quality regression eval.

For each fixture, calls:
    1. the routed model  (whatever ClearView's policy picks)
    2. the baseline model (always-frontier reference)
Then asks a judge model to grade the routed response against the baseline
on a 1–5 scale. Aggregates an average score + quality_drift_pct so CI can
gate on "routing didn't make quality regress more than X%".

This module never mocks anything itself — pass-through to `litellm.completion`.
Unit tests monkeypatch litellm at the module level.

Usage from CLI:
    python -m eval.run_eval --live --quality                # default fixtures
    python -m eval.run_eval --live --quality \
        --quality-fixtures cheap-rename-1,mid-refactor-1   # subset
    python -m eval.run_eval --live --quality \
        --judge-model anthropic/claude-opus-4-7

Public API:
    run_quality(policy, fixtures, *, judge_model, baseline_model,
                routed_model_override=None) -> dict
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from app.config import Policy
from app.router import route as _route


_JUDGE_PROMPT = """\
You are grading whether a candidate AI response meets the same quality bar as
a reference response for the SAME user prompt. Quality means: factual
correctness, instruction-following, completeness, and absence of harmful or
malformed output. Length is NOT quality.

Score on this 1–5 scale and output ONLY the digit, no explanation:
  5 = candidate matches or exceeds reference on every meaningful axis
  4 = candidate is slightly worse but still acceptable for the task
  3 = candidate is materially worse on at least one axis but still usable
  2 = candidate has serious gaps a user would notice and reject
  1 = candidate is wrong, off-topic, refusing, or broken

USER PROMPT:
{prompt}

REFERENCE RESPONSE (from baseline model):
{reference}

CANDIDATE RESPONSE (from routed model):
{candidate}

SCORE (1-5):
"""

# Default quality regression threshold: avg score must be at least this.
DEFAULT_MIN_AVG_SCORE = 4.0
# `quality_drift_pct = (5 - avg_score) / 5 * 100`.  At avg 4.0 this is 20%.
# Operators tighten by lowering this in gate.json.


def _completion_text(model: str, prompt: str, *, max_tokens: int = 400) -> str:
    """Call litellm and return assistant content as a string. Failures
    surface as empty strings — the judge then sees an empty candidate and
    typically scores low, which is the behaviour we want for a regression
    test (a broken upstream IS a quality failure)."""
    import litellm
    try:
        resp = litellm.completion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
        )
    except Exception:
        return ""
    try:
        choices = resp.get("choices") if isinstance(resp, dict) else getattr(resp, "choices", None)
        if not choices:
            return ""
        first = choices[0]
        msg = first.get("message") if isinstance(first, dict) else getattr(first, "message", None)
        if msg is None:
            return ""
        content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
        return (content or "").strip()
    except Exception:
        return ""


_DIGIT_RE = re.compile(r"[1-5]")


def _grade(judge_model: str, prompt: str, reference: str, candidate: str) -> int:
    """Ask the judge for a 1–5 grade. Defaults to 3 (neutral) when the
    judge returns nothing parseable — keeps the eval robust to a flaky
    judge model without silently failing the gate."""
    text = _completion_text(
        judge_model,
        _JUDGE_PROMPT.format(prompt=prompt, reference=reference, candidate=candidate),
        max_tokens=4,
    )
    m = _DIGIT_RE.search(text or "")
    if not m:
        return 3
    return int(m.group(0))


def run_quality(
    policy: Policy,
    fixtures: list[dict[str, Any]],
    *,
    judge_model: str,
    baseline_model: str | None = None,
    routed_model_override: str | None = None,
) -> dict[str, Any]:
    """Grade routed responses against the baseline using a judge model.

    Args:
        policy:               Loaded ClearView policy. Used to resolve which
                              model the router would pick per fixture.
        fixtures:             List of fixture dicts (id, prompt, expected_tier).
        judge_model:          Model id used to grade. Typically the frontier
                              baseline (Opus / GPT-5 / Gemini Pro).
        baseline_model:       Reference model. Defaults to policy.baseline_model.
        routed_model_override: Force every routed call to one model (useful
                              for `--routed clearview-cheap` style sweeps).
                              When None, uses whatever the router picks per
                              fixture (mirrors production behaviour).
    """
    baseline = baseline_model or policy.baseline_model

    rows: list[dict[str, Any]] = []
    score_sum = 0
    skipped = 0

    for f in fixtures:
        prompt = f["prompt"]
        fid = f.get("id", "?")

        # Decide the routed model. Mirrors production unless overridden.
        if routed_model_override:
            routed_model = routed_model_override
        else:
            routed_model = _route(prompt, policy).model

        # Baseline: skip when routed == baseline (would compare a response
        # to itself — uninformative, also doubles cost).
        if routed_model == baseline:
            skipped += 1
            rows.append({
                "id": fid,
                "routed_model": routed_model,
                "baseline_model": baseline,
                "score": 5,
                "skipped": True,
                "reason": "routed == baseline; treated as perfect match",
            })
            score_sum += 5
            continue

        t0 = time.perf_counter()
        candidate = _completion_text(routed_model, prompt)
        candidate_ms = int((time.perf_counter() - t0) * 1000)

        t0 = time.perf_counter()
        reference = _completion_text(baseline, prompt)
        baseline_ms = int((time.perf_counter() - t0) * 1000)

        score = _grade(judge_model, prompt, reference, candidate)
        score_sum += score

        rows.append({
            "id": fid,
            "routed_model": routed_model,
            "baseline_model": baseline,
            "score": score,
            "candidate_ms": candidate_ms,
            "baseline_ms": baseline_ms,
            "candidate_len": len(candidate),
            "reference_len": len(reference),
        })

    n = len(fixtures)
    avg = (score_sum / n) if n else 0.0
    # Drift: how far below the 5/5 ceiling the routed responses landed.
    quality_drift_pct = round(((5.0 - avg) / 5.0) * 100.0, 2) if n else 0.0
    below = sum(1 for r in rows if r["score"] < DEFAULT_MIN_AVG_SCORE)

    return {
        "fixtures": n,
        "skipped_same_model": skipped,
        "avg_score": round(avg, 3),
        "quality_drift_pct": quality_drift_pct,
        "judge_model": judge_model,
        "baseline_model": baseline,
        "below_floor_count": below,
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# Subset helpers
# ---------------------------------------------------------------------------

def load_fixtures(path: Path | str) -> list[dict[str, Any]]:
    return json.loads(Path(path).read_text())


def filter_fixtures(fixtures: list[dict[str, Any]], ids: list[str] | None) -> list[dict[str, Any]]:
    if not ids:
        return fixtures
    wanted = set(ids)
    return [f for f in fixtures if f.get("id") in wanted]
