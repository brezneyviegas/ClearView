"""Unit tests for app.telemetry."""
from __future__ import annotations

import sqlite3
import time

import pytest

from app import telemetry
from app.telemetry import CallRecord, init_db, record, stats


class TestInitDb:
    def test_creates_table(self, tmp_db):
        # tmp_db autouse fixture has already run init_db once.
        with sqlite3.connect(str(tmp_db)) as c:
            row = c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='calls'"
            ).fetchone()
        assert row is not None

    def test_idempotent(self, tmp_db):
        # Calling init_db again should not raise nor wipe data.
        init_db()
        record(CallRecord(session_id="s1", tokens_out=10))
        init_db()
        init_db()
        result = stats(session_id="s1")
        assert result["kpis"]["calls"] == 1


class TestRecord:
    def test_round_trip(self):
        rec = CallRecord(
            session_id="abc",
            client_id="cursor",
            virtual_model="clearview-auto",
            picked_provider="openai",
            picked_model="openai/gpt-4o-mini",
            route_reason="rule:tiny_prompt",
            tokens_in=100,
            tokens_out=50,
            native_cost_usd=0.001,
            plan_equiv_cost_usd=0.01,
            drift_pct=90.0,
            output_cost_per_1k=0.02,
            latency_ms=123,
            escalated=False,
            prompt_hash="deadbeef",
        )
        record(rec)

        result = stats(session_id="abc")
        assert result["kpis"]["calls"] == 1
        assert result["kpis"]["tokens_in"] == 100
        assert result["kpis"]["tokens_out"] == 50
        assert len(result["rows"]) == 1
        row = result["rows"][0]
        assert row["picked_model"] == "openai/gpt-4o-mini"
        assert row["route_reason"] == "rule:tiny_prompt"

    def test_default_request_id_unique(self):
        a = CallRecord(session_id="x")
        b = CallRecord(session_id="x")
        assert a.request_id != b.request_id

    def test_default_ts_recent(self):
        rec = CallRecord(session_id="x")
        assert abs(rec.ts - time.time()) < 1.0


class TestStats:
    def test_empty_db(self):
        result = stats()
        assert result["kpis"]["calls"] == 0
        assert result["kpis"]["native_total"] == 0
        assert result["kpis"]["drift_pct"] == 0
        assert result["rows"] == []

    def test_aggregates_across_sessions(self):
        record(CallRecord(session_id="A", tokens_in=10, tokens_out=20,
                          native_cost_usd=0.5, plan_equiv_cost_usd=2.0,
                          output_cost_per_1k=0.1))
        record(CallRecord(session_id="A", tokens_in=30, tokens_out=40,
                          native_cost_usd=1.5, plan_equiv_cost_usd=4.0,
                          output_cost_per_1k=0.2))
        record(CallRecord(session_id="B", tokens_in=5, tokens_out=5,
                          native_cost_usd=0.05, plan_equiv_cost_usd=0.5,
                          output_cost_per_1k=0.05))

        all_stats = stats()
        assert all_stats["kpis"]["calls"] == 3
        assert all_stats["kpis"]["tokens_in"] == 45
        assert all_stats["kpis"]["tokens_out"] == 65
        assert all_stats["kpis"]["native_total"] == pytest.approx(2.05)
        assert all_stats["kpis"]["plan_equiv_total"] == pytest.approx(6.5)
        # Drift = (6.5 - 2.05) / 6.5 * 100 ≈ 68.5%
        assert all_stats["kpis"]["drift_pct"] == pytest.approx(68.5, abs=0.1)
        # best_cost_per_1k is MIN over the rows.
        assert all_stats["kpis"]["best_cost_per_1k"] == pytest.approx(0.05)

        a_stats = stats(session_id="A")
        assert a_stats["kpis"]["calls"] == 2
        assert a_stats["kpis"]["tokens_out"] == 60
        # Drift for A = (6.0 - 2.0) / 6.0 * 100 ≈ 66.7%
        assert a_stats["kpis"]["drift_pct"] == pytest.approx(66.7, abs=0.1)

    def test_drift_zero_when_baseline_zero(self):
        record(CallRecord(session_id="zero", tokens_out=10,
                          native_cost_usd=0.5, plan_equiv_cost_usd=0.0))
        result = stats(session_id="zero")
        assert result["kpis"]["drift_pct"] == 0.0

    def test_session_listing(self):
        record(CallRecord(session_id="alpha"))
        record(CallRecord(session_id="beta"))
        record(CallRecord(session_id="alpha"))
        result = stats()
        sessions = {s["session_id"]: s["n"] for s in result["sessions"]}
        assert sessions == {"alpha": 2, "beta": 1}

    def test_rows_ordered_desc_by_ts(self):
        record(CallRecord(session_id="t", ts=100.0, route_reason="oldest"))
        record(CallRecord(session_id="t", ts=200.0, route_reason="middle"))
        record(CallRecord(session_id="t", ts=300.0, route_reason="newest"))
        result = stats(session_id="t")
        reasons = [r["route_reason"] for r in result["rows"]]
        assert reasons == ["newest", "middle", "oldest"]
