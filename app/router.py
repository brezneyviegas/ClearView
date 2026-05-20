"""Routing pipeline: rules first, classifier fallback. Returns picked model + reason."""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any

import litellm

from . import embed_classifier
from .config import Policy

CODE_FENCE_RE = re.compile(r"```")
STACK_TRACE_RE = re.compile(
    r"(traceback \(most recent call last\)|\bexception\b|\berror:\s|"
    r"\bat\s+[\w.$<>]+\([^)]*:\d+\)|file \"[^\"]+\", line \d+)",
    re.IGNORECASE,
)
MATH_SYMBOL_RE = re.compile(
    r"(∑|∫|√|≤|≥|≠|≈|∞|\\frac|\\sum|\\int|[A-Za-z0-9)]\s*\^\s*[A-Za-z0-9(])",
    re.IGNORECASE,
)
FILE_PATH_RE = re.compile(
    r"((?:\.{1,2}/|/)[\w./-]+|[A-Za-z]:\\[\w.\\-]+|[\w.-]+/[\w./-]+|"
    r"\b[\w.-]+\.(?:py|ts|tsx|js|jsx|go|rs|java|rb|php|cs|cpp|c|h|sql|ya?ml|json|toml)\b)"
)
URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
CODEISH_RE = re.compile(
    r"(^\s{2,}\S+|[{};]$|\b(def|class|function|const|let|var|if|for|while|return)\b)",
    re.IGNORECASE,
)
IMPERATIVE_RE = re.compile(
    r"^\s*(please\s+)?(fix|debug|implement|write|create|build|refactor|design|"
    r"review|optimize|derive|prove|explain|summarize|compare|analyze)\b",
    re.IGNORECASE,
)

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


@dataclass
class ClassifierDecision:
    score: int
    confidence: float


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
        # Subscription mode: the local Codex CLI fulfills OpenAI calls via the
        # ChatGPT Plus/Pro plan without an API key. Treat as available when the
        # operator opted in.
        if os.environ.get("CLEARVIEW_USE_CODEX_CLI") == "1":
            return True
        return bool(os.environ.get("OPENAI_API_KEY"))
    if model.startswith("gemini/") or model.startswith("google/"):
        if os.environ.get("CLEARVIEW_USE_GEMINI_CLI") == "1":
            return True
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


def _has_multiline_code_without_fence(text: str) -> bool:
    if _has_code(text):
        return False
    codeish = 0
    for line in text.splitlines():
        if CODEISH_RE.search(line):
            codeish += 1
    return codeish >= 2


def _contains_any(text: str, needles: list[str]) -> bool:
    for needle in needles:
        normalized = " ".join(str(needle).strip().split())
        if not normalized:
            continue
        pattern = r"(?<!\w)" + re.escape(normalized).replace(r"\ ", r"\s+") + r"(?!\w)"
        if re.search(pattern, text, flags=re.IGNORECASE):
            return True
    return False


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
    if cond.get("stack_trace") and not STACK_TRACE_RE.search(prompt):
        return False
    if cond.get("math_symbols") and not MATH_SYMBOL_RE.search(prompt):
        return False
    if cond.get("file_path") and not FILE_PATH_RE.search(prompt):
        return False
    if cond.get("url") and not URL_RE.search(prompt):
        return False
    if cond.get("multiline_code_no_fence") and not _has_multiline_code_without_fence(prompt):
        return False
    if cond.get("imperative") and not IMPERATIVE_RE.search(prompt):
        return False
    if cond.get("question") and "?" not in prompt:
        return False
    return True


def _pick_model(tier: str, policy: Policy) -> str:
    """Pick first available model in tier; if none, escalate up tier ladder."""
    if tier not in policy.tiers:
        tier = "cheap"
    # If availability not yet built (e.g. tests calling _pick_model directly),
    # treat all tier members as available.
    policy_model_sets = {t: set(ms) for t, ms in policy.tiers.items()}
    avail_matches_policy = bool(_AVAILABLE) and all(
        set(_AVAILABLE.get(t, [])).issubset(policy_model_sets.get(t, set()))
        for t in policy.tiers
    )
    if avail_matches_policy:
        avail = _AVAILABLE
    else:
        avail = {t: list(ms) for t, ms in policy.tiers.items()}

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


