"""Per-team identity, quotas, and spend attribution.

Teams are identified by a Bearer token whose value IS the team id:
`cv_team_<32 hex chars>`. The server matches the header against the `teams`
table in the shared SQLite DB (same file as telemetry / cache).

Quota enforcement order in `app/main.py`:
    1. per-team daily_usd_cap
    2. per-team monthly_usd_cap (UTC calendar month)
    3. global daily_usd_cap (existing policy.budget behavior)

First breach wins → 429 with structured body. See `chat_completions` for
the exact JSON shapes.

This module is stdlib-only (no new pip deps). Reuses `_conn` and `db_path`
from `app.telemetry` so the migration story stays one-file simple.
"""
from __future__ import annotations

import argparse
import secrets
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import db_path
from .telemetry import _conn


# --- In-process TTL caches for per-team spend lookups -------------------------
# Same shape/scheme as telemetry._TODAY_SPEND_CACHE but keyed by team_id since
# we have N teams instead of one global value.
_TODAY_SPEND_TTL = 5.0
_MONTH_SPEND_TTL = 5.0
_today_spend_cache: dict[str, tuple[float, float]] = {}   # team_id -> (cached_at, value)
_month_spend_cache: dict[str, tuple[float, float]] = {}


_SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    daily_usd_cap REAL,
    monthly_usd_cap REAL,
    allowed_tiers TEXT NOT NULL DEFAULT '',
    created_ts REAL NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_teams_created_ts ON teams(created_ts);
"""

_MIGRATIONS = [
    "ALTER TABLE teams ADD COLUMN timezone TEXT NOT NULL DEFAULT 'UTC'",
]


@dataclass
class Team:
    id: str
    name: str
    daily_usd_cap: float | None = None
    monthly_usd_cap: float | None = None
    allowed_tiers: list[str] = field(default_factory=list)
    created_ts: float = field(default_factory=time.time)
    enabled: bool = True
    timezone: str = "UTC"


def init_db() -> None:
    with _conn() as c:
        c.executescript(_SCHEMA)
        for stmt in _MIGRATIONS:
            try:
                c.execute(stmt)
            except sqlite3.OperationalError:
                pass


def _tiers_to_csv(tiers: list[str] | None) -> str:
    if not tiers:
        return ""
    return ",".join(t.strip() for t in tiers if t and t.strip())


def _csv_to_tiers(csv: str | None) -> list[str]:
    if not csv:
        return []
    return [t for t in (s.strip() for s in csv.split(",")) if t]


def _row_to_team(r: sqlite3.Row) -> Team:
    return Team(
        id=r["id"],
        name=r["name"],
        daily_usd_cap=r["daily_usd_cap"] if r["daily_usd_cap"] is not None else None,
        monthly_usd_cap=r["monthly_usd_cap"] if r["monthly_usd_cap"] is not None else None,
        allowed_tiers=_csv_to_tiers(r["allowed_tiers"]),
        created_ts=float(r["created_ts"] or 0.0),
        enabled=bool(r["enabled"]),
        timezone=r["timezone"] if "timezone" in r.keys() and r["timezone"] else "UTC",
    )


def create(
    name: str,
    daily_usd_cap: float | None = None,
    monthly_usd_cap: float | None = None,
    allowed_tiers: list[str] | None = None,
    timezone_name: str = "UTC",
) -> Team:
    """Mint a new team. The returned `id` IS the Bearer token — store it now."""
    if not name or not name.strip():
        raise ValueError("name required")
    team_id = "cv_team_" + secrets.token_hex(16)  # 32 hex chars → 40-char id
    created_ts = time.time()
    tiers_csv = _tiers_to_csv(allowed_tiers)
    tz = _validate_timezone(timezone_name)
    with _conn() as c:
        c.execute(
            """
            INSERT INTO teams (id, name, daily_usd_cap, monthly_usd_cap,
                               allowed_tiers, created_ts, enabled, timezone)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (team_id, name.strip(), daily_usd_cap, monthly_usd_cap, tiers_csv, created_ts, tz),
        )
    return Team(
        id=team_id,
        name=name.strip(),
        daily_usd_cap=daily_usd_cap,
        monthly_usd_cap=monthly_usd_cap,
        allowed_tiers=allowed_tiers or [],
        created_ts=created_ts,
        enabled=True,
        timezone=tz,
    )


