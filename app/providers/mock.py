"""Mock / echo provider — zero-setup fallback.

Lets ClearView serve end-to-end with NO provider configured: no API keys, no
subscription CLIs, no local ollama. Returns a canned, deterministic response at
$0 (native AND synth), so the router, telemetry, explorer, and chat UI all work
out of the box for demos and first-run.

Activation:
    - Explicit:  CLEARVIEW_USE_MOCK=1   → mock/* models are dispatchable.
    - Implicit:  the router falls back to `mock/echo` when no real provider is
      reachable for any tier (see router._pick_model). The mock is ALWAYS a
      valid call target regardless of the env flag, so the app never hard-fails
      for lack of a backend — the flag only controls whether the router will
      *prefer* routing to it.

Handles `mock/*` model ids only. Output matches the litellm chat.completion
dict so app/main treats it like the CLI adapters (native cost 0). Streaming is
synthesised by the caller from the non-stream body when needed.
"""
from __future__ import annotations

import os
import time
import uuid
from typing import Any

MODEL = "mock/echo"


def is_enabled() -> bool:
    """Whether the operator opted the mock in as a *preferred* target. The
    provider is still callable as a last-resort fallback when this is False."""
    return os.environ.get("CLEARVIEW_USE_MOCK") == "1"


def is_available_model(model: str) -> bool:
    return isinstance(model, str) and model.startswith("mock/")


def _flatten(messages: list[dict[str, Any]]) -> str:
    parts = []
    for m in messages:
        c = m.get("content", "")
        if isinstance(c, list):
            c = " ".join(p.get("text", "") for p in c if isinstance(p, dict))
        parts.append(str(c))
    return "\n".join(parts).strip()


def _reply(prompt: str) -> str:
    snippet = prompt.replace("\n", " ").strip()
    if len(snippet) > 200:
        snippet = snippet[:200] + "…"
    return (
        "[ClearView mock provider] No real LLM is configured, so this is a "
        "canned response. Set a provider API key, enable a subscription CLI "
        "(claude/codex/gemini), or run ollama to get live answers.\n\n"
        f"You said: {snippet}"
    )


def _shape(model: str, prompt: str) -> dict:
    text = _reply(prompt)
    # Cheap token approximation (4 chars/token) so telemetry has non-zero counts.
    in_tok = max(1, len(prompt) // 4)
    out_tok = max(1, len(text) // 4)
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": text},
        }],
        "usage": {
            "prompt_tokens": in_tok,
            "completion_tokens": out_tok,
            "total_tokens": in_tok + out_tok,
        },
        "_clearview_synth_cost_usd": 0.0,
        "_clearview_via": "mock",
    }


def completion(model: str, messages: list[dict], **kwargs: Any) -> dict:
    return _shape(model, _flatten(messages))


async def acompletion(model: str, messages: list[dict], **kwargs: Any) -> dict:
    return _shape(model, _flatten(messages))


async def astream(model: str, messages: list[dict], **kwargs: Any):
    """One-chunk async stream (delta + usage), then the literal "[DONE]"
    sentinel — matches the claude_cli.astream contract that _stream_and_log
    consumes via its async branch."""
    full = _shape(model, _flatten(messages))
    text = full["choices"][0]["message"]["content"]
    yield {
        "id": full["id"],
        "object": "chat.completion.chunk",
        "created": full["created"],
        "model": model,
        "choices": [{"index": 0, "delta": {"role": "assistant", "content": text},
                     "finish_reason": "stop"}],
        "usage": full["usage"],
        "_clearview_via": "mock",
    }
    yield "[DONE]"
