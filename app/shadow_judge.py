"""LLM-as-judge for auto-shadow pairs (routing-accuracy Layer 2).

When a primary call is auto-shadowed to the tier the classifier would have
picked, this module grades the shadow response against the primary response.
The verdict feeds a misroute corpus: if the (cheaper-tier) primary keeps
winning, the route was right; if the shadow keeps winning, the policy is
under-routing and a rule/threshold needs tightening.

Self-contained — does NOT import the eval package (app must not depend on eval).
The judge call goes through litellm. Failures are swallowed (return None) so a
flaky judge never disrupts the request path; the shadow itself still records.
"""
from __future__ import annotations

import logging
import re

import litellm

log = logging.getLogger("clearview.shadow_judge")

# Grades the SHADOW (candidate) against the PRIMARY (reference). Reference is
# whatever the live policy actually served the user, so a high score means
# "shadow was at least as good as what we served" → potential under-route.
_JUDGE_PROMPT = """\
You are comparing two AI responses to the SAME user prompt. Judge quality only:
factual correctness, instruction-following, completeness, absence of harmful or
malformed output. Length is NOT quality.

Output ONLY a single digit 1-5 comparing the CANDIDATE against the REFERENCE:
  5 = candidate clearly better than reference
  4 = candidate slightly better
  3 = roughly equal
  2 = candidate slightly worse
  1 = candidate clearly worse

USER PROMPT:
{prompt}

REFERENCE RESPONSE (what we served):
{reference}

CANDIDATE RESPONSE (alternative tier):
{candidate}

SCORE (1-5):
"""

_DIGIT_RE = re.compile(r"[1-5]")


def _parse_score(text: str) -> int | None:
    m = _DIGIT_RE.search(text or "")
    return int(m.group(0)) if m else None


def _winner_from_score(score: int) -> str:
    if score >= 4:
        return "shadow"
    if score <= 2:
        return "primary"
    return "tie"


def judge(*, prompt: str, primary_text: str, shadow_text: str,
          judge_model: str) -> dict | None:
    """Grade shadow vs primary. Returns {score, winner} or None on failure.

    score is the 1-5 candidate(shadow)-vs-reference(primary) grade.
    winner: 'shadow' (under-routed), 'primary' (route was right), or 'tie'.
    """
    if not primary_text.strip() or not shadow_text.strip():
        return None
    msg = _JUDGE_PROMPT.format(
        prompt=prompt[:4000],
        reference=primary_text[:4000],
        candidate=shadow_text[:4000],
    )
    try:
        from . import llm_dispatch
        resp = llm_dispatch.completion(
            judge_model, [{"role": "user", "content": msg}],
            max_tokens=4, temperature=0)
        out = (resp["choices"][0]["message"]["content"] or "").strip()
    except Exception:
        log.warning("shadow judge call failed (model=%s)", judge_model, exc_info=True)
        return None
    score = _parse_score(out)
    if score is None:
        log.warning("shadow judge returned unparseable output: %r", out[:80])
        return None
    return {"score": score, "winner": _winner_from_score(score)}