def get(team_id: str) -> Team | None:
    if not team_id:
        return None
    with _conn() as c:
        row = c.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
    return _row_to_team(row) if row else None


def list_all() -> list[Team]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM teams ORDER BY created_ts DESC"
        ).fetchall()
    return [_row_to_team(r) for r in rows]


def disable(team_id: str) -> bool:
    with _conn() as c:
        cur = c.execute("UPDATE teams SET enabled = 0 WHERE id = ?", (team_id,))
        return cur.rowcount > 0


def enable(team_id: str) -> bool:
    with _conn() as c:
        cur = c.execute("UPDATE teams SET enabled = 1 WHERE id = ?", (team_id,))
        return cur.rowcount > 0


def delete(team_id: str) -> bool:
    with _conn() as c:
        cur = c.execute("DELETE FROM teams WHERE id = ?", (team_id,))
    # Drop any cached spend numbers for this team — stale anyway.
    _today_spend_cache.pop(team_id, None)
    _month_spend_cache.pop(team_id, None)
    return cur.rowcount > 0


def update(
    team_id: str,
    *,
    daily_usd_cap: float | None = None,
    monthly_usd_cap: float | None = None,
    allowed_tiers: list[str] | None = None,
    timezone_name: str | None = None,
    enabled: bool | None = None,
    _set_daily: bool = False,
    _set_monthly: bool = False,
    _set_tiers: bool = False,
    _set_timezone: bool = False,
) -> Team | None:
    """Partial update. Pass `_set_*` flags to distinguish "leave field alone"
    from "set to None/empty". Callers in HTTP handlers detect "key in payload"
    and translate to these flags.
    """
    sets: list[str] = []
    params: list[object] = []
    if _set_daily:
        sets.append("daily_usd_cap = ?")
        params.append(daily_usd_cap)
    if _set_monthly:
        sets.append("monthly_usd_cap = ?")
        params.append(monthly_usd_cap)
    if _set_tiers:
        sets.append("allowed_tiers = ?")
        params.append(_tiers_to_csv(allowed_tiers))
    if _set_timezone:
        sets.append("timezone = ?")
        params.append(_validate_timezone(timezone_name or "UTC"))
    if enabled is not None:
        sets.append("enabled = ?")
        params.append(1 if enabled else 0)
    if not sets:
        return get(team_id)
    params.append(team_id)
    with _conn() as c:
        cur = c.execute(
            f"UPDATE teams SET {', '.join(sets)} WHERE id = ?",
            tuple(params),
        )
        if cur.rowcount == 0:
            return None
    invalidate_spend_cache(team_id)
    return get(team_id)


# --- Spend accounting --------------------------------------------------------

def _utc_midnight_ts() -> float:
    midnight = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return midnight.timestamp()


def _validate_timezone(name: str) -> str:
    tz = (name or "UTC").strip() or "UTC"
    try:
        ZoneInfo(tz)
    except ZoneInfoNotFoundError as e:
        raise ValueError(f"unknown timezone: {tz}") from e
    return tz


def _month_start_ts(timezone_name: str = "UTC") -> float:
    """First-of-month at local team timezone, returned as an absolute timestamp."""
    tz = ZoneInfo(_validate_timezone(timezone_name))
    now = datetime.now(tz)
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return first.timestamp()


def _team_spend_since(team_id: str, cutoff: float) -> float:
    with _conn() as c:
        row = c.execute(
            """
            SELECT COALESCE(SUM(native_cost_usd), 0)
                 + COALESCE(SUM(synth_cost_usd), 0) AS spent
            FROM calls
            WHERE team_id = ? AND ts >= ?
            """,
            (team_id, cutoff),
        ).fetchone()
    return float(row["spent"] or 0.0)


def today_spend(team_id: str) -> float:
    """Native+synth spend for `team_id` since UTC midnight. 5s in-process TTL."""
    now = time.time()
    cached = _today_spend_cache.get(team_id)
    if cached and (now - cached[0]) < _TODAY_SPEND_TTL:
        return cached[1]
    val = _team_spend_since(team_id, _utc_midnight_ts())
    _today_spend_cache[team_id] = (now, val)
    return val