def _parse_classifier_output(out: str) -> ClassifierDecision:
    numbers = re.findall(r"\d+(?:\.\d+)?", out or "")
    score = 3
    confidence = 1.0
    if numbers:
        score = max(1, min(5, int(float(numbers[0]))))
    if len(numbers) >= 2:
        confidence = max(0.0, min(1.0, float(numbers[1])))
    return ClassifierDecision(score=score, confidence=confidence)


def _escalate_tier(tier: str) -> str:
    try:
        idx = _TIER_ORDER.index(tier)
    except ValueError:
        return tier
    if idx + 1 >= len(_TIER_ORDER):
        return tier
    nxt = _TIER_ORDER[idx + 1]
    return nxt


def _classifier_tier(score: int, confidence: float, policy: Policy) -> str:
    tier = policy.classifier.score_to_tier.get(score, "mid")
    confidence_floor = max(0.0, min(1.0, float(policy.classifier.confidence_floor)))
    if confidence < confidence_floor:
        tier = _escalate_tier(tier)
    return tier


def _classify(prompt: str, policy: Policy) -> ClassifierDecision:
    cls = policy.classifier
    msg = cls.prompt.format(prompt=prompt[:4000])
    try:
        resp = litellm.completion(
            model=cls.model,
            messages=[{"role": "user", "content": msg}],
            max_tokens=12,
            temperature=0,
        )
        out = resp["choices"][0]["message"]["content"].strip()
        return _parse_classifier_output(out)
    except Exception:
        log.warning("classifier call failed (model=%s); falling back to mid tier",
                    cls.model, exc_info=True)
        return ClassifierDecision(score=3, confidence=1.0)  # safe middle on classifier failure


def would_have_tier(prompt: str, policy: Policy) -> str | None:
    """Return the classifier-only tier for routing-quality comparison.

    Skips when the classifier is disabled or unavailable so normal routing
    does not manufacture noisy disagreement rows from missing provider keys.
    """
    if not policy.classifier.enabled:
        return None
    if not _provider_available(policy.classifier.model):
        return None
    classified = _classify(prompt, policy)
    return _classifier_tier(classified.score, classified.confidence, policy)


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

    # Classifier fallback (LLM)
    if policy.classifier.enabled:
        classified = _classify(prompt, policy)
        score = classified.score
        tier = _classifier_tier(score, classified.confidence, policy)
        return RouteDecision(tier=tier, model=_pick_model(tier, policy),
                             reason=(
                                 f"classifier:score={score};"
                                 f"confidence={classified.confidence:.2f}"
                             ))

    # Embedding-classifier fallback (Layer 3): when the LLM classifier is
    # disabled, a kNN over the labelled corpus still routes better than a flat
    # default. Opt-in via CLEARVIEW_EMBED_CLASSIFIER=1; no-op otherwise.
    ec = embed_classifier.classify(prompt)
    if ec is not None:
        tier, conf = ec
        if tier in policy.tiers:
            return RouteDecision(tier=tier, model=_pick_model(tier, policy),
                                 reason=f"embed_classifier:tier={tier};confidence={conf:.2f}")

    # Default
    return RouteDecision(tier="cheap", model=_pick_model("cheap", policy),
                         reason="default:cheap")


def embed_would_have_tier(prompt: str, policy: Policy) -> str | None:
    """Embedding-classifier tier for the routing-quality signal. None when the
    embed classifier is disabled or can't score the prompt."""
    ec = embed_classifier.classify(prompt)
    if ec is None:
        return None
    tier, _conf = ec
    return tier if tier in policy.tiers else None
