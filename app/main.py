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

from . import cache, chat as chat_store, teams, telemetry
from .config import Policy, baseline_model_env, load_policy
from .pricing import cost_for, cost_per_1k_out, drift_pct
from .providers import claude_cli, codex_cli, gemini_cli
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
_TICKER_CACHE: dict[tuple[str | None, int, str | None], tuple[float, dict]] = {}
_TICKER_CACHE_TTL = 2.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    global POLICY
    POLICY = load_policy()
    telemetry.init_db()
    teams.init_db()
    cache.init_db()
    chat_store.init_db()
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
    if (not stream) and codex_cli.is_enabled() and codex_cli.is_available_model(model):
        try:
            return codex_cli.completion(
                model=model,
                messages=forward_kwargs["messages"],
            )
        except NotImplementedError:
            pass
    if (not stream) and gemini_cli.is_enabled() and gemini_cli.is_available_model(model):
        try:
            return gemini_cli.completion(
                model=model,
                messages=forward_kwargs["messages"],
            )
        except NotImplementedError:
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
    if codex_cli.is_enabled() and codex_cli.is_available_model(model):
        try:
            return await codex_cli.acompletion(
                model=model,
                messages=forward_kwargs["messages"],
            )
        except NotImplementedError:
            pass
    if gemini_cli.is_enabled() and gemini_cli.is_available_model(model):
        try:
            return await gemini_cli.acompletion(
                model=model,
                messages=forward_kwargs["messages"],
            )
        except NotImplementedError:
            pass
    return await litellm.acompletion(**forward_kwargs)


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


def _resolve_team(request: Request) -> teams.Team | None:
    """Parse Authorization: Bearer cv_team_<hex> and return the matching Team.

    Returns None when the header is absent (anonymous / single-tenant fallback).
    Raises 401 when the header IS present but malformed, not a team token,
    unknown, or the team is disabled. The resolved team is cached on
    request.state.team so admin handlers can reuse without re-querying.
    """
    cached = getattr(request.state, "team", "__unset__")
    if cached != "__unset__":
        return cached  # may be None — already resolved this request.

    auth = request.headers.get("authorization", "")
    token: str | None = None
    if auth:
        if auth.lower().startswith("bearer "):
            cand = auth.split(" ", 1)[1].strip()
            if cand.startswith("cv_team_"):
                token = cand
    if token is None:
        cookie_val = request.cookies.get("cv_session")
        if cookie_val and cookie_val.startswith("cv_team_"):
            token = cookie_val
    if token is None:
        request.state.team = None
        return None
    t = teams.get(token)
    if t is None:
        raise HTTPException(status_code=401, detail="unknown team token")
    if not t.enabled:
        raise HTTPException(status_code=401, detail="team disabled")
    request.state.team = t
    return t


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
    body = await request.json()
    return await _handle_chat_completions(request, body)


