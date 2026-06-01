"""Shared CLI-aware completion dispatch.

Internal LLM calls (the complexity classifier, the shadow judge) must honour the
subscription-CLI adapters too — otherwise they call the REST API directly and
401 in CLI-only mode (no API key), silently degrading routing/judging.

`main._call_upstream` does the same dispatch for the primary request path, but
`router` and `shadow_judge` can't import `main` (circular). This tiny module is
import-safe for all of them.
"""
from __future__ import annotations

from typing import Any

import litellm

from .providers import claude_cli, codex_cli, gemini_cli


def completion(model: str, messages: list[dict], **kwargs: Any) -> dict:
    """Route a non-stream completion through a subscription CLI when one is
    enabled for the model's provider, else litellm. Returns a chat.completion
    dict either way."""
    for adapter in (claude_cli, codex_cli, gemini_cli):
        if adapter.is_enabled() and adapter.is_available_model(model):
            return adapter.completion(model=model, messages=messages)
    return litellm.completion(model=model, messages=messages, **kwargs)
