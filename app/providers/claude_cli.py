"""Claude Code CLI adapter.

Spawns the locally-installed `claude` binary instead of calling the Anthropic
REST API. Useful when the operator has a Claude Pro/Max subscription — the
subscription covers the cost, so per-call spend is $0 ("synth" cost).

Activation:
    CLEARVIEW_USE_CLAUDE_CLI=1   # gate on adapter (default off → no behavior change)
    CLEARVIEW_CLAUDE_BIN=claude  # override binary path / name (PATH lookup)
    CLEARVIEW_CLI_TIMEOUT_SEC=120

Scope:
    - Anthropic models only (`anthropic/<id>`). The adapter strips the prefix.
    - Non-streaming via `completion`/`acompletion`. Streaming via `astream`
      using the CLI's `--output-format stream-json --include-partial-messages`
      flags. Output is NDJSON; we translate each text delta into an
      OpenAI-style chat.completion.chunk.

Return shape mirrors litellm's chat.completion dict closely enough that
`app/main.py` and `app/pricing.py` can treat it uniformly. Two custom keys
(`_clearview_synth_cost_usd`, `_clearview_via`) are stripped before the
client sees the response.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import time
import uuid
from typing import Any, AsyncIterator

log = logging.getLogger("clearview.providers.claude_cli")

_DEFAULT_TIMEOUT = 120.0
_DEFAULT_BIN = "claude"


def _bin() -> str:
    return os.environ.get("CLEARVIEW_CLAUDE_BIN") or _DEFAULT_BIN


def _timeout() -> float:
    try:
        return float(os.environ.get("CLEARVIEW_CLI_TIMEOUT_SEC", str(_DEFAULT_TIMEOUT)))
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT


def is_enabled() -> bool:
    """Adapter only activates when the operator opts in."""
    return os.environ.get("CLEARVIEW_USE_CLAUDE_CLI") == "1"


def is_available_model(model: str) -> bool:
    """CLI adapter only handles Anthropic-prefixed model ids."""
    return isinstance(model, str) and model.startswith("anthropic/")


def _to_cli_model(model: str) -> str:
    """Strip the `anthropic/` prefix; CLI takes bare model ids."""
    if model.startswith("anthropic/"):
        return model[len("anthropic/"):]
    return model


def _flatten(messages: list[dict[str, Any]]) -> str:
    """Flatten messages → single prompt string. System turns prefixed.

    Matches the spirit of `_flatten_prompt` in app/main.py but separates the
    system prompt with a `[system] ... [/system]` block so the CLI receives
    clear role context (it has no native system-prompt channel beyond
    `--append-system-prompt` which we leave empty).
    """
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


def _parse_cli_json(stdout: str) -> dict:
    """Parse the JSON object from CLI stdout.

    Older CLI versions sometimes emit prefatory log lines. Be defensive: try
    the whole buffer first, then walk back from the last line searching for
    a parseable JSON object.
    """
    raw = (stdout or "").strip()
    if not raw:
        raise RuntimeError("claude CLI returned empty stdout")

    # Fast path: whole stdout is one JSON object.
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Walk lines back to front, find the last one that parses.
    for line in reversed(raw.splitlines()):
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue

    # Last resort: find the last `{` and try from there.
    idx = raw.rfind("{")
    if idx >= 0:
        try:
            return json.loads(raw[idx:])
        except json.JSONDecodeError:
            pass

    raise RuntimeError(f"could not parse claude CLI JSON from stdout: {raw[:300]!r}")


def _shape_response(original_model: str, parsed: dict) -> dict:
    """Convert CLI JSON to a litellm-compatible chat.completion dict."""
    if parsed.get("is_error") or parsed.get("subtype") not in (None, "success"):
        msg = parsed.get("result") or parsed.get("subtype") or "claude CLI error"
        raise RuntimeError(f"claude CLI error: {msg}")

    result_text = parsed.get("result", "")
    usage = parsed.get("usage") or {}
    in_tok = int(usage.get("input_tokens", 0) or 0)
    out_tok = int(usage.get("output_tokens", 0) or 0)
    # Cache-related counts are part of input on Anthropic billing; surface them
    # in prompt_tokens for accurate token accounting.
    in_tok += int(usage.get("cache_creation_input_tokens", 0) or 0)
    in_tok += int(usage.get("cache_read_input_tokens", 0) or 0)
    synth_cost = float(parsed.get("total_cost_usd", 0.0) or 0.0)

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": original_model,
        "choices": [{
            "index": 0,
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": result_text},
        }],
        "usage": {
            "prompt_tokens": in_tok,
            "completion_tokens": out_tok,
            "total_tokens": in_tok + out_tok,
        },
        "_clearview_synth_cost_usd": synth_cost,
        "_clearview_via": "claude_cli",
    }


def _build_args(cli_model: str) -> list[str]:
    # Flag combo benchmarked to give the smallest baseline-token overhead while
    # still working on a Pro/Max subscription:
    #   --system-prompt ""                        : replace default Claude Code system prompt
    #   --disable-slash-commands                  : drop skills/agents preamble
    #   --exclude-dynamic-system-prompt-sections  : drop cwd/env/git context blocks
    # Net effect on a "say hi" probe: ~28k → ~2.5k input tokens, latency 14s → ~2s.
    return [
        _bin(),
        "-p",
        "--output-format", "json",
        "--model", cli_model,
        "--system-prompt", "",
        "--disable-slash-commands",
        "--exclude-dynamic-system-prompt-sections",
        "--input-format", "text",
    ]


async def acompletion(model: str, messages: list[dict], **kwargs) -> dict:
    """Async path: spawn `claude` with stdin-fed prompt, await JSON output."""
    if kwargs.get("stream"):
        raise NotImplementedError("CLI adapter does not support streaming")

    cli_model = _to_cli_model(model)
    prompt = _flatten(messages)
    args = _build_args(cli_model)

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
        raise RuntimeError(f"claude CLI timed out after {_timeout()}s") from e

    if proc.returncode != 0:
        log.warning("claude CLI exit=%s stderr=%s", proc.returncode, (stderr_b or b"").decode("utf-8", "replace")[:500])
        raise RuntimeError(
            f"claude CLI exited with code {proc.returncode}: "
            f"{(stderr_b or b'').decode('utf-8', 'replace')[:300]}"
        )

    parsed = _parse_cli_json((stdout_b or b"").decode("utf-8", "replace"))
    return _shape_response(model, parsed)


def completion(model: str, messages: list[dict], **kwargs) -> dict:
    """Sync path used by app/main.py's request handler.

    Uses subprocess.run directly to avoid loop-management complexity. Safe to
    call from inside a FastAPI handler because the dispatcher invokes this via
    `await asyncio.to_thread(...)` upstream — however in v1 we keep it simple:
    callers run in the request thread and a handful of concurrent CLI procs is
    fine.
    """
    if kwargs.get("stream"):
        raise NotImplementedError("CLI adapter does not support streaming")

    cli_model = _to_cli_model(model)
    prompt = _flatten(messages)
    args = _build_args(cli_model)

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
        raise RuntimeError(f"claude CLI timed out after {_timeout()}s") from e
    except FileNotFoundError as e:
        raise RuntimeError(f"claude CLI binary not found: {_bin()}") from e

    if completed.returncode != 0:
        stderr = (completed.stderr or b"").decode("utf-8", "replace")
        log.warning("claude CLI exit=%s stderr=%s", completed.returncode, stderr[:500])
        raise RuntimeError(
            f"claude CLI exited with code {completed.returncode}: {stderr[:300]}"
        )

    parsed = _parse_cli_json((completed.stdout or b"").decode("utf-8", "replace"))
    return _shape_response(model, parsed)


# --- Streaming path ----------------------------------------------------------

def _build_stream_args(cli_model: str) -> list[str]:
    """Args for streaming NDJSON mode.

    CLI requires --verbose when --output-format stream-json is used with -p.
    --include-partial-messages yields incremental text_delta events; without
    it we'd only get whole content blocks at a time.
    """
    return [
        _bin(),
        "-p",
        "--output-format", "stream-json",
        "--include-partial-messages",
        "--verbose",
        "--model", cli_model,
        "--system-prompt", "",
        "--disable-slash-commands",
        "--exclude-dynamic-system-prompt-sections",
        "--input-format", "text",
    ]


def _extract_text_delta(event_obj: dict) -> str | None:
    """Pull a text delta out of a CLI NDJSON line, if any.

    Two shapes carry assistant text:
      1. `stream_event` with event.type=content_block_delta and
         delta.type=text_delta → use delta.text. (Thinking deltas skipped.)
      2. `assistant` envelope whose message.content[] contains text blocks.
         We skip these to avoid double-counting — content_block_delta covers
         the same characters incrementally.
    """
    t = event_obj.get("type")
    if t == "stream_event":
        ev = event_obj.get("event") or {}
        if ev.get("type") == "content_block_delta":
            delta = ev.get("delta") or {}
            if delta.get("type") == "text_delta":
                return delta.get("text") or ""
    return None


async def astream(
    model: str,
    messages: list[dict],
    **kwargs: Any,
) -> AsyncIterator[Any]:
    """Stream chat.completion.chunk dicts from the local Claude CLI.

    Yields OpenAI-style dicts, then a literal `"[DONE]"` sentinel string the
    caller serializes as `data: [DONE]\\n\\n`. On the final `result` event
    we emit a chunk with finish_reason="stop", `usage`, and our custom
    `_clearview_*` fields populated.

    Raises RuntimeError on CLI nonzero exit (with stderr) or timeout.
    """
    cli_model = _to_cli_model(model)
    prompt = _flatten(messages)
    args = _build_stream_args(cli_model)
    chat_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    # Write the prompt and close stdin so the CLI starts producing output.
    try:
        if proc.stdin is not None:
            proc.stdin.write(prompt.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
    except (BrokenPipeError, ConnectionResetError) as e:
        log.warning("claude CLI stdin write failed: %s", e)

    deadline = asyncio.get_event_loop().time() + _timeout()
    final_emitted = False
    saw_any_delta = False

    def _mk_chunk(delta_text: str, finish: str | None,
                  usage: dict | None, synth_cost: float | None) -> dict:
        d: dict = {
            "id": chat_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{
                "index": 0,
                "delta": ({"role": "assistant", "content": delta_text}
                          if delta_text or finish is None
                          else {}),
                "finish_reason": finish,
            }],
        }
        if usage is not None:
            d["usage"] = usage
        if synth_cost is not None:
            d["_clearview_synth_cost_usd"] = synth_cost
            d["_clearview_via"] = "claude_cli"
        return d

    try:
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                raise RuntimeError(f"claude CLI timed out after {_timeout()}s")

            try:
                line_b = await asyncio.wait_for(
                    proc.stdout.readline(), timeout=remaining
                )
            except asyncio.TimeoutError as e:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                raise RuntimeError(f"claude CLI timed out after {_timeout()}s") from e

            if not line_b:
                break  # EOF

            line = line_b.decode("utf-8", "replace").strip()
            if not line:
                continue

            try:
                event_obj = json.loads(line)
            except json.JSONDecodeError as e:
                log.warning("claude CLI: skipping unparseable line: %s (%s)", line[:200], e)
                continue

            etype = event_obj.get("type")
            if etype == "result":
                # Final event: synthesize finish chunk with usage+synth cost.
                usage = event_obj.get("usage") or {}
                in_tok = int(usage.get("input_tokens", 0) or 0)
                out_tok = int(usage.get("output_tokens", 0) or 0)
                in_tok += int(usage.get("cache_creation_input_tokens", 0) or 0)
                in_tok += int(usage.get("cache_read_input_tokens", 0) or 0)
                synth_cost = float(event_obj.get("total_cost_usd", 0.0) or 0.0)
                is_error = bool(event_obj.get("is_error"))
                final_usage = {
                    "prompt_tokens": in_tok,
                    "completion_tokens": out_tok,
                    "total_tokens": in_tok + out_tok,
                }
                # If the CLI never emitted a partial text_delta (rare — e.g.
                # very short replies emitted as a single content_block), fall
                # back to the full `result` text so the stream has content.
                fallback_text = ""
                if not saw_any_delta and not is_error:
                    fallback_text = str(event_obj.get("result") or "")

                final_emitted = True
                yield _mk_chunk(fallback_text, "stop", final_usage, synth_cost)
                if is_error:
                    msg = event_obj.get("result") or "claude CLI error"
                    raise RuntimeError(f"claude CLI error: {msg}")
                continue

            delta_text = _extract_text_delta(event_obj)
            if delta_text:
                saw_any_delta = True
                yield _mk_chunk(delta_text, None, None, None)

        # Subprocess hit EOF — make sure it exited cleanly.
        rc = await proc.wait()
        if rc != 0:
            stderr_b = b""
            try:
                stderr_b = await proc.stderr.read()
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(
                f"claude CLI exited with code {rc}: "
                f"{stderr_b.decode('utf-8', 'replace')[:300]}"
            )

        if not final_emitted:
            # Stream ended without a `result` event — emit a stop chunk so the
            # consumer can finalize telemetry. No usage info to report.
            yield _mk_chunk("", "stop", {"prompt_tokens": 0, "completion_tokens": 0,
                                         "total_tokens": 0}, 0.0)
    finally:
        # Best-effort cleanup if the consumer cancelled mid-stream.
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass

    yield "[DONE]"


# Startup-time availability probe. If the operator opted in but the binary is
# missing, log a warning — individual calls will still raise.
_BIN_RESOLVED = shutil.which(_bin())
if is_enabled() and not _BIN_RESOLVED:
    log.warning(
        "CLEARVIEW_USE_CLAUDE_CLI=1 but binary %r not found on PATH; calls will fail",
        _bin(),
    )