async def _handle_chat_completions(request: Request, body: dict[str, Any]) -> Any:
    pol = _policy()
    messages = body.get("messages") or []
    if not messages:
        raise HTTPException(status_code=400, detail="messages required")

    # Team identity (Bearer cv_team_*). None = anonymous (single-tenant fallback).
    team = _resolve_team(request)
    team_id = team.id if team else None

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
                team_id=team_id,
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
                        team_id=team_id,
                    ))
                    if stream:
                        return StreamingResponse(
                            cache.synthesize_stream_from_cache(payload),
                            media_type="text/event-stream",
                        )
                    return JSONResponse(payload)

    # --- Semantic cache lookup ---
    # Falls back to cosine-search across team-scoped embedded entries when
    # exact-match missed. Skipped silently if the embedding backend is
    # disabled or returns no vector. Hits log a separate route_reason so
    # operators can see semantic vs exact contributions.
    if cache.semantic_enabled():
        flat_for_lookup = _flatten_prompt(messages)
        sem = None
        try:
            sem = cache.semantic_lookup(flat_for_lookup, team_id=team_id)
        except Exception as e:  # noqa: BLE001
            log.warning("semantic cache lookup failed: %s", e)
        if sem is not None:
            cached_row, similarity = sem
            started_cache = time.perf_counter()
            try:
                payload = json.loads(cached_row["response_json"])
            except Exception:
                payload = None
            if payload is not None:
                tokens_in = int(cached_row.get("tokens_in") or 0)
                tokens_out = int(cached_row.get("tokens_out") or 0)
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
                    route_reason=f"semantic_cache_hit:sim={similarity:.3f}",
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    native_cost_usd=0.0,
                    plan_equiv_cost_usd=plan_equiv,
                    drift_pct=drift_pct(0.0, plan_equiv),
                    output_cost_per_1k=0.0,
                    latency_ms=latency_ms,
                    prompt_hash=_hash_prompt_text(flat_for_lookup),
                    team_id=team_id,
                ))
                if stream:
                    return StreamingResponse(
                        cache.synthesize_stream_from_cache(payload),
                        media_type="text/event-stream",
                    )
                return JSONResponse(payload)

    # --- Budget enforcement (per-team daily → per-team monthly → global daily) ---
    # First breach wins. Team caps always reject. Global cap still honors
    # policy.budget.on_breach (reject/warn/allow).
    budget_warn_scope: str | None = None
    if team is not None:
        if team.daily_usd_cap is not None and team.daily_usd_cap > 0:
            spent_t = teams.today_spend(team.id)
            cap_t = float(team.daily_usd_cap)
            if spent_t >= cap_t:
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": "team daily budget exceeded",
                        "spent": round(spent_t, 4),
                        "cap": round(cap_t, 4),
                        "scope": "team_daily",
                    },
                )
        if team.monthly_usd_cap is not None and team.monthly_usd_cap > 0:
            spent_m = teams.month_spend(team.id)
            cap_m = float(team.monthly_usd_cap)
            if spent_m >= cap_m:
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": "team monthly budget exceeded",
                        "spent": round(spent_m, 4),
                        "cap": round(cap_m, 4),
                        "scope": "team_monthly",
                    },
                )

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
                        "scope": "global_daily",
                    },
                )
            if mode == "warn":
                budget_warn_scope = "global_daily"
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

    # --- Team tier-gating ---
    # If the team declared an `allowed_tiers` whitelist, refuse routes that
    # resolve to a tier outside the list. Fires AFTER routing so the operator
    # sees which tier the prompt would have run on (helps explain refusals).
    if team is not None and team.allowed_tiers:
        if decision.tier not in team.allowed_tiers:
            return JSONResponse(
                status_code=403,
                content={
                    "error": "tier not allowed for team",
                    "tier": decision.tier,
                    "allowed": list(team.allowed_tiers),
                },
            )

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
                             request_id=request_id, team_id=team_id)
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
                             request_id=request_id, team_id=team_id)
                raise HTTPException(status_code=502, detail=f"upstream error: {e2}") from e2
            # If the original request was streaming, the escalated reply is a
            # plain chat.completion dict — finalize it as non-stream below.
            stream = False
            use_cli_stream = False
        else:
            _log_failure(session_id, client_id, requested, decision, prompt_text, str(e),
                         request_id=request_id, team_id=team_id)
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
                team_id=team_id,
            ))
        # Budget-warn header: scope name (e.g. "global_daily") if a breach
        # was tripped in warn mode; absent otherwise. Always include the
        # request_id + routed model so /chat/send_stream (and any other
        # consumer) can correlate telemetry after the stream finishes.
        stream_headers: dict[str, str] = {
            "x-clearview-request-id": request_id,
            "x-clearview-tier": used_tier or "",
            "x-clearview-model": used_model or "",
        }
        if budget_warn_scope:
            stream_headers["x-clearview-budget-warn"] = f"{budget_warn_scope}:true"
        return StreamingResponse(
            _stream_and_log(resp, decision, session_id, client_id, requested,
                            prompt_text, started, escalated, empty_escalated, used_model,
                            used_tier,
                            request_id=request_id,
                            cache_hash=cache_hash,
                            via_cli_stream=use_cli_stream,
                            team_id=team_id),
            media_type="text/event-stream",
            headers=stream_headers,
        )

    response, primary_request_id, primary_model = _finalize_non_stream(
        resp, decision, session_id, client_id, requested,
        prompt_text, started, escalated, empty_escalated, used_model,
        used_tier,
        cache_hash=cache_hash,
        request_id=request_id,
        team_id=team_id,
    )
    if budget_warn_scope:
        response.headers["x-clearview-budget-warn"] = f"{budget_warn_scope}:true"

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
            team_id=team_id,
        ))

    return response


def _response_payload(resp: Any) -> dict:
    if isinstance(resp, JSONResponse):
        try:
            return json.loads(resp.body.decode("utf-8"))
        except Exception:
            return {}
    if isinstance(resp, dict):
        return resp
    return _resp_to_dict(resp)


def _chat_text(payload: dict) -> str:
    try:
        choices = payload.get("choices") or []
        msg = (choices[0] or {}).get("message") or {}
        content = msg.get("content") or ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                p.get("text", "") for p in content
                if isinstance(p, dict) and isinstance(p.get("text"), str)
            )
    except Exception:
        pass
    return ""


def _usage(payload: dict) -> dict:
    usage = payload.get("usage") or {}
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    return usage if isinstance(usage, dict) else {}


def _text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif block.get("type") == "tool_result":
                tool_content = block.get("content", "")
                parts.append(_text_from_content(tool_content))
        return "\n".join(p for p in parts if p)
    return str(content) if content is not None else ""


def _anthropic_to_chat(body: dict[str, Any]) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    system = body.get("system")
    if system:
        messages.append({"role": "system", "content": _text_from_content(system)})
    for msg in body.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "user")
        if role == "assistant":
            out_role = "assistant"
        else:
            out_role = "user"
        messages.append({"role": out_role, "content": _text_from_content(msg.get("content", ""))})

    out: dict[str, Any] = {
        "model": body.get("model") or "clearview-auto",
        "messages": messages,
        "stream": False,
    }
    if "max_tokens" in body:
        out["max_tokens"] = body["max_tokens"]
    if "temperature" in body:
        out["temperature"] = body["temperature"]
    return out


