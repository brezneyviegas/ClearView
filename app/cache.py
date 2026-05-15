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

from . import embeddings as _emb
from .config import db_path

# Table definition. Kept separate from index DDL so we can run column
# migrations BEFORE the indexes that depend on the newly-added columns.
# Upgrading a DB created by a pre-semantic-cache build would otherwise fail
# on the team_id index before the ALTER TABLE could run.
_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS prompt_cache (
    prompt_hash TEXT PRIMARY KEY,
    virtual_model TEXT,
    response_json TEXT NOT NULL,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    picked_model TEXT,
    ts REAL NOT NULL,
    team_id TEXT,
    prompt_text TEXT,
    embedding BLOB
);
"""

_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_prompt_cache_ts ON prompt_cache(ts);
CREATE INDEX IF NOT EXISTS idx_prompt_cache_team_ts ON prompt_cache(team_id, ts DESC);
"""

# Idempotent column adds for upgrades from earlier schema. Match the
# pattern used in telemetry.init_db.
_MIGRATIONS = [
    "ALTER TABLE prompt_cache ADD COLUMN team_id TEXT",
    "ALTER TABLE prompt_cache ADD COLUMN prompt_text TEXT",
    "ALTER TABLE prompt_cache ADD COLUMN embedding BLOB",
]

_DEFAULT_TTL_SEC = 3600.0
_DEFAULT_SEMANTIC_THRESHOLD = 0.95
# Bound the scan window. Scanning every cached row would scale poorly; in
# practice teams rarely have >2k recent unique prompts. Operator override
# via CLEARVIEW_SEMANTIC_SCAN_LIMIT.
_DEFAULT_SEMANTIC_SCAN_LIMIT = 500


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
        # 1. Create the table (no-op if it already exists).
        c.executescript(_TABLE_DDL)
        # 2. Add columns the schema expects but older tables may lack. Must
        #    happen before any index that references those columns.
        for stmt in _MIGRATIONS:
            try:
                c.execute(stmt)
            except sqlite3.OperationalError:
                # Column already exists. Safe to ignore.
                pass
        # 3. Now safe to create indexes that touch the new columns.
        c.executescript(_INDEX_DDL)


def enabled() -> bool:
    return os.environ.get("CLEARVIEW_CACHE_ENABLED", "1") != "0"


def ttl_sec() -> float:
    try:
        return float(os.environ.get("CLEARVIEW_CACHE_TTL_SEC", str(_DEFAULT_TTL_SEC)))
    except (TypeError, ValueError):
        return _DEFAULT_TTL_SEC


def semantic_enabled() -> bool:
    """Semantic cache is on whenever:
       1. CLEARVIEW_CACHE_ENABLED is on (the exact-match cache is on), AND
       2. the embedding backend is not `disabled`, AND
       3. CLEARVIEW_SEMANTIC_CACHE != "0".

    Operator can keep exact-match on while turning semantic off via
    CLEARVIEW_SEMANTIC_CACHE=0.
    """
    if not enabled():
        return False
    if os.environ.get("CLEARVIEW_SEMANTIC_CACHE", "1") == "0":
        return False
    return _emb.is_enabled()


def semantic_threshold() -> float:
    try:
        return float(os.environ.get("CLEARVIEW_SEMANTIC_THRESHOLD",
                                     str(_DEFAULT_SEMANTIC_THRESHOLD)))
    except (TypeError, ValueError):
        return _DEFAULT_SEMANTIC_THRESHOLD


def _scan_limit() -> int:
    try:
        return max(1, int(os.environ.get("CLEARVIEW_SEMANTIC_SCAN_LIMIT",
                                          str(_DEFAULT_SEMANTIC_SCAN_LIMIT))))
    except (TypeError, ValueError):
        return _DEFAULT_SEMANTIC_SCAN_LIMIT


def hash_key(messages: list[dict[str, Any]], virtual_model: str, temperature: float,
             team_id: str | None = None) -> str:
    """Deterministic sha256 over the request fields that affect output.

    Excludes stream/user/metadata — they don't change the model's output.

    `team_id` is folded in so teams can't replay each other's cached prompts.
    Anonymous traffic (no Bearer header) shares a single cache namespace
    (team_id=None) — preserves single-tenant behavior when no teams exist.
    """
    payload = json.dumps(
        {"messages": messages, "model": virtual_model, "temperature": temperature,
         "team": team_id},
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
    *,
    team_id: str | None = None,
    prompt_text: str | None = None,
    embedding: list[float] | None = None,
) -> None:
    """Persist a cache row. `prompt_text` + `embedding` enable later
    semantic lookups; both are best-effort — pass None when unavailable.
    """
    if not enabled():
        return
    # Lazily compute the embedding when the caller has text but no vector.
    # Skip the call when semantic cache is off so we don't burn embedding $$$
    # on operators who only want exact-match.
    if embedding is None and prompt_text and semantic_enabled():
        embedding = _emb.embed(prompt_text)
    blob = _emb.to_blob(embedding) if embedding else b""
    with _conn() as c:
        c.execute(
            """
            INSERT OR REPLACE INTO prompt_cache
                (prompt_hash, virtual_model, response_json, tokens_in, tokens_out,
                 picked_model, ts, team_id, prompt_text, embedding)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (prompt_hash, virtual_model, response_json, tokens_in, tokens_out,
             picked_model, time.time(), team_id, prompt_text, blob),
        )


def semantic_lookup(
    prompt_text: str,
    team_id: str | None = None,
    *,
    threshold: float | None = None,
) -> tuple[dict, float] | None:
    """Cosine-search recent embedded cache rows for the same team. Returns
    (row, similarity) when the best match clears the threshold, else None.

    Same TTL gate as exact-match lookup. Iterates the most-recent rows up
    to CLEARVIEW_SEMANTIC_SCAN_LIMIT to keep the cosine pass O(N) where N
    is small.
    """
    if not semantic_enabled() or not prompt_text:
        return None

    query_vec = _emb.embed(prompt_text)
    if not query_vec:
        return None

    thresh = threshold if threshold is not None else semantic_threshold()
    cutoff = time.time() - ttl_sec()

    with _conn() as c:
        rows = c.execute(
            """
            SELECT * FROM prompt_cache
            WHERE ts >= ?
              AND (team_id IS ? OR team_id = ?)
              AND embedding IS NOT NULL AND length(embedding) > 0
            ORDER BY ts DESC
            LIMIT ?
            """,
            (cutoff, team_id, team_id, _scan_limit()),
        ).fetchall()

    best: tuple[dict, float] | None = None
    for r in rows:
        vec = _emb.from_blob(r["embedding"])
        sim = _emb.cosine(query_vec, vec)
        if best is None or sim > best[1]:
            best = (dict(r), sim)

    if best and best[1] >= thresh:
        return best
    return None


def write_streamed(
    prompt_hash: str,
    virtual_model: str,
    full_text: str,
    tokens_in: int,
    tokens_out: int,
    picked_model: str,
    *,
    team_id: str | None = None,
    prompt_text: str | None = None,
    embedding: list[float] | None = None,
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
        team_id=team_id,
        prompt_text=prompt_text,
        embedding=embedding,
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
