"""SQLite telemetry. One row per upstream call."""
from __future__ import annotations

import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator

from .config import db_path

# In-process cache for today_spend(): (timestamp, value)
_TODAY_SPEND_CACHE: dict[str, float] = {"ts": 0.0, "value": 0.0}
_TODAY_SPEND_TTL = 5.0  # seconds

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    request_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    ts REAL NOT NULL,
    client_id TEXT,
    virtual_model TEXT,
    picked_provider TEXT,
    picked_model TEXT,
    route_reason TEXT,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    native_cost_usd REAL NOT NULL DEFAULT 0,
    plan_equiv_cost_usd REAL NOT NULL DEFAULT 0,
    drift_pct REAL NOT NULL DEFAULT 0,
    output_cost_per_1k REAL NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    escalated INTEGER NOT NULL DEFAULT 0,
    consensus_flag INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ok',
    prompt_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_calls_session ON calls(session_id);
CREATE INDEX IF NOT EXISTS idx_calls_ts ON calls(ts);

CREATE TABLE IF NOT EXISTS shadow_verdict (
    primary_request_id TEXT PRIMARY KEY,
    shadow_request_id TEXT,
    ts REAL NOT NULL,
    session_id TEXT,
    team_id TEXT,
    prompt_hash TEXT,
    picked_tier TEXT,
    shadow_tier TEXT,
    winner TEXT,            -- 'primary' | 'shadow' | 'tie'
    score INTEGER,          -- judge 1-5: shadow graded against primary as reference
    judge_model TEXT,
    note TEXT
);
CREATE INDEX IF NOT EXISTS idx_verdict_ts ON shadow_verdict(ts);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    ts REAL NOT NULL,
    rating INTEGER NOT NULL,   -- +1 thumbs-up, -1 thumbs-down
    picked_tier TEXT,
    route_reason TEXT,
    prompt_hash TEXT,
    team_id TEXT,
    note TEXT
);
CREATE INDEX IF NOT EXISTS idx_feedback_ts ON feedback(ts);
CREATE INDEX IF NOT EXISTS idx_feedback_request ON feedback(request_id);

CREATE TABLE IF NOT EXISTS tuner_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    backup_path TEXT,           -- policy.yaml backup written before this apply
    proposals_json TEXT,        -- the applied proposals (audit trail)
    reverted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tuner_ts ON tuner_log(ts);
"""

# Additive migrations applied on every init_db(). Each statement is wrapped in
# try/except sqlite3.OperationalError to handle the "column already exists" case
# without losing data. Append-only — never DROP/RENAME here.
_MIGRATIONS: list[str] = [
    "ALTER TABLE calls ADD COLUMN shadow_of TEXT",
    "ALTER TABLE calls ADD COLUMN synth_cost_usd REAL NOT NULL DEFAULT 0",
    "ALTER TABLE calls ADD COLUMN picked_tier TEXT",
    # team_id is nullable: anonymous (no Bearer header) calls stay supported.
    "ALTER TABLE calls ADD COLUMN team_id TEXT",
    "ALTER TABLE calls ADD COLUMN would_have_tier TEXT",
]


@dataclass
class CallRecord:
    session_id: str
    client_id: str | None = None
    virtual_model: str | None = None
    picked_provider: str | None = None
    picked_model: str | None = None
    route_reason: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    native_cost_usd: float = 0.0
    plan_equiv_cost_usd: float = 0.0
    drift_pct: float = 0.0
    output_cost_per_1k: float = 0.0
    latency_ms: int = 0
    escalated: bool = False
    consensus_flag: bool = False
    status: str = "ok"
    prompt_hash: str | None = None
    shadow_of: str | None = None
    synth_cost_usd: float = 0.0
    picked_tier: str | None = None
    team_id: str | None = None
    would_have_tier: str | None = None
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ts: float = field(default_factory=time.time)


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
        for stmt in _MIGRATIONS:
            try:
                c.execute(stmt)
            except sqlite3.OperationalError:
                # Column already exists or compatible no-op. Safe to ignore.
                pass


def get_call(request_id: str) -> dict | None:
    """Return a calls-table row for the given request_id, or None."""
    if not request_id:
        return None
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM calls WHERE request_id = ?", (request_id,)
        ).fetchone()
    return dict(row) if row else None


def record(call: CallRecord) -> None:
    with _conn() as c:
        c.execute(
            """
            INSERT INTO calls (
                request_id, session_id, ts, client_id, virtual_model,
                picked_provider, picked_model, route_reason,
                tokens_in, tokens_out,
                native_cost_usd, plan_equiv_cost_usd, drift_pct, output_cost_per_1k,
                latency_ms, escalated, consensus_flag, status, prompt_hash, shadow_of,
                synth_cost_usd, picked_tier, team_id, would_have_tier
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                call.request_id, call.session_id, call.ts, call.client_id, call.virtual_model,
                call.picked_provider, call.picked_model, call.route_reason,
                call.tokens_in, call.tokens_out,
                call.native_cost_usd, call.plan_equiv_cost_usd, call.drift_pct, call.output_cost_per_1k,
                call.latency_ms, int(call.escalated), int(call.consensus_flag), call.status, call.prompt_hash,
                call.shadow_of,
                float(call.synth_cost_usd or 0.0),
                call.picked_tier,
                call.team_id,
                call.would_have_tier,
            ),
        )


