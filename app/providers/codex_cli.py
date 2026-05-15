"""Codex CLI adapter (ChatGPT Plus/Pro subscription bypass).

Spawns the locally-installed `codex` binary in `exec --json` mode so the
ChatGPT subscription pays for the call instead of the OpenAI REST API.

Activation:
    CLEARVIEW_USE_CODEX_CLI=1     # gate on adapter (default off)
    CLEARVIEW_CODEX_BIN=codex     # override binary
    CLEARVIEW_CODEX_MODEL=gpt-5.5 # override the Codex model id (ChatGPT sub
                                    today only allows a small set; gpt-5.5 is
                                    the working default. gpt-5 / gpt-5-codex
                                    are rejected for ChatGPT-account auth.)
    CLEARVIEW_CLI_TIMEOUT_SEC=120

Scope:
    - Handles `openai/<id>` model ids only. The router still picks the OpenAI
      model from policy.yaml; this adapter re-maps it to the single Codex
      sub-allowed model before invoking the CLI.
    - Non-streaming only (Codex exec --json is event-based; one final
      `item.completed` carries the full assistant message).
    - Pricing: real money spent is $0 (sub). `_clearview_synth_cost_usd` is
      computed by the caller from the usage block using the original
      OpenAI model id, so the explorer surfaces a fair "what API would have
      charged" number.

Output shape matches litellm's chat.completion dict so app/main.py treats
this identically to the Claude CLI path. Two custom keys are stripped before
the client sees the response:
    _clearview_synth_cost_usd
    _clearview_via=codex_cli
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
import uuid
from typing import Any

log = logging.getLogger("clearview.providers.codex_cli")

_DEFAULT_TIMEOUT = 120.0
_DEFAULT_BIN = "codex"
_DEFAULT_CODEX_MODEL = "gpt-5.5"  # only model currently allowed for ChatGPT-account sub.


def _bin() -> str:
    return os.environ.get("CLEARVIEW_CODEX_BIN") or _DEFAULT_BIN


def _timeout() -> float:
    try:
        return float(os.environ.get("CLEARVIEW_CLI_TIMEOUT_SEC", str(_DEFAULT_TIMEOUT)))
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT


def _codex_model() -> str:
    return os.environ.get("CLEARVIEW_CODEX_MODEL") or _DEFAULT_CODEX_MODEL


def is_enabled() -> bool:
    return os.environ.get("CLEARVIEW_USE_CODEX_CLI") == "1"


def is_available_model(model: str) -> bool:
    """Adapter handles any `openai/*` model id. Caller is responsible for
    routing — anything OpenAI-shaped will be remapped to the Codex sub model.
    """
    return isinstance(model, str) and model.startswith("openai/")


def _flatten(messages: list[dict[str, Any]]) -> str:
    """Collapse OpenAI-shape messages into a single prompt blob. System turns
    prefixed with a [system] block so the model has clear role context."""
    sys_parts: list[str] = []
    convo_parts: list[str] = []
    for m in messages:
        role = m.get("role", "user")
        c = m.get("content", "")
        if isinstance(c, list):
            c = " ".join(p.get("text", "") for p in c if isinstance(p, dict))
        c = str(c)
        if role == "system":
            sys_parts.append(c)
        else:
            convo_parts.append(f"{role}: {c}")
    out = ""
    if sys_parts:
        out += "[system]\n" + "\n\n".join(sys_parts) + "\n\n"
    out += "\n".join(convo_parts)
    return out


def _parse_events(stdout: str) -> tuple[str, dict, str | None]:
    """Parse Codex NDJSON event stream → (assistant_text, usage, error_msg).

    Event shapes we care about:
      {"type":"item.completed","item":{"type":"agent_message","text":"..."}}
      {"type":"turn.completed","usage":{"input_tokens":N,"cached_input_tokens":N,
                                       "output_tokens":N,"reasoning_output_tokens":N}}
      {"type":"error","message":"..."}
      {"type":"turn.failed","error":{"message":"..."}}
    """
    assistant_text = ""
    usage: dict = {}
    error_msg: str | None = None

    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue

        t = ev.get("type")
        if t == "item.completed":
            item = ev.get("item") or {}
            if item.get("type") == "agent_message":
                # Multiple item.completed events can fire; concatenate.
                assistant_text += (item.get("text") or "")
        elif t == "turn.completed":
            u = ev.get("usage") or {}
            if u:
                usage = u
        elif t == "error":
            error_msg = ev.get("message") or "codex CLI error"
        elif t == "turn.failed":
            err = ev.get("error") or {}
            error_msg = err.get("message") or error_msg or "codex CLI turn failed"

    return assistant_text, usage, error_msg


def _shape_response(original_model: str, assistant_text: str, usage: dict) -> dict:
    """Wrap parsed CLI output in a litellm-compatible chat.completion dict.

    Token accounting: Codex reports cached_input_tokens separately. Roll all
    input categories into prompt_tokens. reasoning_output_tokens roll into
    completion_tokens (they're billed as output).
    """
    in_tok = int(usage.get("input_tokens", 0) or 0)
    in_tok += int(usage.get("cached_input_tokens", 0) or 0)
    out_tok = int(usage.get("output_tokens", 0) or 0)
    out_tok += int(usage.get("reasoning_output_tokens", 0) or 0)

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": original_model,
        "choices": [{
            "index": 0,
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": assistant_text},
        }],
        "usage": {
            "prompt_tokens": in_tok,
            "completion_tokens": out_tok,
            "total_tokens": in_tok + out_tok,
        },
        # Synth cost is filled in by the caller via pricing.cost_for(original_model)
        # — leaving 0 here keeps the shape uniform with claude_cli.
        "_clearview_synth_cost_usd": 0.0,
        "_clearview_via": "codex_cli",
    }


def _build_args() -> list[str]:
    return [
        _bin(),
        "exec",
        "--json",
        "--skip-git-repo-check",
        "-m", _codex_model(),
    ]


async def acompletion(model: str, messages: list[dict], **kwargs: Any) -> dict:
    if kwargs.get("stream"):
        raise NotImplementedError("codex CLI adapter does not support streaming")

    prompt = _flatten(messages)
    args = _build_args()

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(input=prompt.encode("utf-8")),
            timeout=_timeout(),
        )
    except asyncio.TimeoutError as e:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        raise RuntimeError(f"codex CLI timed out after {_timeout()}s") from e

    if proc.returncode != 0:
        stderr = (stderr_b or b"").decode("utf-8", "replace")
        log.warning("codex CLI exit=%s stderr=%s", proc.returncode, stderr[:500])
        raise RuntimeError(
            f"codex CLI exited with code {proc.returncode}: {stderr[:300]}"
        )

    text, usage, err = _parse_events((stdout_b or b"").decode("utf-8", "replace"))
    if err:
        raise RuntimeError(f"codex CLI error: {err}")
    return _shape_response(model, text, usage)


def completion(model: str, messages: list[dict], **kwargs: Any) -> dict:
    if kwargs.get("stream"):
        raise NotImplementedError("codex CLI adapter does not support streaming")

    prompt = _flatten(messages)
    args = _build_args()

    try:
        completed = subprocess.run(
            args,
            input=prompt.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=_timeout(),
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"codex CLI timed out after {_timeout()}s") from e
    except FileNotFoundError as e:
        raise RuntimeError(f"codex CLI binary not found: {_bin()}") from e

    if completed.returncode != 0:
        stderr = (completed.stderr or b"").decode("utf-8", "replace")
        log.warning("codex CLI exit=%s stderr=%s", completed.returncode, stderr[:500])
        raise RuntimeError(
            f"codex CLI exited with code {completed.returncode}: {stderr[:300]}"
        )

    text, usage, err = _parse_events((completed.stdout or b"").decode("utf-8", "replace"))
    if err:
        raise RuntimeError(f"codex CLI error: {err}")
    return _shape_response(model, text, usage)