def _anthropic_payload(chat_payload: dict, requested_model: str | None = None) -> dict:
    text = _chat_text(chat_payload)
    usage = _usage(chat_payload)
    return {
        "id": chat_payload.get("id") or f"msg_{uuid.uuid4().hex}",
        "type": "message",
        "role": "assistant",
        "model": requested_model or chat_payload.get("model") or "clearview-auto",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens", 0) or 0),
            "output_tokens": int(usage.get("completion_tokens", 0) or 0),
        },
    }


async def _anthropic_stream(payload: dict):
    text = _text_from_content(payload.get("content") or [])
    usage = payload.get("usage") or {}
    start = {
        "type": "message_start",
        "message": {
            **payload,
            "content": [],
            "usage": {
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": 0,
            },
        },
    }
    yield f"event: message_start\ndata: {json.dumps(start)}\n\n"
    content_start = {
        "type": "content_block_start",
        "index": 0,
        "content_block": {"type": "text", "text": ""},
    }
    yield f"event: content_block_start\ndata: {json.dumps(content_start)}\n\n"
    delta = {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": text},
    }
    yield f"event: content_block_delta\ndata: {json.dumps(delta)}\n\n"
    yield "event: content_block_stop\ndata: {\"type\":\"content_block_stop\",\"index\":0}\n\n"
    stop = {
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
        "usage": {"output_tokens": usage.get("output_tokens", 0)},
    }
    yield f"event: message_delta\ndata: {json.dumps(stop)}\n\n"
    yield "event: message_stop\ndata: {\"type\":\"message_stop\"}\n\n"


@app.post("/v1/messages")
async def anthropic_messages(request: Request) -> Any:
    """Anthropic Messages compatibility shim.

    This translates basic Claude-style message requests into ClearView's
    OpenAI-compatible router. Tool-use parity is intentionally not implemented
    yet; normal text prompt/response calls work and still get telemetry.
    """
    body = await request.json()
    wants_stream = bool(body.get("stream"))
    chat_body = _anthropic_to_chat(body)
    resp = await _handle_chat_completions(request, chat_body)
    if isinstance(resp, JSONResponse) and resp.status_code >= 400:
        return resp
    payload = _anthropic_payload(_response_payload(resp), requested_model=body.get("model"))
    if wants_stream:
        return StreamingResponse(_anthropic_stream(payload), media_type="text/event-stream")
    return JSONResponse(payload)


def _responses_to_chat(body: dict[str, Any]) -> dict[str, Any]:
    inp = body.get("input", "")
    messages: list[dict[str, Any]] = []
    if isinstance(inp, str):
        messages.append({"role": "user", "content": inp})
    elif isinstance(inp, list):
        for item in inp:
            if isinstance(item, dict) and item.get("type") == "message":
                content = item.get("content", "")
                messages.append({
                    "role": item.get("role", "user"),
                    "content": _text_from_content(content),
                })
            elif isinstance(item, dict) and isinstance(item.get("content"), str):
                messages.append({
                    "role": item.get("role", "user"),
                    "content": item["content"],
                })
    out: dict[str, Any] = {
        "model": body.get("model") or "clearview-auto",
        "messages": messages,
        "stream": False,
    }
    if "max_output_tokens" in body:
        out["max_tokens"] = body["max_output_tokens"]
    if "temperature" in body:
        out["temperature"] = body["temperature"]
    return out


def _responses_payload(chat_payload: dict, requested_model: str | None = None) -> dict:
    text = _chat_text(chat_payload)
    usage = _usage(chat_payload)
    response_id = chat_payload.get("id") or f"resp_{uuid.uuid4().hex}"
    return {
        "id": response_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "model": requested_model or chat_payload.get("model") or "clearview-auto",
        "output": [{
            "id": f"msg_{uuid.uuid4().hex}",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        }],
        "output_text": text,
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens", 0) or 0),
            "output_tokens": int(usage.get("completion_tokens", 0) or 0),
            "total_tokens": int(usage.get("total_tokens", 0) or 0),
        },
    }


async def _responses_stream(payload: dict):
    text = payload.get("output_text", "")
    created = {"type": "response.created", "response": {**payload, "output": [], "output_text": ""}}
    yield f"event: response.created\ndata: {json.dumps(created)}\n\n"
    delta = {"type": "response.output_text.delta", "delta": text}
    yield f"event: response.output_text.delta\ndata: {json.dumps(delta)}\n\n"
    completed = {"type": "response.completed", "response": payload}
    yield f"event: response.completed\ndata: {json.dumps(completed)}\n\n"


@app.post("/v1/responses")
async def openai_responses(request: Request) -> Any:
    """Minimal OpenAI Responses compatibility shim for clients that no longer
    use `/v1/chat/completions`.
    """
    body = await request.json()
    wants_stream = bool(body.get("stream"))
    resp = await _handle_chat_completions(request, _responses_to_chat(body))
    if isinstance(resp, JSONResponse) and resp.status_code >= 400:
        return resp
    payload = _responses_payload(_response_payload(resp), requested_model=body.get("model"))
    if wants_stream:
        return StreamingResponse(_responses_stream(payload), media_type="text/event-stream")
    return JSONResponse(payload)


def _gemini_parts_text(parts: Any) -> str:
    if not isinstance(parts, list):
        return ""
    return "\n".join(
        str(p.get("text", "")) for p in parts
        if isinstance(p, dict) and p.get("text") is not None
    )


