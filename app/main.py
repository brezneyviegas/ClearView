"""FastAPI entrypoint. OpenAI-compatible /v1/chat/completions + admin views."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

import litellm
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from . import cache, telemetry
from .config import Policy, baseline_model_env, load_policy
from .pricing import cost_for, cost_per_1k_out, drift_pct
from .providers import claude_cli
from .router import build_availability, route

log = logging.getLogger("clearview.main")

POLICY: Policy | None = None
TEMPLATES = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))

# Tier ladder for upward escalation.
_TIER_ORDER = ["cheap", "mid", "frontier"]

# Static map: provider model id → ticker symbol. Falls back to last `/`-segment
# uppercased + alnum-only + truncated to 8 chars when key missing.
TICKER_SYMBOLS: dict[str, str] = {
    "anthropic/claude-haiku-4-5": "HAIKU45",
    "anthropic/claude-sonnet-4-6": "SON46",
    "anthropic/claude-opus-4-7": "OPUS47",
    "openai/gpt-4o-mini": "4OMINI",
    "openai/gpt-4o": "GPT4O",
    "gemini/gemini-1.5-flash": "FLASH",
    "gemini/gemini-1.5-pro": "GEMPRO",
    "ollama/llama3.2": "LLAMA32",
    "cache": "CACHE",
}


def _symbol(model: str) -> str:
    """Map a provider model id to a short ticker symbol.

    Static dict first; fallback derives from the last `/`-segment, uppercases,
    drops non-alphanumeric, and truncates to 8 chars.
    """
    if not model:
        return "UNKNOWN"
    if model in TICKER_SYMBOLS:
        return TICKER_SYMBOLS[model]
    tail = model.rsplit("/", 1)[-1].upper()
    cleaned = "".join(ch for ch in tail if ch.isalnum())
    return (cleaned or "UNKNOWN")[:8]


# Per-process micro-cache for /admin/ticker. Key: (session, window_sec).
# Value: (cached_at_monotonic, payload). TTL: 2s (per Idea.md spec).
_TICKER_CACHE: dict[tuple[str | None, int], tuple[float, dict]] = {}
_TICKER_CACHE_TTL = 2.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    global POLICY
    POLICY = load_policy()
    telemetry.init_db()
    cache.init_db()
    avail = build_availability(POLICY)
    for tier, models in avail.items():
        if not models:
            log.warning("tier %s has zero available models given current env vars", tier)
    yield


app = FastAPI(title="ClearView", version="0.1.0", lifespan=lifespan)


def _policy() -> Policy:
    assert POLICY is not None
    return POLICY


def _flatten_prompt(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for m in messages:
        c = m.get("content", "")
        if isinstance(c, list):
            c = " ".join(p.get("text", "") for p in c if isinstance(p, dict))
        parts.append(f"{m.get('role', 'user')}: {c}")
    return "\n".join(parts)


def _next_tier(tier: str) -> str | None:
    try:
        idx = _TIER_ORDER.index(tier)
    except ValueError:
        return None
    if idx + 1 >= len(_TIER_ORDER):
        return None
    return _TIER_ORDER[idx + 1]


def _call_upstream(forward_kwargs: dict, stream: bool):
    """Dispatch upstream call. Routes Anthropic models through the local
    Claude CLI when CLEARVIEW_USE_CLAUDE_CLI=1; otherwise litellm.

    Non-stream path. For streaming, callers either use litellm.completion
    directly (returns an iterator) or `claude_cli.astream` for the CLI path —
    see `chat_completions` for the dispatch.
    """
    model = forward_kwargs.get("model", "")
    if (not stream) and claude_cli.is_enabled() and claude_cli.is_available_model(model):
        try:
            return claude_cli.completion(
                model=model,
                messages=forward_kwargs["messages"],
            )
        except NotImplementedError:
            # Defensive — shouldn't trigger because we gate on `not stream`.
            pass
    return litellm.completion(**forward_kwargs)


async def _acall_upstream(forward_kwargs: dict) -> Any:
    """Async non-stream dispatch. Mirrors `_call_upstream` but uses
    `claude_cli.acompletion` (native async) when applicable, else
    `litellm.acompletion` if available, falling back to a thread.

    Streaming has no async-non-stream meaning; shadow + escalation paths
    only need non-stream.
    """
    model = forward_kwargs.get("model", "")
    if claude_cli.is_enabled() and claude_cli.is_available_model(model):
        try:
            return await claude_cli.acompletion(
                model=model,
                messages=forward_kwargs["messages"],
            )
        except NotImplementedError:
            pass
    acompletion = getattr(litellm, "acompletion", None)
    if acompletion is not None:
        return await acompletion(**forward_kwargs)
    return await asyncio.to_thread(litellm.completion, **forward_kwargs)


def _resp_to_dict(resp: Any) -> dict:
    if isinstance(resp, dict):
        return resp
    if hasattr(resp, "model_dump"):
        return resp.model_dump()
    try:
        return dict(resp)
    except Exception:
        return {}


def _is_empty_response(resp: Any) -> bool:
    d = _resp_to_dict(resp)
    choices = d.get("choices") or []
    if not choices:
        return True
    msg = (choices[0] or {}).get("message") or {}
    content = msg.get("content")
    if content is None:
        return True
    if isinstance(content, str) and not content.strip():
        return True
    return False


def _admin_auth(request: Request) -> None:
    """Raise 401 if CLEARVIEW_ADMIN_TOKEN is set and request lacks matching bearer."""
    expected = os.environ.get("CLEARVIEW_ADMIN_TOKEN")
    if not expected:
        return  # dev mode: open
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = auth.split(" ", 1)[1].strip()
    if token != expected:
        raise HTTPException(status_code=401, detail="invalid admin token")


@app.get("/v1/models")
async def list_models() -> dict:
    pol = _policy()
    virtual = [
        {"id": "clearview-auto", "object": "model", "owned_by": "clearview"},
        {"id": "clearview-cheap", "object": "model", "owned_by": "clearview"},
        {"id": "clearview-mid", "object": "model", "owned_by": "clearview"},
        {"id": "clearview-frontier", "object": "model", "owned_by": "clearview"},
    ]
    underlying = [{"id": m, "object": "model", "owned_by": "underlying"}
                  for tier in pol.tiers.values() for m in tier]
    return {"object": "list", "data": virtual + underlying}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Any:
    pol = _policy()
    body = await request.json()
    messages = body.get("messages") or []
    if not messages:
        raise HTTPException(status_code=400, detail="messages required")

    requested = (body.get("model") or "clearview-auto").lower()
    header_tier = request.headers.get("x-clearview-tier")
    session_id = request.headers.get("x-clearview-session", "default")
    client_id = request.headers.get("x-clearview-client")
    stream = bool(body.get("stream"))

    # Pre-allocate request_id so streamed primaries can be referenced by
    # `shadow_of` on a paired shadow row.
    request_id = uuid.uuid4().hex

    # Shadow-routing header. Validated against policy.tiers; invalid → ignored.
    shadow_tier = request.headers.get("x-clearview-shadow")
    if shadow_tier is not None and shadow_tier not in pol.tiers:
        log.warning("ignoring invalid x-clearview-shadow header value: %r", shadow_tier)
        shadow_tier = None

    # --- Exact-match prompt cache (stream or non-stream) ---
    # The on-disk shape is always a non-stream chat.completion. For stream=true
    # requests on a hit, we synthesize a one-chunk SSE replay so the client's
    # SSE consumer stays happy. See cache.synthesize_stream_from_cache.
    cache_hash: str | None = None
    if cache.enabled():
        try:
            cache_hash = cache.hash_key(
                messages=messages,
                virtual_model=requested,
                temperature=float(body.get("temperature", 1.0) or 1.0),
            )
        except Exception:
            cache_hash = None
        if cache_hash:
            cached = cache.lookup(cache_hash)
            if cached:
                started_cache = time.perf_counter()
                try:
                    payload = json.loads(cached["response_json"])
                except Exception:
                    payload = None
                if payload is not None:
                    tokens_in = int(cached.get("tokens_in") or 0)
                    tokens_out = int(cached.get("tokens_out") or 0)
                    baseline = baseline_model_env() or pol.baseline_model
                    plan_equiv = cost_for(baseline, tokens_in, tokens_out)
                    latency_ms = int((time.perf_counter() - started_cache) * 1000)
                    telemetry.record(telemetry.CallRecord(
                        request_id=request_id,
                        session_id=session_id,
                        client_id=client_id,
                        virtual_model=requested,
                        picked_provider="cache",
                        picked_model="cache",
                        picked_tier="cache",
                        route_reason="cache_hit",
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                        native_cost_usd=0.0,
                        plan_equiv_cost_usd=plan_equiv,
                        drift_pct=drift_pct(0.0, plan_equiv),
                        output_cost_per_1k=0.0,
                        latency_ms=latency_ms,
                        prompt_hash=_hash_prompt_text(_flatten_prompt(messages)),
                    ))
                    if stream:
                        return StreamingResponse(
                            cache.synthesize_stream_from_cache(payload),
                            media_type="text/event-stream",
                        )
                    return JSONResponse(payload)

    # --- Budget enforcement ---
    budget_warn = False
    if pol.budget and pol.budget.daily_usd_cap > 0:
        spent = telemetry.today_spend()
        cap = float(pol.budget.daily_usd_cap)
        if spent >= cap:
            mode = (pol.budget.on_breach or "reject").lower()
            if mode == "reject":
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": "daily budget exceeded",
                        "spent": round(spent, 4),
                        "cap": round(cap, 4),
                    },
                )
            if mode == "warn":
                budget_warn = True
                log.warning("daily budget breach: spent=%.4f cap=%.4f (mode=warn)", spent, cap)
            # mode == "allow" -> no-op

    # Tier override via virtual model name (clearview-cheap etc).
    forced_tier: str | None = None
    if requested.startswith("clearview-") and requested != "clearview-auto":
        forced_tier = requested.split("-", 1)[1]
        if forced_tier not in pol.tiers:
            forced_tier = None

    prompt_text = _flatten_prompt(messages)
    if forced_tier:
        from .router import RouteDecision, _pick_model  # local import to keep main slim
        decision = RouteDecision(
            tier=forced_tier,
            model=_pick_model(forced_tier, pol),
            reason=f"virtual_model:{requested}",
        )
    else:
        decision = route(prompt_text, pol, header_tier=header_tier)

    # Strip ClearView-only fields before forwarding
    forward_kwargs = {k: v for k, v in body.items() if k != "model"}
    forward_kwargs["model"] = decision.model
    forward_kwargs["stream"] = stream

    # When streaming, ask provider to emit usage in final chunk.
    if stream:
        existing_opts = forward_kwargs.get("stream_options") or {}
        if isinstance(existing_opts, dict):
            existing_opts = {**existing_opts, "include_usage": True}
        else:
            existing_opts = {"include_usage": True}
        forward_kwargs["stream_options"] = existing_opts

    started = time.perf_counter()
    escalated = False
    used_tier = decision.tier

    # Streaming + CLI path: claude_cli.astream returns a native async iterator
    # of OpenAI-style chunk dicts (terminated by literal "[DONE]"). For
    # everything else, fall back to litellm's sync iterator via _call_upstream.
    use_cli_stream = (
        stream
        and claude_cli.is_enabled()
        and claude_cli.is_available_model(forward_kwargs["model"])
    )

    try:
        if use_cli_stream:
            resp = claude_cli.astream(
                model=forward_kwargs["model"],
                messages=messages,
            )
        else:
            resp = _call_upstream(forward_kwargs, stream)
    except Exception as e:
        if pol.escalation.on_error and decision.tier != "frontier":
            # Use availability-filtered frontier list so we don't try a model
            # whose provider key isn't set. Walk up tiers if frontier is empty.
            from .router import _AVAILABLE
            chosen = None
            for t in ("frontier", "mid", "cheap"):
                models = _AVAILABLE.get(t) or []
                if models and t != decision.tier:
                    chosen = (t, models[0])
                    break
            if chosen is None:
                _log_failure(session_id, client_id, requested, decision, prompt_text, str(e),
                             request_id=request_id)
                raise HTTPException(
                    status_code=502,
                    detail=f"upstream error and no escalation target available: {e}",
                ) from e
            escalated = True
            used_tier, forward_kwargs["model"] = chosen
            # Escalation re-issues the full call; always non-stream so we have a
            # complete response to inspect. Routes through _call_upstream so the
            # CLI handles Anthropic models when sub mode is on.
            esc_kwargs = {k: v for k, v in forward_kwargs.items() if k != "stream"}
            esc_kwargs["stream"] = False
            try:
                resp = _call_upstream(esc_kwargs, stream=False)
            except Exception as e2:
                _log_failure(session_id, client_id, requested, decision, prompt_text, str(e2),
                             request_id=request_id)
                raise HTTPException(status_code=502, detail=f"upstream error: {e2}") from e2
            # If the original request was streaming, the escalated reply is a
            # plain chat.completion dict — finalize it as non-stream below.
            stream = False
            use_cli_stream = False
        else:
            _log_failure(session_id, client_id, requested, decision, prompt_text, str(e),
                         request_id=request_id)
            raise HTTPException(status_code=502, detail=f"upstream error: {e}") from e

    used_model = forward_kwargs["model"]
    empty_escalated = False

    # --- Empty-response escalation (non-stream only) ---
    if not stream and pol.escalation.on_empty_response and _is_empty_response(resp):
        max_retries = max(0, int(pol.escalation.max_retries or 0))
        retries_left = max_retries
        cur_tier = used_tier
        while retries_left > 0:
            nxt = _next_tier(cur_tier)
            if not nxt:
                break
            from .router import _pick_model
            forward_kwargs["model"] = _pick_model(nxt, pol)
            try:
                resp_retry = _call_upstream(forward_kwargs, stream)
            except Exception as e:
                log.warning("empty-response retry to %s failed: %s", nxt, e)
                break
            empty_escalated = True
            used_model = forward_kwargs["model"]
            cur_tier = nxt
            used_tier = nxt
            resp = resp_retry
            retries_left -= 1
            if not _is_empty_response(resp):
                break

    if stream:
        # Streaming primary: fire shadow as non-stream linked to this request_id.
        if shadow_tier:
            asyncio.create_task(_run_shadow(
                shadow_tier=shadow_tier,
                primary_request_id=request_id,
                primary_model=used_model,
                messages=messages,
                body=body,
                session_id=session_id,
                client_id=client_id,
                requested=requested,
                prompt_text=prompt_text,
            ))
        return StreamingResponse(
            _stream_and_log(resp, decision, session_id, client_id, requested,
                            prompt_text, started, escalated, empty_escalated, used_model,
                            used_tier,
                            request_id=request_id,
                            cache_hash=cache_hash,
                            via_cli_stream=use_cli_stream),
            media_type="text/event-stream",
            headers={"x-clearview-budget-warn": "true"} if budget_warn else None,
        )

    response, primary_request_id, primary_model = _finalize_non_stream(
        resp, decision, session_id, client_id, requested,
        prompt_text, started, escalated, empty_escalated, used_model,
        used_tier,
        cache_hash=cache_hash,
        request_id=request_id,
    )
    if budget_warn:
        response.headers["x-clearview-budget-warn"] = "true"

    # Fire-and-forget shadow call. Client already has its response — zero latency impact.
    if shadow_tier:
        asyncio.create_task(_run_shadow(
            shadow_tier=shadow_tier,
            primary_request_id=primary_request_id,
            primary_model=primary_model,
            messages=messages,
            body=body,
            session_id=session_id,
            client_id=client_id,
            requested=requested,
            prompt_text=prompt_text,
        ))

    return response


def _build_route_reason(decision, escalated: bool, empty_escalated: bool) -> str:
    reason = decision.reason
    if escalated:
        reason += ";escalated"
    if empty_escalated:
        reason += ";empty_escalated"
    return reason


def _finalize_non_stream(resp: Any, decision, session_id, client_id, requested,
                         prompt_text, started, escalated, empty_escalated, used_model,
                         used_tier: str,
                         cache_hash: str | None = None,
                         request_id: str | None = None) -> tuple[JSONResponse, str, str]:
    """Persist telemetry, optionally write to prompt cache, return (response, request_id, used_model)."""
    pol = _policy()
    latency_ms = int((time.perf_counter() - started) * 1000)
    usage = resp.get("usage") if isinstance(resp, dict) else getattr(resp, "usage", {}) or {}
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    tokens_in = int((usage or {}).get("prompt_tokens", 0) or 0)
    tokens_out = int((usage or {}).get("completion_tokens", 0) or 0)

    payload = _resp_to_dict(resp)
    via_cli = payload.get("_clearview_via") == "claude_cli"
    if via_cli:
        # Subscription path → no per-call API charge. Stash the notional API
        # price the CLI reports as "synth_cost_usd" so we can compute savings.
        native = 0.0
        synth = float(payload.get("_clearview_synth_cost_usd", 0.0) or 0.0)
    else:
        native = cost_for(used_model, tokens_in, tokens_out)
        synth = 0.0
    baseline = baseline_model_env() or pol.baseline_model
    plan_equiv = cost_for(baseline, tokens_in, tokens_out)

    rec_kwargs = dict(
        session_id=session_id,
        client_id=client_id,
        virtual_model=requested,
        picked_provider=used_model.split("/", 1)[0] if "/" in used_model else "unknown",
        picked_model=used_model,
        picked_tier=used_tier,
        route_reason=_build_route_reason(decision, escalated, empty_escalated),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        native_cost_usd=native,
        plan_equiv_cost_usd=plan_equiv,
        drift_pct=drift_pct(native, plan_equiv),
        output_cost_per_1k=cost_per_1k_out(native, tokens_out),
        latency_ms=latency_ms,
        escalated=escalated or empty_escalated,
        prompt_hash=_hash_prompt_text(prompt_text),
        synth_cost_usd=synth,
    )
    if request_id:
        rec_kwargs["request_id"] = request_id
    rec = telemetry.CallRecord(**rec_kwargs)
    telemetry.record(rec)

    # Strip ClearView-internal fields before returning to the client / caching.
    for k in [k for k in payload.keys() if isinstance(k, str) and k.startswith("_clearview_")]:
        payload.pop(k, None)

    # Write-through cache (best-effort). Skip on streaming (handled by caller).
    if cache_hash and cache.enabled():
        try:
            cache.store(
                prompt_hash=cache_hash,
                virtual_model=requested,
                response_json=json.dumps(payload),
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                picked_model=used_model,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("cache store failed: %s", e)

    return JSONResponse(payload), rec.request_id, used_model


async def _stream_and_log(resp, decision, session_id, client_id, requested,
                          prompt_text, started, escalated, empty_escalated, used_model,
                          used_tier: str,
                          request_id: str | None = None,
                          cache_hash: str | None = None,
                          via_cli_stream: bool = False):
    """SSE-pump the upstream stream, accumulate text + usage, write telemetry,
    write the buffered text to the prompt cache on a successful single-call
    stream (i.e. no escalation), and emit a trailing `data: [DONE]\\n\\n`.

    `resp` is either a sync iterator (litellm) or an async iterator (the CLI
    adapter's `astream`). We branch on `via_cli_stream`.
    """
    pol = _policy()
    final_usage: dict[str, Any] = {}
    synth_cost: float = 0.0
    via_cli_payload = False
    streamed_text_parts: list[str] = []

    def _accumulate_text(d: dict) -> None:
        for ch in d.get("choices") or []:
            delta = (ch or {}).get("delta") or {}
            txt = delta.get("content")
            if isinstance(txt, str) and txt:
                streamed_text_parts.append(txt)

    try:
        if via_cli_stream:
            async for chunk in resp:
                if chunk == "[DONE]":
                    # CLI adapter signals end with literal sentinel; consume it
                    # so the loop terminates and we emit our own [DONE] below.
                    break
                d = chunk if isinstance(chunk, dict) else (
                    chunk.model_dump() if hasattr(chunk, "model_dump") else dict(chunk)
                )
                if d.get("usage"):
                    final_usage = d["usage"]
                if d.get("_clearview_via") == "claude_cli":
                    via_cli_payload = True
                    synth_cost = float(d.get("_clearview_synth_cost_usd", 0.0) or 0.0)
                _accumulate_text(d)
                # Strip internal-only fields before they reach the wire.
                out = {k: v for k, v in d.items()
                       if not (isinstance(k, str) and k.startswith("_clearview_"))}
                yield f"data: {json.dumps(out)}\n\n"
        else:
            for chunk in resp:
                d = chunk if isinstance(chunk, dict) else (
                    chunk.model_dump() if hasattr(chunk, "model_dump") else dict(chunk)
                )
                if d.get("usage"):
                    final_usage = d["usage"]
                _accumulate_text(d)
                yield f"data: {json.dumps(d)}\n\n"
        yield "data: [DONE]\n\n"
    finally:
        latency_ms = int((time.perf_counter() - started) * 1000)
        tokens_in = int((final_usage or {}).get("prompt_tokens", 0) or 0)
        tokens_out = int((final_usage or {}).get("completion_tokens", 0) or 0)
        if via_cli_payload:
            native = 0.0
            synth = synth_cost
        else:
            native = cost_for(used_model, tokens_in, tokens_out)
            synth = 0.0
        baseline = baseline_model_env() or pol.baseline_model
        plan_equiv = cost_for(baseline, tokens_in, tokens_out)

        rec_kwargs = dict(
            session_id=session_id,
            client_id=client_id,
            virtual_model=requested,
            picked_provider=used_model.split("/", 1)[0] if "/" in used_model else "unknown",
            picked_model=used_model,
            picked_tier=used_tier,
            route_reason=_build_route_reason(decision, escalated, empty_escalated),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            native_cost_usd=native,
            plan_equiv_cost_usd=plan_equiv,
            drift_pct=drift_pct(native, plan_equiv),
            output_cost_per_1k=cost_per_1k_out(native, tokens_out),
            latency_ms=latency_ms,
            escalated=escalated or empty_escalated,
            prompt_hash=_hash_prompt_text(prompt_text),
            synth_cost_usd=synth,
        )
        if request_id:
            rec_kwargs["request_id"] = request_id
        telemetry.record(telemetry.CallRecord(**rec_kwargs))

        # Buffered streaming cache: only write when this was a single-call
        # primary (no escalation) AND we have some text. The cached entry
        # replays as one big chunk; see cache.synthesize_stream_from_cache.
        if (
            cache_hash
            and cache.enabled()
            and not (escalated or empty_escalated)
            and used_model != "cache"
            and streamed_text_parts
        ):
            full_text = "".join(streamed_text_parts)
            try:
                cache.write_streamed(
                    prompt_hash=cache_hash,
                    virtual_model=requested,
                    full_text=full_text,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    picked_model=used_model,
                )
            except Exception as e:  # noqa: BLE001
                log.warning("streamed cache write failed: %s", e)


async def _run_shadow(*, shadow_tier: str, primary_request_id: str | None,
                      primary_model: str, messages: list[dict[str, Any]], body: dict,
                      session_id: str, client_id: str | None, requested: str,
                      prompt_text: str) -> None:
    """Fire a shadow upstream call for offline cost+quality comparison.

    Runs after the client already has the primary response. Errors are swallowed
    and logged as a failure record so they don't disrupt the primary path.
    """
    pol = _policy()
    from .router import _pick_model  # local import to keep module slim
    try:
        shadow_model = _pick_model(shadow_tier, pol)
    except Exception as e:  # noqa: BLE001
        log.warning("shadow: _pick_model failed for tier %s: %s", shadow_tier, e)
        return

    forward_kwargs = {k: v for k, v in body.items() if k not in ("model", "stream")}
    forward_kwargs["model"] = shadow_model
    forward_kwargs["messages"] = messages

    started = time.perf_counter()
    try:
        # Async dispatch — routes Anthropic models through claude_cli when sub
        # mode is on, otherwise litellm.acompletion (or threaded sync fallback).
        resp = await _acall_upstream(forward_kwargs)
    except Exception as e:  # noqa: BLE001
        log.warning("shadow upstream failed (%s): %s", shadow_model, e)
        telemetry.record(telemetry.CallRecord(
            session_id=session_id,
            client_id=client_id,
            virtual_model=requested,
            picked_provider=shadow_model.split("/", 1)[0] if "/" in shadow_model else "unknown",
            picked_model=shadow_model,
            picked_tier=shadow_tier,
            route_reason=f"shadow:{primary_model}",
            status=f"error:{str(e)[:120]}",
            consensus_flag=True,
            shadow_of=primary_request_id,
            prompt_hash=_hash_prompt_text(prompt_text),
        ))
        return

    latency_ms = int((time.perf_counter() - started) * 1000)
    usage = resp.get("usage") if isinstance(resp, dict) else getattr(resp, "usage", {}) or {}
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    tokens_in = int((usage or {}).get("prompt_tokens", 0) or 0)
    tokens_out = int((usage or {}).get("completion_tokens", 0) or 0)

    # Mirror the primary path's CLI cost handling: subscription calls report
    # synth cost (notional API price) but $0 native.
    payload = _resp_to_dict(resp)
    via_cli = payload.get("_clearview_via") == "claude_cli"
    if via_cli:
        native = 0.0
        synth = float(payload.get("_clearview_synth_cost_usd", 0.0) or 0.0)
    else:
        native = cost_for(shadow_model, tokens_in, tokens_out)
        synth = 0.0
    baseline = baseline_model_env() or pol.baseline_model
    plan_equiv = cost_for(baseline, tokens_in, tokens_out)

    telemetry.record(telemetry.CallRecord(
        session_id=session_id,
        client_id=client_id,
        virtual_model=requested,
        picked_provider=shadow_model.split("/", 1)[0] if "/" in shadow_model else "unknown",
        picked_model=shadow_model,
        picked_tier=shadow_tier,
        route_reason=f"shadow:{primary_model}",
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        native_cost_usd=native,
        plan_equiv_cost_usd=plan_equiv,
        drift_pct=drift_pct(native, plan_equiv),
        output_cost_per_1k=cost_per_1k_out(native, tokens_out),
        latency_ms=latency_ms,
        consensus_flag=True,
        shadow_of=primary_request_id,
        prompt_hash=_hash_prompt_text(prompt_text),
        synth_cost_usd=synth,
    ))


def _log_failure(session_id, client_id, requested, decision, prompt_text, err,
                 request_id: str | None = None):
    kw = dict(
        session_id=session_id,
        client_id=client_id,
        virtual_model=requested,
        picked_model=decision.model,
        picked_provider=decision.model.split("/", 1)[0] if "/" in decision.model else "unknown",
        picked_tier=decision.tier,
        route_reason=decision.reason,
        status=f"error:{err[:120]}",
        prompt_hash=_hash_prompt_text(prompt_text),
    )
    if request_id:
        kw["request_id"] = request_id
    telemetry.record(telemetry.CallRecord(**kw))


def _hash_prompt_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# --- admin views ---

@app.get("/admin/stats")
async def admin_stats(request: Request, session: str | None = None) -> dict:
    _admin_auth(request)
    return telemetry.stats(session_id=session)


@app.get("/admin/explorer", response_class=HTMLResponse)
async def admin_explorer(request: Request, session: str | None = None):
    _admin_auth(request)
    data = telemetry.stats(session_id=session)
    return TEMPLATES.TemplateResponse(
        request,
        "explorer.html",
        {"data": data, "current_session": session},
    )


@app.get("/admin/timeseries")
async def admin_timeseries(request: Request, session: str | None = None, window: int = 60) -> dict:
    """Bucket calls by minute over the last `window` minutes for the explorer sparklines."""
    _admin_auth(request)
    import sqlite3 as _sqlite3
    from .config import db_path as _db_path

    window = max(1, min(int(window or 60), 24 * 60))
    now = time.time()
    start = now - window * 60

    where = "WHERE ts >= ?"
    params: list[Any] = [start]
    if session:
        where += " AND session_id = ?"
        params.append(session)

    c = _sqlite3.connect(_db_path())
    c.row_factory = _sqlite3.Row
    try:
        rows = c.execute(
            f"""
            SELECT ts, native_cost_usd, plan_equiv_cost_usd, drift_pct
            FROM calls {where}
            ORDER BY ts ASC
            """,
            tuple(params),
        ).fetchall()
    finally:
        c.close()

    start_bucket = int(start // 60)
    end_bucket = int(now // 60)
    n_buckets = end_bucket - start_bucket + 1

    native = [0.0] * n_buckets
    plan_equiv = [0.0] * n_buckets
    calls_per_min = [0] * n_buckets
    drift_sum = [0.0] * n_buckets
    drift_cnt = [0] * n_buckets

    for r in rows:
        idx = int(r["ts"] // 60) - start_bucket
        if 0 <= idx < n_buckets:
            native[idx] += float(r["native_cost_usd"] or 0.0)
            plan_equiv[idx] += float(r["plan_equiv_cost_usd"] or 0.0)
            calls_per_min[idx] += 1
            drift_sum[idx] += float(r["drift_pct"] or 0.0)
            drift_cnt[idx] += 1

    drift = [round(drift_sum[i] / drift_cnt[i], 2) if drift_cnt[i] else 0.0 for i in range(n_buckets)]

    labels: list[str] = []
    for i in range(n_buckets):
        secs = (start_bucket + i) * 60
        labels.append(time.strftime("%H:%M", time.localtime(secs)))

    return {
        "labels": labels,
        "native": [round(x, 6) for x in native],
        "plan_equiv": [round(x, 6) for x in plan_equiv],
        "calls": calls_per_min,
        "drift": drift,
    }


@app.get("/admin/calls_detail")
async def admin_calls_detail(request: Request, session: str | None = None) -> dict:
    """Same row set as stats(), but with session_id + prompt_hash for the modal detail view."""
    _admin_auth(request)
    import sqlite3 as _sqlite3
    from .config import db_path as _db_path

    where = "WHERE session_id = ?" if session else ""
    params: tuple = (session,) if session else ()
    c = _sqlite3.connect(_db_path())
    c.row_factory = _sqlite3.Row
    try:
        rows = c.execute(
            f"""
            SELECT request_id, session_id, picked_provider, picked_model, picked_tier,
                   latency_ms, tokens_in, tokens_out,
                   native_cost_usd, plan_equiv_cost_usd, output_cost_per_1k,
                   route_reason, escalated, consensus_flag, shadow_of,
                   prompt_hash, ts, virtual_model, status
            FROM calls {where}
            ORDER BY ts DESC LIMIT 200
            """,
            params,
        ).fetchall()
    finally:
        c.close()
    return {"rows": [dict(r) for r in rows]}


@app.get("/admin/shadow_compare")
async def admin_shadow_compare(request: Request, session: str | None = None) -> dict:
    """Pair primary calls with their shadow counterparts.

    Joined via shadow.shadow_of = primary.request_id. Limited to 50 most
    recent pairs (ordered by shadow ts desc).
    """
    _admin_auth(request)
    import sqlite3 as _sqlite3
    from .config import db_path as _db_path

    where = "AND s.session_id = ?" if session else ""
    params: tuple = (session,) if session else ()

    c = _sqlite3.connect(_db_path())
    c.row_factory = _sqlite3.Row
    try:
        rows = c.execute(
            f"""
            SELECT
                p.request_id AS p_request_id, p.session_id AS p_session_id,
                p.picked_provider AS p_provider, p.picked_model AS p_model,
                p.tokens_in AS p_tokens_in, p.tokens_out AS p_tokens_out,
                p.native_cost_usd AS p_native, p.plan_equiv_cost_usd AS p_plan_equiv,
                p.latency_ms AS p_latency_ms, p.route_reason AS p_route_reason, p.ts AS p_ts,
                s.request_id AS s_request_id, s.session_id AS s_session_id,
                s.picked_provider AS s_provider, s.picked_model AS s_model,
                s.tokens_in AS s_tokens_in, s.tokens_out AS s_tokens_out,
                s.native_cost_usd AS s_native, s.plan_equiv_cost_usd AS s_plan_equiv,
                s.latency_ms AS s_latency_ms, s.route_reason AS s_route_reason, s.ts AS s_ts
            FROM calls s
            JOIN calls p ON p.request_id = s.shadow_of
            WHERE s.shadow_of IS NOT NULL {where}
            ORDER BY s.ts DESC
            LIMIT 50
            """,
            params,
        ).fetchall()
    finally:
        c.close()

    pairs = []
    for r in rows:
        primary = {
            "request_id": r["p_request_id"], "session_id": r["p_session_id"],
            "picked_provider": r["p_provider"], "picked_model": r["p_model"],
            "tokens_in": r["p_tokens_in"], "tokens_out": r["p_tokens_out"],
            "native_cost_usd": r["p_native"], "plan_equiv_cost_usd": r["p_plan_equiv"],
            "latency_ms": r["p_latency_ms"], "route_reason": r["p_route_reason"], "ts": r["p_ts"],
        }
        shadow = {
            "request_id": r["s_request_id"], "session_id": r["s_session_id"],
            "picked_provider": r["s_provider"], "picked_model": r["s_model"],
            "tokens_in": r["s_tokens_in"], "tokens_out": r["s_tokens_out"],
            "native_cost_usd": r["s_native"], "plan_equiv_cost_usd": r["s_plan_equiv"],
            "latency_ms": r["s_latency_ms"], "route_reason": r["s_route_reason"], "ts": r["s_ts"],
        }
        pairs.append({
            "primary": primary,
            "shadow": shadow,
            "native_diff_usd": round(float(shadow["native_cost_usd"] or 0.0)
                                     - float(primary["native_cost_usd"] or 0.0), 6),
            "latency_diff_ms": int((shadow["latency_ms"] or 0) - (primary["latency_ms"] or 0)),
        })
    return {"pairs": pairs}


@app.get("/admin/ticker")
async def admin_ticker(request: Request, session: str | None = None,
                       window_sec: int = 300) -> dict:
    """Bloomberg-style live cost view. See .claude/Idea.md "Cost Ticker".

    Shape:
      tape: [{symbol, model, price_per_1k_out, delta_pct, direction,
              calls_window, spend_window_usd}, ...]
      burn_rate: {native_per_min_usd, plan_equiv_per_min_usd, savings_per_min_usd}
      candles: {model: [{ts, open, high, low, close, n}, ...]}  # top-5 active
      leaderboard: {burners: [...], savers: [...]}
      market_open: bool
      last_trade_ts: int
    """
    _admin_auth(request)
    window_sec = max(1, min(int(window_sec or 300), 24 * 3600))

    # Process-local 2s cache to soak rapid polling.
    cache_key = (session, window_sec)
    now = time.time()
    cached = _TICKER_CACHE.get(cache_key)
    if cached and (now - cached[0]) < _TICKER_CACHE_TTL:
        return cached[1]

    import sqlite3 as _sqlite3
    from .config import db_path as _db_path

    cur_start = now - window_sec
    prev_start = now - 2 * window_sec
    burn_start = now - 60.0

    where_session = " AND session_id = ?" if session else ""
    params_session: list[Any] = [session] if session else []

    c = _sqlite3.connect(_db_path())
    c.row_factory = _sqlite3.Row
    try:
        # Pull all rows in the prior+current window once. Window count is
        # bounded (default 600s) so this stays cheap even at high QPS.
        rows = c.execute(
            f"""
            SELECT ts, picked_model, picked_tier, tokens_in, tokens_out,
                   native_cost_usd, plan_equiv_cost_usd, synth_cost_usd,
                   output_cost_per_1k
            FROM calls
            WHERE ts >= ?{where_session}
            ORDER BY ts ASC
            """,
            tuple([prev_start] + params_session),
        ).fetchall()
    finally:
        c.close()

    def _row_price(r) -> float:
        """Effective output price per 1k tokens for this row.

        Subscription rows (synth>0, native==0) and cache rows have
        output_cost_per_1k=0 — fall back to synth_cost_usd / (tokens_out/1000)
        so the ticker still shows a notional price.
        """
        tier = r["picked_tier"]
        synth = float(r["synth_cost_usd"] or 0.0)
        tokens_out = int(r["tokens_out"] or 0)
        if (tier == "cache" or synth > 0) and tokens_out > 0:
            return synth / (tokens_out / 1000.0)
        return float(r["output_cost_per_1k"] or 0.0)

    def _row_spend(r) -> float:
        """Native + synth combined; subscription mode has native=0, baseline
        has synth=0 — sum handles both."""
        return float(r["native_cost_usd"] or 0.0) + float(r["synth_cost_usd"] or 0.0)

    cur_rows = [r for r in rows if r["ts"] >= cur_start]
    prev_rows = [r for r in rows if r["ts"] < cur_start]

    # --- Tape: per-model aggregates over current window + prior delta ---
    cur_by_model: dict[str, list] = {}
    for r in cur_rows:
        cur_by_model.setdefault(r["picked_model"] or "unknown", []).append(r)
    prev_by_model: dict[str, list] = {}
    for r in prev_rows:
        prev_by_model.setdefault(r["picked_model"] or "unknown", []).append(r)

    tape = []
    for model, rs in cur_by_model.items():
        prices = [_row_price(r) for r in rs]
        avg_price = sum(prices) / len(prices) if prices else 0.0
        prev_rs = prev_by_model.get(model, [])
        prev_prices = [_row_price(r) for r in prev_rs]
        prev_avg = sum(prev_prices) / len(prev_prices) if prev_prices else 0.0

        if prev_avg > 0:
            delta_pct = ((avg_price - prev_avg) / prev_avg) * 100.0
        else:
            delta_pct = 0.0
        if abs(delta_pct) < 0.5:
            direction = "flat"
        elif delta_pct > 0:
            direction = "up"
        else:
            direction = "down"

        tape.append({
            "symbol": _symbol(model),
            "model": model,
            "price_per_1k_out": round(avg_price, 6),
            "delta_pct": round(delta_pct, 2),
            "direction": direction,
            "calls_window": len(rs),
            "spend_window_usd": round(sum(_row_spend(r) for r in rs), 6),
        })

    # --- Burn rate over last 60s ---
    burn_rows = [r for r in cur_rows if r["ts"] >= burn_start]
    native_60 = sum(float(r["native_cost_usd"] or 0.0) for r in burn_rows)
    plan_60 = sum(float(r["plan_equiv_cost_usd"] or 0.0) for r in burn_rows)
    synth_60 = sum(float(r["synth_cost_usd"] or 0.0) for r in burn_rows)
    native_per_min = native_60  # already per-60s window
    plan_per_min = plan_60
    synth_per_min = synth_60
    savings_per_min = plan_per_min - native_per_min - synth_per_min

    burn_rate = {
        "native_per_min_usd": round(native_per_min, 6),
        "plan_equiv_per_min_usd": round(plan_per_min, 6),
        "savings_per_min_usd": round(savings_per_min, 6),
    }

    # --- Candles: per-model 1-min OHLC, top-5 most active ---
    top_models = sorted(cur_by_model.keys(),
                        key=lambda m: len(cur_by_model[m]), reverse=True)[:5]
    candles: dict[str, list[dict]] = {}
    for model in top_models:
        buckets: dict[int, list[dict]] = {}
        for r in cur_by_model[model]:
            bucket = int(r["ts"] // 60) * 60
            buckets.setdefault(bucket, []).append(r)
        out = []
        for bucket_ts in sorted(buckets.keys()):
            brs = buckets[bucket_ts]
            prices = [_row_price(r) for r in brs]
            if not prices:
                continue
            out.append({
                "ts": int(bucket_ts),
                "open": round(prices[0], 6),
                "close": round(prices[-1], 6),
                "high": round(max(prices), 6),
                "low": round(min(prices), 6),
                "n": len(brs),
            })
        candles[model] = out

    # --- Leaderboard ---
    burners_acc: dict[str, dict] = {}
    savers_acc: dict[str, dict] = {}
    for model, rs in cur_by_model.items():
        spend = sum(_row_spend(r) for r in rs)
        plan = sum(float(r["plan_equiv_cost_usd"] or 0.0) for r in rs)
        savings = plan - spend
        burners_acc[model] = {"symbol": _symbol(model), "spend_usd": round(spend, 6),
                              "calls": len(rs)}
        savers_acc[model] = {"symbol": _symbol(model),
                             "saved_vs_baseline_usd": round(savings, 6),
                             "calls": len(rs)}

    burners = sorted(burners_acc.values(), key=lambda x: x["spend_usd"],
                     reverse=True)[:5]
    savers = [s for s in savers_acc.values() if s["saved_vs_baseline_usd"] > 0]
    savers = sorted(savers, key=lambda x: x["saved_vs_baseline_usd"],
                    reverse=True)[:5]

    market_open = any(r["ts"] >= burn_start for r in cur_rows)
    last_ts_vals = [r["ts"] for r in cur_rows]
    last_trade_ts = int(max(last_ts_vals)) if last_ts_vals else 0

    payload = {
        "tape": tape,
        "burn_rate": burn_rate,
        "candles": candles,
        "leaderboard": {"burners": burners, "savers": savers},
        "market_open": market_open,
        "last_trade_ts": last_trade_ts,
    }
    _TICKER_CACHE[cache_key] = (now, payload)
    return payload


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# --- Prometheus metrics ---

def _prom_escape_label(v: str) -> str:
    return str(v).replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n")


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics() -> PlainTextResponse:
    snap = telemetry.metrics_snapshot()
    lines: list[str] = []

    lines.append("# HELP clearview_requests_total Total upstream requests by tier/provider/status.")
    lines.append("# TYPE clearview_requests_total counter")
    for (tier, provider, status), count in sorted(snap["buckets"].items()):
        lbl = (
            f'tier="{_prom_escape_label(tier)}",'
            f'provider="{_prom_escape_label(provider)}",'
            f'status="{_prom_escape_label(status)}"'
        )
        lines.append(f"clearview_requests_total{{{lbl}}} {count}")

    lines.append("# HELP clearview_native_cost_usd_total Sum of native cost in USD.")
    lines.append("# TYPE clearview_native_cost_usd_total counter")
    lines.append(f"clearview_native_cost_usd_total {snap['native_total']:.6f}")

    lines.append("# HELP clearview_plan_equiv_cost_usd_total Sum of plan-equivalent baseline cost in USD.")
    lines.append("# TYPE clearview_plan_equiv_cost_usd_total counter")
    lines.append(f"clearview_plan_equiv_cost_usd_total {snap['plan_equiv_total']:.6f}")

    lines.append("# HELP clearview_tokens_out_total Total output tokens served.")
    lines.append("# TYPE clearview_tokens_out_total counter")
    lines.append(f"clearview_tokens_out_total {snap['tokens_out_total']}")

    lines.append("# HELP clearview_drift_pct Savings percentage vs baseline.")
    lines.append("# TYPE clearview_drift_pct gauge")
    lines.append(f"clearview_drift_pct {snap['drift_pct']:.4f}")

    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