def month_spend(team_id: str) -> float:
    """Native+synth spend since 1st of the current UTC calendar month.

    Note: monthly cap resets at UTC month boundary — no per-team timezone
    yet. See module docstring.
    """
    now = time.time()
    cached = _month_spend_cache.get(team_id)
    if cached and (now - cached[0]) < _MONTH_SPEND_TTL:
        return cached[1]
    team = get(team_id)
    tz = team.timezone if team else "UTC"
    val = _team_spend_since(team_id, _month_start_ts(tz))
    _month_spend_cache[team_id] = (now, val)
    return val


def invalidate_spend_cache(team_id: str | None = None) -> None:
    """Drop cached spend values. Called after a write to keep the
    `today_spend`/`month_spend` numbers honest in tests and right after a
    successful upstream call where the cap check on the NEXT request would
    otherwise read stale data."""
    if team_id is None:
        _today_spend_cache.clear()
        _month_spend_cache.clear()
        return
    _today_spend_cache.pop(team_id, None)
    _month_spend_cache.pop(team_id, None)


# --- CLI bootstrap -----------------------------------------------------------

def _cli() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.teams",
        description="Manage ClearView teams (multi-tenant API keys + quotas).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_create = sub.add_parser("create", help="Mint a new team and print its Bearer token.")
    p_create.add_argument("--name", required=True, help="Display name.")
    p_create.add_argument("--daily-cap", type=float, default=None,
                          help="Daily USD cap (omit → no team daily cap).")
    p_create.add_argument("--monthly-cap", type=float, default=None,
                          help="Monthly USD cap (omit → no team monthly cap).")
    p_create.add_argument("--tiers", type=str, default="",
                          help="CSV of allowed tiers (e.g. cheap,mid). Empty → all.")
    p_create.add_argument("--timezone", type=str, default="UTC",
                          help="IANA timezone for monthly cap reset (default: UTC).")

    sub.add_parser("list", help="List all teams.")

    p_disable = sub.add_parser("disable", help="Disable a team (refuse its requests).")
    p_disable.add_argument("team_id")

    p_enable = sub.add_parser("enable", help="Re-enable a team.")
    p_enable.add_argument("team_id")

    p_delete = sub.add_parser("delete", help="Delete a team. Historical calls keep team_id.")
    p_delete.add_argument("team_id")

    args = parser.parse_args()
    init_db()

    if args.cmd == "create":
        tiers = _csv_to_tiers(args.tiers) if args.tiers else None
        t = create(args.name, daily_usd_cap=args.daily_cap,
                   monthly_usd_cap=args.monthly_cap, allowed_tiers=tiers,
                   timezone_name=args.timezone)
        print(f"id:            {t.id}")
        print(f"name:          {t.name}")
        print(f"daily_cap:     {t.daily_usd_cap}")
        print(f"monthly_cap:   {t.monthly_usd_cap}")
        print(f"allowed_tiers: {','.join(t.allowed_tiers) or '(all)'}")
        print(f"timezone:      {t.timezone}")
        print(f"created_ts:    {int(t.created_ts)}")
        print("")
        print("Authorization: Bearer " + t.id)
        return 0

    if args.cmd == "list":
        for t in list_all():
            print(f"{t.id}  {t.name!r}  daily={t.daily_usd_cap}  "
                  f"monthly={t.monthly_usd_cap}  tiers={','.join(t.allowed_tiers) or '(all)'}  "
                  f"timezone={t.timezone}  enabled={t.enabled}")
        return 0

    if args.cmd == "disable":
        ok = disable(args.team_id)
        print("disabled" if ok else "not found", file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1

    if args.cmd == "enable":
        ok = enable(args.team_id)
        print("enabled" if ok else "not found", file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1

    if args.cmd == "delete":
        ok = delete(args.team_id)
        print("deleted" if ok else "not found", file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    # Resolves to the configured CLEARVIEW_DB_PATH (defaults to ./clearview.db).
    sys.exit(_cli())