def _gemini_to_chat(body: dict[str, Any], model_name: str) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    sys_inst = body.get("systemInstruction") or body.get("system_instruction")
    if isinstance(sys_inst, dict):
        sys_text = _gemini_parts_text(sys_inst.get("parts"))
        if sys_text:
            messages.append({"role": "system", "content": sys_text})

    for item in body.get("contents") or []:
        if not isinstance(item, dict):
            continue
        role = "assistant" if item.get("role") == "model" else "user"
        messages.append({"role": role, "content": _gemini_parts_text(item.get("parts"))})

    gen = body.get("generationConfig") or body.get("generation_config") or {}
    out: dict[str, Any] = {
        "model": model_name or "clearview-auto",
        "messages": messages,
        "stream": False,
    }
    if isinstance(gen, dict):
        if "maxOutputTokens" in gen:
            out["max_tokens"] = gen["maxOutputTokens"]
        if "temperature" in gen:
            out["temperature"] = gen["temperature"]
    return out


def _gemini_payload(chat_payload: dict, requested_model: str | None = None) -> dict:
    text = _chat_text(chat_payload)
    usage = _usage(chat_payload)
    prompt = int(usage.get("prompt_tokens", 0) or 0)
    output = int(usage.get("completion_tokens", 0) or 0)
    return {
        "candidates": [{
            "content": {
                "role": "model",
                "parts": [{"text": text}],
            },
            "finishReason": "STOP",
            "index": 0,
        }],
        "usageMetadata": {
            "promptTokenCount": prompt,
            "candidatesTokenCount": output,
            "totalTokenCount": prompt + output,
        },
        "modelVersion": requested_model,
    }


async def _gemini_stream(payload: dict):
    yield f"data: {json.dumps(payload)}\n\n"


async def _handle_gemini_generate(request: Request, model_name: str, stream: bool) -> Any:
    body = await request.json()
    clean_model = model_name.split(":", 1)[0]
    resp = await _handle_chat_completions(request, _gemini_to_chat(body, clean_model))
    if isinstance(resp, JSONResponse) and resp.status_code >= 400:
        return resp
    payload = _gemini_payload(_response_payload(resp), requested_model=clean_model)
    if stream:
        return StreamingResponse(_gemini_stream(payload), media_type="text/event-stream")
    return JSONResponse(payload)


@app.post("/v1beta/models/{model_name}:generateContent")
async def gemini_generate_v1beta(model_name: str, request: Request) -> Any:
    return await _handle_gemini_generate(request, model_name, stream=False)


@app.post("/v1/models/{model_name}:generateContent")
async def gemini_generate_v1(model_name: str, request: Request) -> Any:
    return await _handle_gemini_generate(request, model_name, stream=False)


@app.post("/v1beta/models/{model_name}:streamGenerateContent")
async def gemini_stream_v1beta(model_name: str, request: Request) -> Any:
    return await _handle_gemini_generate(request, model_name, stream=True)


@app.post("/v1/models/{model_name}:streamGenerateContent")
async def gemini_stream_v1(model_name: str, request: Request) -> Any:
    return await _handle_gemini_generate(request, model_name, stream=True)


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
                         request_id: str | None = None,
                         team_id: str | None = None) -> tuple[JSONResponse, str, str]:
    """Persist telemetry, optionally write to prompt cache, return (response, request_id, used_model)."""
    pol = _policy()
    latency_ms = int((time.perf_counter() - started) * 1000)
    usage = resp.get("usage") if isinstance(resp, dict) else getattr(resp, "usage", {}) or {}
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    tokens_in = int((usage or {}).get("prompt_tokens", 0) or 0)
    tokens_out = int((usage or {}).get("completion_tokens", 0) or 0)

    payload = _resp_to_dict(resp)
    via_marker = payload.get("_clearview_via")
    via_cli = via_marker in ("claude_cli", "codex_cli", "gemini_cli")
    if via_cli:
        # Subscription path → no per-call API charge. Either the adapter
        # reported a synth price (claude_cli) or we compute it from the
        # original model + token counts via the litellm cost table (codex_cli).
        native = 0.0
        synth = float(payload.get("_clearview_synth_cost_usd", 0.0) or 0.0)
        if synth == 0.0:
            synth = cost_for(used_model, tokens_in, tokens_out)
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
        team_id=team_id,
    )
    if request_id:
        rec_kwargs["request_id"] = request_id
    rec = telemetry.CallRecord(**rec_kwargs)
    telemetry.record(rec)
    # Drop the team's cached spend so the next request sees this call when
    # checking its daily/monthly cap. Skipping this could let one team race
    # past its cap during the 5s TTL window.
    if team_id:
        teams.invalidate_spend_cache(team_id)

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
                team_id=team_id,
                prompt_text=prompt_text,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("cache store failed: %s", e)

    response = JSONResponse(payload)
    response.headers["x-clearview-request-id"] = rec.request_id
    response.headers["x-clearview-tier"] = used_tier or ""
    response.headers["x-clearview-model"] = used_model or ""
    return response, rec.request_id, used_model