def record_verdict(*, primary_request_id: str, shadow_request_id: str | None,
                   session_id: str | None, team_id: str | None,
                   prompt_hash: str | None, picked_tier: str | None,
                   shadow_tier: str | None, winner: str, score: int | None,
                   judge_model: str | None, note: str | None = None) -> None:
    """Upsert one shadow-vs-primary judge verdict (Layer-2 misroute corpus)."""
    with _conn() as c:
        c.execute(
            """
            INSERT INTO shadow_verdict (
                primary_request_id, shadow_request_id, ts, session_id, team_id,
                prompt_hash, picked_tier, shadow_tier, winner, score, judge_model, note
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(primary_request_id) DO UPDATE SET
                shadow_request_id=excluded.shadow_request_id,
                ts=excluded.ts, winner=excluded.winner, score=excluded.score,
                judge_model=excluded.judge_model, note=excluded.note
            """,
            (primary_request_id, shadow_request_id, time.time(), session_id, team_id,
             prompt_hash, picked_tier, shadow_tier, winner, score, judge_model, note),
        )


def record_feedback(*, request_id: str | None, rating: int,
                    note: str | None = None) -> dict:
    """Record a thumbs up/down on a served response. Joins the calls row by
    request_id to denormalise tier/reason/prompt_hash/team into the corpus so
    the tuner can aggregate without a join. Returns the stored row."""
    rating = 1 if rating >= 0 else -1
    call = get_call(request_id) if request_id else None
    row = {
        "request_id": request_id,
        "rating": rating,
        "picked_tier": (call or {}).get("picked_tier"),
        "route_reason": (call or {}).get("route_reason"),
        "prompt_hash": (call or {}).get("prompt_hash"),
        "team_id": (call or {}).get("team_id"),
        "note": note,
        "ts": time.time(),
    }
    with _conn() as c:
        c.execute(
            """
            INSERT INTO feedback (request_id, ts, rating, picked_tier,
                route_reason, prompt_hash, team_id, note)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (row["request_id"], row["ts"], row["rating"], row["picked_tier"],
             row["route_reason"], row["prompt_hash"], row["team_id"], row["note"]),
        )
    return row


def feedback_summary(*, team_id: str | None = None, window_minutes: int = 7 * 24 * 60) -> dict:
    """Aggregate feedback: totals + per-tier + per-rule down-vote rates."""
    start = time.time() - window_minutes * 60
    clauses = ["ts >= ?"]
    params: list = [start]
    if team_id:
        clauses.append("team_id = ?")
        params.append(team_id)
    where = "WHERE " + " AND ".join(clauses)
    with _conn() as c:
        agg = c.execute(
            f"""SELECT COUNT(*) AS n,
                       COALESCE(SUM(CASE WHEN rating>0 THEN 1 ELSE 0 END),0) AS up,
                       COALESCE(SUM(CASE WHEN rating<0 THEN 1 ELSE 0 END),0) AS down
                FROM feedback {where}""",
            tuple(params),
        ).fetchone()
        by_rule = c.execute(
            f"""SELECT route_reason, picked_tier, COUNT(*) AS n,
                       COALESCE(SUM(CASE WHEN rating<0 THEN 1 ELSE 0 END),0) AS down
                FROM feedback {where}
                GROUP BY route_reason, picked_tier
                ORDER BY down DESC, n DESC LIMIT 50""",
            tuple(params),
        ).fetchall()
    n = int(agg["n"] or 0)
    down = int(agg["down"] or 0)
    return {
        "window_minutes": window_minutes,
        "total": n,
        "up": int(agg["up"] or 0),
        "down": down,
        "down_rate_pct": round(down / n * 100.0, 2) if n else 0.0,
        "by_rule": [dict(r) for r in by_rule],
    }


def verdict_by_pair(window_minutes: int = 7 * 24 * 60) -> list[dict]:
    """Per (picked_tier, shadow_tier): judged count + shadow-win count, used by
    the tuner to spot systematic under-routing."""
    start = time.time() - window_minutes * 60
    with _conn() as c:
        rows = c.execute(
            """SELECT picked_tier, shadow_tier,
                      COUNT(*) AS judged,
                      COALESCE(SUM(CASE WHEN winner='shadow' THEN 1 ELSE 0 END),0) AS shadow_wins
               FROM shadow_verdict
               WHERE ts >= ? AND picked_tier IS NOT NULL AND shadow_tier IS NOT NULL
               GROUP BY picked_tier, shadow_tier""",
            (start,),
        ).fetchall()
    return [dict(r) for r in rows]


def record_tune(*, backup_path: str, proposals_json: str) -> int:
    """Log a tuner apply. Returns the new tuner_log id."""
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO tuner_log (ts, backup_path, proposals_json, reverted) "
            "VALUES (?,?,?,0)",
            (time.time(), backup_path, proposals_json),
        )
        return int(cur.lastrowid)


def latest_tune(include_reverted: bool = False) -> dict | None:
    """Most recent tuner_log row (default: only un-reverted)."""
    q = "SELECT * FROM tuner_log"
    if not include_reverted:
        q += " WHERE reverted = 0"
    q += " ORDER BY ts DESC LIMIT 1"
    with _conn() as c:
        row = c.execute(q).fetchone()
    return dict(row) if row else None


def mark_tune_reverted(tune_id: int) -> None:
    with _conn() as c:
        c.execute("UPDATE tuner_log SET reverted = 1 WHERE id = ?", (tune_id,))


def tune_history(limit: int = 50) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM tuner_log ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def today_spend() -> float:
    """Sum of native_cost_usd for rows since UTC midnight. Cached 5s in-process."""
    now = time.time()
    if now - _TODAY_SPEND_CACHE["ts"] < _TODAY_SPEND_TTL:
        return _TODAY_SPEND_CACHE["value"]
    midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff = midnight.timestamp()
    with _conn() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(native_cost_usd), 0) AS spent FROM calls WHERE ts >= ?",
            (cutoff,),
        ).fetchone()
    val = float(row["spent"] or 0.0)
    _TODAY_SPEND_CACHE["ts"] = now
    _TODAY_SPEND_CACHE["value"] = val
    return val


def metrics_snapshot() -> dict:
    """Aggregate counters for Prometheus exposition. Cheap single query."""
    with _conn() as c:
        agg = c.execute(
            """
            SELECT
                COALESCE(SUM(native_cost_usd), 0) AS native_total,
                COALESCE(SUM(plan_equiv_cost_usd), 0) AS plan_equiv_total,
                COALESCE(SUM(tokens_out), 0) AS tokens_out
            FROM calls
            """
        ).fetchone()
        rows = c.execute(
            """
            SELECT picked_tier, picked_provider, picked_model, status,
                   COALESCE(team_id, 'anon') AS team_id,
                   COUNT(*) AS n
            FROM calls
            GROUP BY picked_tier, picked_provider, picked_model, status, team_id
            """
        ).fetchall()

    native = float(agg["native_total"] or 0.0)
    plan_equiv = float(agg["plan_equiv_total"] or 0.0)
    drift = ((plan_equiv - native) / plan_equiv * 100.0) if plan_equiv > 0 else 0.0

    # Bucket per (picked_tier, provider, status, team_id). picked_tier is a
    # first-class column written at route time; team_id was added when teams
    # landed (NULL → "anon" for back-compat single-tenant traffic).
    buckets: dict[tuple[str, str, str, str], int] = {}
    for r in rows:
        provider = r["picked_provider"] or "unknown"
        status = r["status"] or "ok"
        # status simplification: collapse error:* into "error"
        if status.startswith("error"):
            status = "error"
        tier = r["picked_tier"] or "unknown"
        team = r["team_id"] or "anon"
        key = (tier, provider, status, team)
        buckets[key] = buckets.get(key, 0) + int(r["n"] or 0)

    return {
        "native_total": native,
        "plan_equiv_total": plan_equiv,
        "tokens_out_total": int(agg["tokens_out"] or 0),
        "drift_pct": drift,
        "buckets": buckets,  # dict[(tier, provider, status, team_id)] -> count
    }


def stats(session_id: str | None = None, team_id: str | None = None) -> dict:
    """Aggregate KPIs + per-call rows for explorer.

    Both filters are AND-combined. `team_id` scopes aggregates AND the row list
    so the explorer view only sees the team's own calls when an admin pivots
    to a specific team.
    """
    with _conn() as c:
        clauses: list[str] = []
        params: list = []
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if team_id:
            clauses.append("team_id = ?")
            params.append(team_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params_t = tuple(params)

        agg = c.execute(
            f"""
            SELECT
                COUNT(*) AS calls,
                COALESCE(SUM(tokens_in), 0) AS tokens_in,
                COALESCE(SUM(tokens_out), 0) AS tokens_out,
                COALESCE(SUM(native_cost_usd), 0) AS native_total,
                COALESCE(SUM(plan_equiv_cost_usd), 0) AS plan_equiv_total,
                COALESCE(SUM(synth_cost_usd), 0) AS synth_total,
                COALESCE(MIN(output_cost_per_1k), 0) AS best_cost_per_1k,
                COALESCE(SUM(CASE WHEN picked_model = 'cache' THEN 1 ELSE 0 END), 0) AS cache_hits,
                COALESCE(SUM(CASE WHEN picked_model = 'cache' THEN plan_equiv_cost_usd ELSE 0 END), 0) AS cache_savings_usd,
                COALESCE(SUM(CASE WHEN route_reason LIKE 'semantic_cache_hit%' THEN 1 ELSE 0 END), 0) AS semantic_hits,
                COALESCE(SUM(CASE WHEN route_reason LIKE 'semantic_cache_hit%' THEN plan_equiv_cost_usd ELSE 0 END), 0) AS semantic_savings_usd
            FROM calls {where}
            """,
            params_t,
        ).fetchone()

        rows = c.execute(
            f"""
            SELECT request_id, picked_provider, picked_model, picked_tier,
                   latency_ms, tokens_in, tokens_out,
                   native_cost_usd, plan_equiv_cost_usd, output_cost_per_1k,
                   route_reason, escalated, consensus_flag, shadow_of, ts, team_id,
                   would_have_tier
            FROM calls {where}
            ORDER BY ts DESC LIMIT 200
            """,
            params_t,
        ).fetchall()

        sessions = c.execute(
            "SELECT session_id, COUNT(*) AS n FROM calls GROUP BY session_id ORDER BY MAX(ts) DESC"
        ).fetchall()

    native = agg["native_total"] or 0.0
    plan_equiv = agg["plan_equiv_total"] or 0.0
    synth = float(agg["synth_total"] or 0.0)
    # Subscription mode: when calls went via the Claude CLI, native is $0 but
    # the CLI reports a notional API price (synth). Compute drift from synth so
    # the savings number reflects "what API would have cost − what subscription
    # notionally cost". Falls back to native-based drift in normal mode.
    if synth > 0 and native == 0 and plan_equiv > 0:
        drift = ((plan_equiv - synth) / plan_equiv) * 100.0
    elif plan_equiv > 0:
        drift = ((plan_equiv - native) / plan_equiv) * 100.0
    else:
        drift = 0.0

    return {
        "kpis": {
            "calls": agg["calls"],
            "tokens_in": agg["tokens_in"],
            "tokens_out": agg["tokens_out"],
            "native_total": round(native, 4),
            "plan_equiv_total": round(plan_equiv, 4),
            "synth_total": round(synth, 4),
            "drift_pct": round(drift, 1),
            "best_cost_per_1k": round(agg["best_cost_per_1k"], 4),
            "cache_hits": int(agg["cache_hits"] or 0),
            "cache_savings_usd": round(float(agg["cache_savings_usd"] or 0.0), 4),
            "semantic_hits": int(agg["semantic_hits"] or 0),
            "semantic_savings_usd": round(float(agg["semantic_savings_usd"] or 0.0), 4),
        },
        "rows": [dict(r) for r in rows],
        "sessions": [dict(s) for s in sessions],
    }
