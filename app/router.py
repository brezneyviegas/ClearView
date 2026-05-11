"""Routing pipeline: rules first, classifier fallback. Returns picked model + reason."""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any

import litellm

from .config import Policy

CODE_FENCE_RE = re.compile(r"```")

log = logging.getLogger("clearview.router")

# Tier order for upward escalation when a tier has no available models.
_TIER_ORDER = ["cheap", "mid", "frontier"]

# Module-level availability set populated at startup by build_availability().
# Maps tier name -> ordered list of available models in that tier.
_AVAILABLE: dict[str, list[str]] = {}


@dataclass
class RouteDecision:
    tier: str
    model: str
    reason: str  # human-readable: "rule:tiny_prompt" / "classifier:score=4"


def _provider_available(model: str) -> bool:
    """Decide if a given prefixed model id is callable based on env var presence."""
    if model.startswith("ollama/") or model.startswith("ollama_chat/"):
        return True
    if model.startswith("anthropic/"):
        # Subscription mode: the local Claude CLI fulfills Anthropic calls
        # without an API key. Treat as available when the operator opted in.
        if os.environ.get("CLEARVIEW_USE_CLAUDE_CLI") == "1":
            return True
        return bool(os.environ.get("ANTHROPIC_API_KEY"))
    if model.startswith("openai/"):
        return bool(os.environ.get("OPENAI_API_KEY"))
    if model.startswith("gemini/") or model.startswith("google/"):
        return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    # Unknown provider prefix: assume available (litellm will surface the error).
    return True


def build_availability(policy: Policy) -> dict[str, list[str]]:
    """Compute per-tier list of available models given current env. Stores module-level."""
    global _AVAILABLE
    avail: dict[str, list[str]] = {}
    for tier, models in policy.tiers.items():
        avail[tier] = [m for m in models if _provider_available(m)]
    _AVAILABLE = avail
    return avail


def availability() -> dict[str, list[str]]:
    return _AVAILABLE


def _approx_tokens(text: str) -> int:
    # Cheap approximation: 4 chars/token. Avoid loading tokenizer on hot path.
    return max(1, len(text) // 4)


def _has_code(text: str) -> bool:
    return bool(CODE_FENCE_RE.search(text))


def _contains_any(text: str, needles: list[str]) -> bool:
    low = text.lower()
    return any(n.lower() in low for n in needles)


def _eval_rule(cond: dict[str, Any], prompt: str, header_tier: str | None) -> bool:
    if "header" in cond:
        return header_tier is not None
    tokens = _approx_tokens(prompt)
    if "tokens_lt" in cond and not (tokens < cond["tokens_lt"]):
        return False
    if "tokens_gte" in cond and not (tokens >= cond["tokens_gte"]):
        return False
    if cond.get("no_code") and _has_code(prompt):
        return False
    if "contains_any" in cond and not _contains_any(prompt, cond["contains_any"]):
        return False
    return True


def _pick_model(tier: str, policy: Policy) -> str:
    """Pick first available model in tier; if none, escalate up tier ladder."""
    if tier not in policy.tiers:
        tier = "cheap"
    # If availability not yet built (e.g. tests calling _pick_model directly),
    # treat all tier members as available.
    avail = _AVAILABLE if _AVAILABLE else {t: list(ms) for t, ms in policy.tiers.items()}

    try:
        start_idx = _TIER_ORDER.index(tier)
    except ValueError:
        start_idx = 0

    for idx in range(start_idx, len(_TIER_ORDER)):
        t = _TIER_ORDER[idx]
        models = avail.get(t) or []
        if models:
            if t != tier:
                log.warning(
                    "tier %s has no available models (missing provider keys); escalating to %s",
                    tier, t,
                )
            return models[0]

    # Nothing available anywhere — fall back to first declared model in requested tier.
    fallback = policy.tiers.get(tier) or policy.tiers.get("cheap") or []
    if not fallback:
        raise RuntimeError("no models configured in any tier")
    log.error("no provider keys set for any tier; using configured default %s", fallback[0])
    return fallback[0]


def _classify(prompt: str, policy: Policy) -> int:
    cls = policy.classifier
    msg = cls.prompt.format(prompt=prompt[:4000])
    try:
        resp = litellm.completion(
            model=cls.model,
            messages=[{"role": "user", "content": msg}],
            max_tokens=4,
            temperature=0,
        )
        out = resp["choices"][0]["message"]["content"].strip()
        digit = next((c for c in out if c.isdigit()), "3")
        return max(1, min(5, int(digit)))
    except Exception:
        return 3  # safe middle on classifier failure


def route(prompt: str, policy: Policy, header_tier: str | None = None) -> RouteDecision:
    # Validate header_tier: ignore if not a known tier name.
    if header_tier is not None and header_tier not in policy.tiers:
        log.warning("ignoring invalid x-clearview-tier header value: %r", header_tier)
        header_tier = None

    # Rule layer
    for rule in policy.rules:
        cond = rule.get("if", {})
        if not _eval_rule(cond, prompt, header_tier):
            continue
        then = rule.get("then")
        if then == "header_value" and header_tier:
            tier = header_tier
        else:
            tier = then
        if tier in policy.tiers:
            return RouteDecision(tier=tier, model=_pick_model(tier, policy),
                                 reason=f"rule:{rule.get('name', 'unnamed')}")

    # Classifier fallback
    if policy.classifier.enabled:
        score = _classify(prompt, policy)
        tier = policy.classifier.score_to_tier.get(score, "mid")
        return RouteDecision(tier=tier, model=_pick_model(tier, policy),
                             reason=f"classifier:score={score}")

    # Default
    return RouteDecision(tier="cheap", model=_pick_model("cheap", policy),
                         reason="default:cheap")