async def _stream_and_log(resp, decision, session_id, client_id, requested,
                          prompt_text, started, escalated, empty_escalated, used_model,
                          used_tier: str,
                          request_id: str | None = None,
                          cache_hash: str | None = None,
                          via_cli_stream: bool = False,
                          team_id: str | None = None):
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
            team_id=team_id,
        )
        if request_id:
            rec_kwargs["request_id"] = request_id
        telemetry.record(telemetry.CallRecord(**rec_kwargs))
        if team_id:
            teams.invalidate_spend_cache(team_id)

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
                    team_id=team_id,
                    prompt_text=prompt_text,
                )
            except Exception as e:  # noqa: BLE001
                log.warning("streamed cache write failed: %s", e)


async def _run_shadow(*, shadow_tier: str, primary_request_id: str | None,
                      primary_model: str, messages: list[dict[str, Any]], body: dict,
                      session_id: str, client_id: str | None, requested: str,
                      prompt_text: str, team_id: str | None = None) -> None:
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
            team_id=team_id,
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
    via_marker = payload.get("_clearview_via")
    via_cli = via_marker in ("claude_cli", "codex_cli", "gemini_cli")
    if via_cli:
        native = 0.0
        synth = float(payload.get("_clearview_synth_cost_usd", 0.0) or 0.0)
        if synth == 0.0:
            synth = cost_for(shadow_model, tokens_in, tokens_out)
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
        team_id=team_id,
    ))
    if team_id:
        teams.invalidate_spend_cache(team_id)


def _log_failure(session_id, client_id, requested, decision, prompt_text, err,
                 request_id: str | None = None, team_id: str | None = None):
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
        team_id=team_id,
    )
    if request_id:
        kw["request_id"] = request_id
    telemetry.record(telemetry.CallRecord(**kw))


def _hash_prompt_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# --- admin views ---

@app.get("/admin/stats")
async def admin_stats(request: Request, session: str | None = None,
                      team: str | None = None) -> dict:
    _admin_auth(request)
    return telemetry.stats(session_id=session, team_id=team)


@app.get("/admin/explorer", response_class=HTMLResponse)
async def admin_explorer(request: Request, session: str | None = None,
                         team: str | None = None):
    _admin_auth(request)
    data = telemetry.stats(session_id=session, team_id=team)
    return TEMPLATES.TemplateResponse(
        request,
        "explorer.html",
        {"data": data, "current_session": session, "current_team": team},
    )


@app.get("/admin/timeseries")
async def admin_timeseries(request: Request, session: str | None = None, window: int = 60,
                           team: str | None = None) -> dict:
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
    if team:
        where += " AND team_id = ?"
        params.append(team)

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
async def admin_calls_detail(request: Request, session: str | None = None,
                             team: str | None = None) -> dict:
    """Same row set as stats(), but with session_id + prompt_hash for the modal detail view."""
    _admin_auth(request)
    import sqlite3 as _sqlite3
    from .config import db_path as _db_path

    clauses: list[str] = []
    params_l: list[Any] = []
    if session:
        clauses.append("session_id = ?")
        params_l.append(session)
    if team:
        clauses.append("team_id = ?")
        params_l.append(team)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params = tuple(params_l)
    c = _sqlite3.connect(_db_path())
    c.row_factory = _sqlite3.Row
    try:
        rows = c.execute(
            f"""
            SELECT request_id, session_id, picked_provider, picked_model, picked_tier,
                   latency_ms, tokens_in, tokens_out,
                   native_cost_usd, plan_equiv_cost_usd, output_cost_per_1k,
                   route_reason, escalated, consensus_flag, shadow_of,
                   prompt_hash, ts, virtual_model, status, team_id
            FROM calls {where}
            ORDER BY ts DESC LIMIT 200
            """,
            params,
        ).fetchall()
    finally:
        c.close()
    return {"rows": [dict(r) for r in rows]}


@app.get("/admin/shadow_compare")
async def admin_shadow_compare(request: Request, session: str | None = None,
                               team: str | None = None) -> dict:
    """Pair primary calls with their shadow counterparts.

    Joined via shadow.shadow_of = primary.request_id. Limited to 50 most
    recent pairs (ordered by shadow ts desc).
    """
    _admin_auth(request)
    import sqlite3 as _sqlite3
    from .config import db_path as _db_path

    clauses: list[str] = []
    params_l: list[Any] = []
    if session:
        clauses.append("s.session_id = ?")
        params_l.append(session)
    if team:
        clauses.append("s.team_id = ?")
        params_l.append(team)
    where = ("AND " + " AND ".join(clauses)) if clauses else ""
    params = tuple(params_l)

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
                       window_sec: int = 300, team: str | None = None) -> dict:
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

    # Process-local 2s cache to soak rapid polling. Keyed on (session, window, team)
    # so an operator pivoting from "all" to "team X" view doesn't see stale rows.
    cache_key = (session, window_sec, team)
    now = time.time()
    cached = _TICKER_CACHE.get(cache_key)
    if cached and (now - cached[0]) < _TICKER_CACHE_TTL:
        return cached[1]

    import sqlite3 as _sqlite3
    from .config import db_path as _db_path

    cur_start = now - window_sec
    prev_start = now - 2 * window_sec
    burn_start = now - 60.0

    where_extras = ""
    params_session: list[Any] = []
    if session:
        where_extras += " AND session_id = ?"
        params_session.append(session)
    if team:
        where_extras += " AND team_id = ?"
        params_session.append(team)
    where_session = where_extras

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


