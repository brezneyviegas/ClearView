"""Exact-match prompt cache. SQLite-backed, TTL-bounded.

Identical (messages, model, temperature) tuples within TTL skip upstream entirely.
Disabled when CLEARVIEW_CACHE_ENABLED=0. TTL via CLEARVIEW_CACHE_TTL_SEC (default 3600).

Streaming requests ARE cached as of this revision: `write_streamed` buffers the
concatenated assistant text from a stream and stores it as a single
chat.completion response. On replay, `synthesize_stream_from_cache` emits a
one-chunk SSE stream so `stream:true` clients still see SSE framing.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import Any, AsyncIterator, Iterator

from .config import db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS prompt_cache (
    prompt_hash TEXT PRIMARY KEY,
    virtual_model TEXT,
    response_json TEXT NOT NULL,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    picked_model TEXT,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_prompt_cache_ts ON prompt_cache(ts);
"""

_DEFAULT_TTL_SEC = 3600.0


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(db_path())
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db() -> None:
    with _conn() as c:
        c.executescript(SCHEMA)


def enabled() -> bool:
    return os.environ.get("CLEARVIEW_CACHE_ENABLED", "1") != "0"


def ttl_sec() -> float:
    try:
        return float(os.environ.get("CLEARVIEW_CACHE_TTL_SEC", str(_DEFAULT_TTL_SEC)))
    except (TypeError, ValueError):
        return _DEFAULT_TTL_SEC


def hash_key(messages: list[dict[str, Any]], virtual_model: str, temperature: float) -> str:
    """Deterministic sha256 over the request fields that affect output.

    Excludes stream/user/metadata — they don't change the model's output.
    """
    payload = json.dumps(
        {"messages": messages, "model": virtual_model, "temperature": temperature},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def lookup(prompt_hash: str) -> dict | None:
    """Return cached row dict if present AND within TTL, else None."""
    if not enabled():
        return None
    ttl = ttl_sec()
    cutoff = time.time() - ttl
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM prompt_cache WHERE prompt_hash = ? AND ts >= ?",
            (prompt_hash, cutoff),
        ).fetchone()
    if not row:
        return None
    return dict(row)


def store(
    prompt_hash: str,
    virtual_model: str,
    response_json: str,
    tokens_in: int,
    tokens_out: int,
    picked_model: str,
) -> None:
    if not enabled():
        return
    with _conn() as c:
        c.execute(
            """
            INSERT OR REPLACE INTO prompt_cache
                (prompt_hash, virtual_model, response_json, tokens_in, tokens_out, picked_model, ts)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (prompt_hash, virtual_model, response_json, tokens_in, tokens_out, picked_model, time.time()),
        )


def write_streamed(
    prompt_hash: str,
    virtual_model: str,
    full_text: str,
    tokens_in: int,
    tokens_out: int,
    picked_model: str,
) -> None:
    """Cache the concatenated text of a streamed completion as one chat.completion
    response. Same table / TTL as `store`; the stored shape is non-stream so
    cache replay can serve either streamed or non-streamed callers.
    """
    if not enabled():
        return
    payload = {
        "id": f"chatcmpl-cache-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": picked_model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": full_text},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": int(tokens_in or 0),
            "completion_tokens": int(tokens_out or 0),
            "total_tokens": int((tokens_in or 0) + (tokens_out or 0)),
        },
    }
    store(
        prompt_hash=prompt_hash,
        virtual_model=virtual_model,
        response_json=json.dumps(payload),
        tokens_in=int(tokens_in or 0),
        tokens_out=int(tokens_out or 0),
        picked_model=picked_model,
    )


async def synthesize_stream_from_cache(cached_response: dict) -> AsyncIterator[str]:
    """Emit a one-chunk SSE stream from a cached non-stream chat.completion.

    Known v1 simplification: cache hits replay as a single chunk regardless of
    how long the original response was. SSE consumers that assume many small
    chunks must tolerate one big delta + `[DONE]`.
    """
    try:
        choices = cached_response.get("choices") or []
        content = ""
        if choices:
            msg = (choices[0] or {}).get("message") or {}
            content = msg.get("content") or ""
    except Exception:
        content = ""

    chunk = {
        "id": cached_response.get("id") or f"chatcmpl-cache-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion.chunk",
        "created": cached_response.get("created") or int(time.time()),
        "model": cached_response.get("model") or "cache",
        "choices": [{
            "index": 0,
            "delta": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
    }
    if cached_response.get("usage"):
        chunk["usage"] = cached_response["usage"]
    yield f"data: {json.dumps(chunk)}\n\n"
    yield "data: [DONE]\n\n"
