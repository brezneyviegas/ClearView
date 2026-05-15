"""Gemini CLI adapter (Google AI / Gemini Code Assist subscription bypass).

Spawns the locally-installed `gemini` binary in `-o json` mode so the Google
subscription pays for the call instead of the Gemini REST API.

Activation:
    CLEARVIEW_USE_GEMINI_CLI=1     # gate on adapter (default off)
    CLEARVIEW_GEMINI_BIN=gemini    # override binary
    CLEARVIEW_GEMINI_MODEL=        # optional override of the model id passed via -m
                                     (useful when the sub plan only allows a
                                     specific id and policy.yaml uses another).
                                     Unset → the adapter strips the `gemini/`
                                     or `google/` prefix from the routed model.
    CLEARVIEW_CLI_TIMEOUT_SEC=120

Scope:
    - Handles `gemini/*` and `google/*` model ids only.
    - Non-streaming only — `gemini -p` emits one final JSON object.
    - Pricing: real money spent is $0 (sub). Synth cost is computed by the
      caller via pricing.cost_for(original_model) when the adapter returns
      0 — mirrors the codex_cli convention.

Output shape matches litellm's chat.completion dict so app/main.py treats
this identically to the Claude + Codex CLI paths. Two custom keys are
stripped before the client sees the response:
    _clearview_synth_cost_usd
    _clearview_via=gemini_cli
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

log = logging.getLogger("clearview.providers.gemini_cli")

_DEFAULT_TIMEOUT = 120.0
_DEFAULT_BIN = "gemini"


def _bin() -> str:
    return os.environ.get("CLEARVIEW_GEMINI_BIN") or _DEFAULT_BIN


def _timeout() -> float:
    try:
        return float(os.environ.get("CLEARVIEW_CLI_TIMEOUT_SEC", str(_DEFAULT_TIMEOUT)))
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT


def is_enabled() -> bool:
    return os.environ.get("CLEARVIEW_USE_GEMINI_CLI") == "1"


def is_available_model(model: str) -> bool:
    """Adapter handles any `gemini/*` or `google/*` model id."""
    if not isinstance(model, str):
        return False
    return model.startswith("gemini/") or model.startswith("google/")


def _to_cli_model(model: str) -> str:
    """Strip the provider prefix; CLI takes bare model ids. Operator can
    override entirely via CLEARVIEW_GEMINI_MODEL."""
    override = os.environ.get("CLEARVIEW_GEMINI_MODEL")
    if override:
        return override
    for prefix in ("gemini/", "google/"):
        if model.startswith(prefix):
            return model[len(prefix):]
    return model


def _flatten(messages: list[dict[str, Any]]) -> str:
    """Collapse OpenAI-shape messages into a single prompt blob. System turns
    get a [system] prefix so the model has clear role context — `gemini` CLI
    has no native system-prompt channel beyond prepending text."""
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


def _parse_output(stdout: str) -> dict:
    """Locate and parse the JSON object in `gemini -o json` stdout.

    The CLI sometimes emits leading log lines (e.g. "Ripgrep is not
    available.") before the JSON. Try a whole-buffer parse first, then walk
    forward to the first `{` and try parsing the slice.
    """
    raw = (stdout or "").strip()
    if not raw:
        raise RuntimeError("gemini CLI returned empty stdout")

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    idx = raw.find("{")
    if idx >= 0:
        try:
            return json.loads(raw[idx:])
        except json.JSONDecodeError:
            pass

    raise RuntimeError(f"could not parse gemini CLI JSON: {raw[:300]!r}")


def _extract_usage(parsed: dict) -> dict:
    """Pull token counts out of `stats.models.<first-model>.tokens`.

    The Gemini CLI nests stats under whichever model id the request actually
    used (which can differ from the requested `-m` value if the CLI's router
    decides e.g. `gemini-3.1-flash-lite`). Take the first model entry since
    the adapter is non-streaming + single-turn."""
    stats = (parsed.get("stats") or {}).get("models") or {}
    if not stats:
        return {}
    first = next(iter(stats.values()))
    return (first or {}).get("tokens") or {}


def _shape_response(original_model: str, parsed: dict) -> dict:
    """Wrap parsed CLI output in a litellm-compatible chat.completion dict.

    Token accounting:
        prompt_tokens     = tokens.input (falls back to .prompt)
        completion_tokens = tokens.candidates + tokens.thoughts (reasoning)
    """
    assistant_text = parsed.get("response", "") or ""
    if not isinstance(assistant_text, str):
        assistant_text = json.dumps(assistant_text)

    usage = _extract_usage(parsed)
    in_tok = int(usage.get("input", usage.get("prompt", 0)) or 0)
    out_tok = int(usage.get("candidates", 0) or 0)
    out_tok += int(usage.get("thoughts", 0) or 0)

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
        # Caller fills in synth cost via pricing.cost_for(original_model) when
        # this is zero — matches the codex_cli convention so callers don't
        # need to branch per-adapter.
        "_clearview_synth_cost_usd": 0.0,
        "_clearview_via": "gemini_cli",
    }


def _build_args(model: str) -> list[str]:
    """Build argv for `gemini -p "" -o json -y --skip-trust [-m <model>]`.

    Prompt is piped on stdin (the empty `-p ""` flag tells gemini to append
    stdin as the prompt). `-y` auto-approves any tool actions (headless) and
    `--skip-trust` avoids the workspace-trust prompt.
    """
    cli_model = _to_cli_model(model)
    args = [
        _bin(),
        "-p", "",
        "-o", "json",
        "-y",
        "--skip-trust",
    ]
    if cli_model:
        args += ["-m", cli_model]
    return args


async def acompletion(model: str, messages: list[dict], **kwargs: Any) -> dict:
    if kwargs.get("stream"):
        raise NotImplementedError("gemini CLI adapter does not support streaming")

    prompt = _flatten(messages)
    args = _build_args(model)

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
        raise RuntimeError(f"gemini CLI timed out after {_timeout()}s") from e

    if proc.returncode != 0:
        stderr = (stderr_b or b"").decode("utf-8", "replace")
        log.warning("gemini CLI exit=%s stderr=%s", proc.returncode, stderr[:500])
        raise RuntimeError(
            f"gemini CLI exited with code {proc.returncode}: {stderr[:300]}"
        )

    parsed = _parse_output((stdout_b or b"").decode("utf-8", "replace"))
    return _shape_response(model, parsed)


def completion(model: str, messages: list[dict], **kwargs: Any) -> dict:
    if kwargs.get("stream"):
        raise NotImplementedError("gemini CLI adapter does not support streaming")

    prompt = _flatten(messages)
    args = _build_args(model)

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
        raise RuntimeError(f"gemini CLI timed out after {_timeout()}s") from e
    except FileNotFoundError as e:
        raise RuntimeError(f"gemini CLI binary not found: {_bin()}") from e

    if completed.returncode != 0:
        stderr = (completed.stderr or b"").decode("utf-8", "replace")
        log.warning("gemini CLI exit=%s stderr=%s", completed.returncode, stderr[:500])
        raise RuntimeError(
            f"gemini CLI exited with code {completed.returncode}: {stderr[:300]}"
        )

    parsed = _parse_output((completed.stdout or b"").decode("utf-8", "replace"))
    return _shape_response(model, parsed)