# --- Team admin endpoints ----------------------------------------------------
#
# All gated by CLEARVIEW_ADMIN_TOKEN (when set) via _admin_auth. The team's
# `id` (which IS the Bearer token used by clients) is returned in full ONLY on
# create — listing redacts it to a short prefix to avoid leaking tokens via
# operator logs / shoulder-surfing in the explorer.

def _team_to_full_dict(t: teams.Team) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "daily_usd_cap": t.daily_usd_cap,
        "monthly_usd_cap": t.monthly_usd_cap,
        "allowed_tiers": list(t.allowed_tiers),
        "created_ts": t.created_ts,
        "enabled": t.enabled,
    }


def _team_to_redacted_dict(t: teams.Team) -> dict:
    """List view: never returns the full Bearer token. Show prefix for
    operator-side identification only."""
    return {
        "id_short": (t.id[:18] + "...") if len(t.id) > 18 else t.id,
        "name": t.name,
        "daily_usd_cap": t.daily_usd_cap,
        "monthly_usd_cap": t.monthly_usd_cap,
        "allowed_tiers": list(t.allowed_tiers),
        "created_ts": t.created_ts,
        "enabled": t.enabled,
    }


@app.post("/admin/teams")
async def admin_create_team(request: Request) -> dict:
    _admin_auth(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    name = (body or {}).get("name")
    if not name or not isinstance(name, str):
        raise HTTPException(status_code=400, detail="name required")
    allowed = body.get("allowed_tiers")
    if allowed is not None and not isinstance(allowed, list):
        raise HTTPException(status_code=400, detail="allowed_tiers must be a list")
    try:
        t = teams.create(
            name=name,
            daily_usd_cap=body.get("daily_usd_cap"),
            monthly_usd_cap=body.get("monthly_usd_cap"),
            allowed_tiers=allowed,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _team_to_full_dict(t)


@app.get("/admin/teams")
async def admin_list_teams(request: Request) -> dict:
    _admin_auth(request)
    return {"teams": [_team_to_redacted_dict(t) for t in teams.list_all()]}


@app.patch("/admin/teams/{team_id}")
async def admin_update_team(team_id: str, request: Request) -> dict:
    _admin_auth(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body or {}
    # Distinguish "field absent" from "field set to null" — caller may want to
    # clear a cap by sending {"daily_usd_cap": null}.
    set_daily = "daily_usd_cap" in body
    set_monthly = "monthly_usd_cap" in body
    set_tiers = "allowed_tiers" in body
    enabled_val = body.get("enabled") if "enabled" in body else None
    if "allowed_tiers" in body and body["allowed_tiers"] is not None and \
            not isinstance(body["allowed_tiers"], list):
        raise HTTPException(status_code=400, detail="allowed_tiers must be a list")
    t = teams.update(
        team_id,
        daily_usd_cap=body.get("daily_usd_cap"),
        monthly_usd_cap=body.get("monthly_usd_cap"),
        allowed_tiers=body.get("allowed_tiers"),
        enabled=(bool(enabled_val) if enabled_val is not None else None),
        _set_daily=set_daily,
        _set_monthly=set_monthly,
        _set_tiers=set_tiers,
    )
    if t is None:
        raise HTTPException(status_code=404, detail="team not found")
    return _team_to_full_dict(t)


@app.delete("/admin/teams/{team_id}")
async def admin_delete_team(team_id: str, request: Request) -> dict:
    _admin_auth(request)
    ok = teams.delete(team_id)
    if not ok:
        raise HTTPException(status_code=404, detail="team not found")
    return {"deleted": team_id}


# --- Prometheus metrics ---

def _prom_escape_label(v: str) -> str:
    return str(v).replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n")


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics() -> PlainTextResponse:
    snap = telemetry.metrics_snapshot()
    lines: list[str] = []

    lines.append("# HELP clearview_requests_total Total upstream requests by tier/provider/status/team.")
    lines.append("# TYPE clearview_requests_total counter")
    for (tier, provider, status, team_id), count in sorted(snap["buckets"].items()):
        lbl = (
            f'tier="{_prom_escape_label(tier)}",'
            f'provider="{_prom_escape_label(provider)}",'
            f'status="{_prom_escape_label(status)}",'
            f'team_id="{_prom_escape_label(team_id)}"'
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


# --------------------------------------------------------------------------- #
# /chat — non-technical user surface (cookie auth, persisted conversations)   #
# --------------------------------------------------------------------------- #

_CHAT_TIER_TO_MODEL = {
    "auto": "clearview-auto",
    "cheap": "clearview-cheap",
    "mid": "clearview-mid",
    "frontier": "clearview-frontier",
}


def _require_chat_team(request: Request) -> teams.Team:
    t = _resolve_team(request)
    if t is None:
        raise HTTPException(status_code=401, detail="not_logged_in")
    return t


@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    team = _resolve_team(request)
    return TEMPLATES.TemplateResponse(
        request,
        "chat.html",
        {"logged_in": team is not None, "team_name": team.name if team else ""},
    )


@app.post("/chat/login")
async def chat_login(request: Request) -> JSONResponse:
    body = await request.json()
    token = (body or {}).get("token", "").strip()
    if not token.startswith("cv_team_"):
        raise HTTPException(status_code=400, detail="invalid token format")
    t = teams.get(token)
    if t is None or not t.enabled:
        raise HTTPException(status_code=401, detail="unknown or disabled team")
    resp = JSONResponse({"ok": True, "team_name": t.name})
    # 30-day cookie. HttpOnly so JS can't read it. SameSite=Lax for fetch posts.
    resp.set_cookie(
        "cv_session", token,
        max_age=60 * 60 * 24 * 30,
        httponly=True, samesite="lax", path="/",
    )
    return resp


@app.post("/chat/logout")
async def chat_logout() -> JSONResponse:
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("cv_session", path="/")
    return resp


@app.get("/chat/conversations")
async def chat_list_conversations(request: Request) -> dict:
    team = _require_chat_team(request)
    return {"conversations": chat_store.list_conversations(team.id)}


@app.post("/chat/conversations")
async def chat_create_conversation(request: Request) -> dict:
    team = _require_chat_team(request)
    body = await request.json() if (await request.body()) else {}
    title = (body or {}).get("title") or "New chat"
    conv = chat_store.create_conversation(team.id, title=title)
    return {"id": conv.id, "title": conv.title, "created_ts": conv.created_ts,
            "updated_ts": conv.updated_ts}


@app.get("/chat/conversations/{cid}/messages")
async def chat_get_messages(request: Request, cid: str) -> dict:
    team = _require_chat_team(request)
    if not chat_store.get_conversation(cid, team.id):
        raise HTTPException(status_code=404, detail="conversation not found")
    return {"messages": chat_store.list_messages(cid, team.id)}


@app.delete("/chat/conversations/{cid}")
async def chat_delete_conversation(request: Request, cid: str) -> dict:
    team = _require_chat_team(request)
    ok = chat_store.delete_conversation(cid, team.id)
    if not ok:
        raise HTTPException(status_code=404, detail="conversation not found")
    return {"ok": True}


@app.post("/chat/conversations/{cid}/send")
async def chat_send(request: Request, cid: str) -> JSONResponse:
    team = _require_chat_team(request)
    if not chat_store.get_conversation(cid, team.id):
        raise HTTPException(status_code=404, detail="conversation not found")

    body = await request.json()
    user_text = (body or {}).get("content", "").strip()
    if not user_text:
        raise HTTPException(status_code=400, detail="content required")
    tier_key = ((body or {}).get("tier") or "auto").lower()
    model_id = _CHAT_TIER_TO_MODEL.get(tier_key, "clearview-auto")

    # Persist user message first.
    chat_store.append_message(cid, "user", user_text)

    history = chat_store.messages_for_upstream(cid)
    # Build OpenAI-shape body. Reuse main chat-completions handler so router,
    # cache, quotas, telemetry all kick in identically to API path.
    inner_body = {
        "model": model_id,
        "messages": history,
        "stream": False,
    }
    inner_resp = await _handle_chat_completions(request, inner_body)

    # _handle_chat_completions returns a JSONResponse for non-stream.
    if not isinstance(inner_resp, JSONResponse):
        raise HTTPException(status_code=500, detail="unexpected response type")

    payload_text = inner_resp.body.decode("utf-8", errors="replace")
    payload = json.loads(payload_text)
    assistant_text = ""
    try:
        assistant_text = payload["choices"][0]["message"]["content"] or ""
    except Exception:  # noqa: BLE001
        assistant_text = ""

    rid = inner_resp.headers.get("x-clearview-request-id", "")
    rec = telemetry.get_call(rid) if rid else None
    picked_tier = (rec or {}).get("picked_tier") or inner_resp.headers.get("x-clearview-tier")
    picked_model = (rec or {}).get("picked_model") or inner_resp.headers.get("x-clearview-model")

    chat_store.append_message(
        cid, "assistant", assistant_text,
        request_id=rid or None,
        picked_tier=picked_tier,
        picked_model=picked_model,
        native_cost_usd=float((rec or {}).get("native_cost_usd") or 0.0),
        synth_cost_usd=float((rec or {}).get("synth_cost_usd") or 0.0),
        plan_equiv_cost_usd=float((rec or {}).get("plan_equiv_cost_usd") or 0.0),
        tokens_in=int((rec or {}).get("tokens_in") or 0),
        tokens_out=int((rec or {}).get("tokens_out") or 0),
        latency_ms=int((rec or {}).get("latency_ms") or 0),
    )

    return JSONResponse({
        "content": assistant_text,
        "request_id": rid,
        "picked_tier": picked_tier,
        "picked_model": picked_model,
        "native_cost_usd": float((rec or {}).get("native_cost_usd") or 0.0),
        "synth_cost_usd": float((rec or {}).get("synth_cost_usd") or 0.0),
        "plan_equiv_cost_usd": float((rec or {}).get("plan_equiv_cost_usd") or 0.0),
        "tokens_in": int((rec or {}).get("tokens_in") or 0),
        "tokens_out": int((rec or {}).get("tokens_out") or 0),
        "latency_ms": int((rec or {}).get("latency_ms") or 0),
    })


def _chat_metadata_from_telemetry(rid: str, inner_headers: Any) -> dict:
    """Hydrate per-turn cost data from telemetry by request_id. Falls back
    to response headers when the row isn't readable yet."""
    rec = telemetry.get_call(rid) if rid else None
    return {
        "type": "metadata",
        "request_id": rid,
        "picked_tier": (rec or {}).get("picked_tier") or inner_headers.get("x-clearview-tier"),
        "picked_model": (rec or {}).get("picked_model") or inner_headers.get("x-clearview-model"),
        "native_cost_usd": float((rec or {}).get("native_cost_usd") or 0.0),
        "synth_cost_usd": float((rec or {}).get("synth_cost_usd") or 0.0),
        "plan_equiv_cost_usd": float((rec or {}).get("plan_equiv_cost_usd") or 0.0),
        "tokens_in": int((rec or {}).get("tokens_in") or 0),
        "tokens_out": int((rec or {}).get("tokens_out") or 0),
        "latency_ms": int((rec or {}).get("latency_ms") or 0),
    }


@app.post("/chat/conversations/{cid}/send_stream")
async def chat_send_stream(request: Request, cid: str):
    """SSE-streamed variant of /chat/conversations/{cid}/send.

    Forwards upstream chat.completion.chunk events to the browser as they
    arrive, then emits a custom `{"type":"metadata", ...}` event with the
    per-turn cost numbers before the final `[DONE]`. The assistant message
    is persisted to chat_messages once the stream ends.
    """
    team = _require_chat_team(request)
    if not chat_store.get_conversation(cid, team.id):
        raise HTTPException(status_code=404, detail="conversation not found")

    body = await request.json()
    user_text = (body or {}).get("content", "").strip()
    if not user_text:
        raise HTTPException(status_code=400, detail="content required")
    tier_key = ((body or {}).get("tier") or "auto").lower()
    model_id = _CHAT_TIER_TO_MODEL.get(tier_key, "clearview-auto")

    chat_store.append_message(cid, "user", user_text)

    history = chat_store.messages_for_upstream(cid)
    inner_body = {"model": model_id, "messages": history, "stream": True}
    inner_resp = await _handle_chat_completions(request, inner_body)

    # If exact-match or semantic cache intercepted, the handler returns a
    # StreamingResponse synthesized from the cache (one big chunk + [DONE]).
    # We tap that same stream so the chat send_stream contract stays uniform.
    if not isinstance(inner_resp, StreamingResponse):
        # Defensive: dispatch returned a JSONResponse (e.g. budget reject).
        # Re-emit as a single metadata event so the client doesn't hang.
        async def _gen_err():
            try:
                payload = json.loads(inner_resp.body.decode("utf-8", "replace"))
            except Exception:
                payload = {"error": "non-stream response from upstream"}
            yield (f"data: {json.dumps({'type': 'error', 'payload': payload})}\n\n").encode("utf-8")
            yield b"data: [DONE]\n\n"
        return StreamingResponse(_gen_err(), media_type="text/event-stream",
                                 status_code=inner_resp.status_code or 200)

    rid = inner_resp.headers.get("x-clearview-request-id", "")

    async def gen():
        text_buf: list[str] = []
        try:
            async for chunk_b in inner_resp.body_iterator:
                if isinstance(chunk_b, str):
                    chunk_bytes = chunk_b.encode("utf-8")
                    chunk_str = chunk_b
                else:
                    chunk_bytes = chunk_b
                    chunk_str = chunk_b.decode("utf-8", errors="replace")

                # Accumulate assistant text by parsing each SSE `data: ...`
                # JSON line. Best-effort — non-JSON chunks pass through.
                for line in chunk_str.splitlines():
                    if not line.startswith("data: "):
                        continue
                    payload_text = line[6:].strip()
                    if payload_text == "[DONE]":
                        continue
                    try:
                        evt = json.loads(payload_text)
                    except Exception:
                        continue
                    choices = evt.get("choices") or []
                    if choices:
                        delta = (choices[0] or {}).get("delta") or {}
                        piece = delta.get("content")
                        if isinstance(piece, str) and piece:
                            text_buf.append(piece)

                yield chunk_bytes
        except Exception as e:  # noqa: BLE001
            log.warning("chat send_stream inner iterator failed: %s", e)

        full_text = "".join(text_buf)
        meta = _chat_metadata_from_telemetry(rid, inner_resp.headers)

        try:
            chat_store.append_message(
                cid, "assistant", full_text,
                request_id=rid or None,
                picked_tier=meta.get("picked_tier"),
                picked_model=meta.get("picked_model"),
                native_cost_usd=meta.get("native_cost_usd", 0.0),
                synth_cost_usd=meta.get("synth_cost_usd", 0.0),
                plan_equiv_cost_usd=meta.get("plan_equiv_cost_usd", 0.0),
                tokens_in=meta.get("tokens_in", 0),
                tokens_out=meta.get("tokens_out", 0),
                latency_ms=meta.get("latency_ms", 0),
            )
        except Exception as e:  # noqa: BLE001
            log.warning("chat send_stream persist failed: %s", e)

        yield f"data: {json.dumps(meta)}\n\n".encode("utf-8")
        yield b"data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
