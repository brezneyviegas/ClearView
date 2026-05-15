"""Conversation + message persistence for the /chat UI.

Two tables, both keyed by team_id so multi-tenant isolation matches the rest of
ClearView. Reuses telemetry's `_conn` so migrations stay one-file simple.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Any

from .telemetry import _conn


_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_conversations (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    title TEXT NOT NULL,
    created_ts REAL NOT NULL,
    updated_ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_conv_team ON chat_conversations(team_id, updated_ts DESC);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    ts REAL NOT NULL,
    request_id TEXT,
    picked_tier TEXT,
    picked_model TEXT,
    native_cost_usd REAL NOT NULL DEFAULT 0,
    synth_cost_usd REAL NOT NULL DEFAULT 0,
    plan_equiv_cost_usd REAL NOT NULL DEFAULT 0,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (conversation_id) REFERENCES chat_conversations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chat_msg_conv ON chat_messages(conversation_id, ts);
"""


def init_db() -> None:
    with _conn() as c:
        c.executescript(_SCHEMA)


@dataclass
class Conversation:
    id: str
    team_id: str
    title: str
    created_ts: float
    updated_ts: float


def create_conversation(team_id: str, title: str = "New chat") -> Conversation:
    cid = "cv_chat_" + secrets.token_hex(8)
    now = time.time()
    with _conn() as c:
        c.execute(
            "INSERT INTO chat_conversations (id, team_id, title, created_ts, updated_ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (cid, team_id, title.strip() or "New chat", now, now),
        )
    return Conversation(id=cid, team_id=team_id, title=title, created_ts=now, updated_ts=now)


def list_conversations(team_id: str, limit: int = 50) -> list[dict[str, Any]]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id, title, created_ts, updated_ts FROM chat_conversations "
            "WHERE team_id = ? ORDER BY updated_ts DESC LIMIT ?",
            (team_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_conversation(conversation_id: str, team_id: str) -> dict[str, Any] | None:
    with _conn() as c:
        row = c.execute(
            "SELECT id, team_id, title, created_ts, updated_ts FROM chat_conversations "
            "WHERE id = ? AND team_id = ?",
            (conversation_id, team_id),
        ).fetchone()
    return dict(row) if row else None


def rename_conversation(conversation_id: str, team_id: str, title: str) -> bool:
    title = (title or "").strip()
    if not title:
        return False
    with _conn() as c:
        cur = c.execute(
            "UPDATE chat_conversations SET title = ?, updated_ts = ? "
            "WHERE id = ? AND team_id = ?",
            (title[:200], time.time(), conversation_id, team_id),
        )
    return cur.rowcount > 0


def delete_conversation(conversation_id: str, team_id: str) -> bool:
    with _conn() as c:
        cur = c.execute(
            "DELETE FROM chat_conversations WHERE id = ? AND team_id = ?",
            (conversation_id, team_id),
        )
        c.execute(
            "DELETE FROM chat_messages WHERE conversation_id = ?",
            (conversation_id,),
        )
    return cur.rowcount > 0


def append_message(
    conversation_id: str,
    role: str,
    content: str,
    *,
    request_id: str | None = None,
    picked_tier: str | None = None,
    picked_model: str | None = None,
    native_cost_usd: float = 0.0,
    synth_cost_usd: float = 0.0,
    plan_equiv_cost_usd: float = 0.0,
    tokens_in: int = 0,
    tokens_out: int = 0,
    latency_ms: int = 0,
) -> None:
    now = time.time()
    with _conn() as c:
        c.execute(
            """
            INSERT INTO chat_messages (
                conversation_id, role, content, ts, request_id,
                picked_tier, picked_model, native_cost_usd, synth_cost_usd,
                plan_equiv_cost_usd, tokens_in, tokens_out, latency_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                conversation_id, role, content, now, request_id,
                picked_tier, picked_model,
                float(native_cost_usd or 0.0), float(synth_cost_usd or 0.0),
                float(plan_equiv_cost_usd or 0.0),
                int(tokens_in or 0), int(tokens_out or 0), int(latency_ms or 0),
            ),
        )
        c.execute(
            "UPDATE chat_conversations SET updated_ts = ? WHERE id = ?",
            (now, conversation_id),
        )


def list_messages(conversation_id: str, team_id: str) -> list[dict[str, Any]]:
    """Returns messages for a conversation. Team scope enforced via the
    conversation lookup — caller should still verify team owns the conversation."""
    if not get_conversation(conversation_id, team_id):
        return []
    with _conn() as c:
        rows = c.execute(
            """
            SELECT role, content, ts, request_id, picked_tier, picked_model,
                   native_cost_usd, synth_cost_usd, plan_equiv_cost_usd,
                   tokens_in, tokens_out, latency_ms
            FROM chat_messages WHERE conversation_id = ? ORDER BY ts ASC, id ASC
            """,
            (conversation_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def messages_for_upstream(conversation_id: str) -> list[dict[str, str]]:
    """OpenAI-shape message list for the next upstream call (role/content only)."""
    with _conn() as c:
        rows = c.execute(
            "SELECT role, content FROM chat_messages WHERE conversation_id = ? "
            "ORDER BY ts ASC, id ASC",
            (conversation_id,),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in rows]
