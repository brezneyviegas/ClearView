"""Cost calculation. Wraps litellm cost tables, adds plan-equivalent baseline."""
from __future__ import annotations

import litellm


def cost_for(model: str, tokens_in: int, tokens_out: int) -> float:
    """USD cost for a single call given a model and token counts."""
    # Local models are free.
    if model.startswith("ollama/") or model.startswith("ollama_chat/"):
        return 0.0
    try:
        return float(
            litellm.completion_cost(
                model=model,
                prompt_tokens=tokens_in,
                completion_tokens=tokens_out,
            )
        )
    except Exception:
        # Fallback if model missing from litellm pricing table.
        # Conservative estimate: $5/M in, $15/M out (mid-frontier rate).
        return (tokens_in / 1_000_000) * 5.0 + (tokens_out / 1_000_000) * 15.0


def cost_per_1k_out(native_cost: float, tokens_out: int) -> float:
    if tokens_out <= 0:
        return 0.0
    return native_cost / (tokens_out / 1000.0)


def drift_pct(native: float, plan_equiv: float) -> float:
    """Savings % vs baseline. Positive = saved money."""
    if plan_equiv <= 0:
        return 0.0
    return ((plan_equiv - native) / plan_equiv) * 100.0
