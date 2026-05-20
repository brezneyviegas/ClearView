"""Routing-accuracy Layer 2: auto-shadow trigger, LLM-judge, verdict storage."""
from __future__ import annotations

import pytest

from app import shadow_judge, telemetry
from app.main import _auto_shadow_tier
from tests.conftest import FakeCompletion


# ---------------------------------------------------------------------------
# _auto_shadow_tier gating
# ---------------------------------------------------------------------------

class TestAutoShadowGate:
    def test_off_by_default(self, monkeypatch):
        monkeypatch.delenv("CLEARVIEW_AUTO_SHADOW", raising=False)
        assert _auto_shadow_tier("cheap", "mid") is None

    def test_fires_on_disagreement(self, monkeypatch):
        monkeypatch.setenv("CLEARVIEW_AUTO_SHADOW", "disagree")
        assert _auto_shadow_tier("cheap", "mid") == "mid"

    def test_no_fire_on_agreement(self, monkeypatch):
        monkeypatch.setenv("CLEARVIEW_AUTO_SHADOW", "disagree")
        assert _auto_shadow_tier("mid", "mid") is None

    def test_no_fire_without_classifier_tier(self, monkeypatch):
        monkeypatch.setenv("CLEARVIEW_AUTO_SHADOW", "disagree")
        assert _auto_shadow_tier("cheap", None) is None

    def test_rate_zero_never_fires(self, monkeypatch):
        monkeypatch.setenv("CLEARVIEW_AUTO_SHADOW", "disagree")
        monkeypatch.setenv("CLEARVIEW_AUTO_SHADOW_RATE", "0")
        assert _auto_shadow_tier("cheap", "mid") is None

    def test_bad_rate_defaults_to_one(self, monkeypatch):
        monkeypatch.setenv("CLEARVIEW_AUTO_SHADOW", "disagree")
        monkeypatch.setenv("CLEARVIEW_AUTO_SHADOW_RATE", "notafloat")
        assert _auto_shadow_tier("cheap", "mid") == "mid"


# ---------------------------------------------------------------------------
# shadow_judge
# ---------------------------------------------------------------------------

class TestShadowJudge:
    def test_parse_score(self):
        assert shadow_judge._parse_score("5") == 5
        assert shadow_judge._parse_score("score: 2 because") == 2
        assert shadow_judge._parse_score("no digit") is None
        assert shadow_judge._parse_score("9") is None  # out of 1-5 range

    def test_winner_mapping(self):
        assert shadow_judge._winner_from_score(5) == "shadow"
        assert shadow_judge._winner_from_score(4) == "shadow"
        assert shadow_judge._winner_from_score(3) == "tie"
        assert shadow_judge._winner_from_score(2) == "primary"
        assert shadow_judge._winner_from_score(1) == "primary"

    def test_judge_skips_empty_text(self):
        assert shadow_judge.judge(prompt="p", primary_text="",
                                  shadow_text="x", judge_model="m") is None

    def test_judge_happy_path(self, monkeypatch):
        def fake_completion(**kw):
            return {"choices": [{"message": {"content": "5"}}]}
        monkeypatch.setattr(shadow_judge.litellm, "completion", fake_completion)
        out = shadow_judge.judge(prompt="p", primary_text="a", shadow_text="b",
                                 judge_model="m")
        assert out == {"score": 5, "winner": "shadow"}

    def test_judge_swallows_failure(self, monkeypatch):
        def boom(**kw):
            raise RuntimeError("judge down")
        monkeypatch.setattr(shadow_judge.litellm, "completion", boom)
        assert shadow_judge.judge(prompt="p", primary_text="a", shadow_text="b",
                                  judge_model="m") is None

    def test_judge_unparseable_returns_none(self, monkeypatch):
        monkeypatch.setattr(shadow_judge.litellm, "completion",
                            lambda **kw: {"choices": [{"message": {"content": "meh"}}]})
        assert shadow_judge.judge(prompt="p", primary_text="a", shadow_text="b",
                                  judge_model="m") is None


# ---------------------------------------------------------------------------
# telemetry.record_verdict + endpoint
# ---------------------------------------------------------------------------

class TestVerdictStorage:
    def test_record_and_upsert(self, tmp_db):
        telemetry.record_verdict(
            primary_request_id="p1", shadow_request_id="s1",
            session_id="sess", team_id=None, prompt_hash="h",
            picked_tier="cheap", shadow_tier="mid", winner="shadow",
            score=5, judge_model="m")
        # Upsert: same primary id, new verdict overwrites.
        telemetry.record_verdict(
            primary_request_id="p1", shadow_request_id="s1",
            session_id="sess", team_id=None, prompt_hash="h",
            picked_tier="cheap", shadow_tier="mid", winner="primary",
            score=1, judge_model="m")
        import sqlite3
        from app.config import db_path
        c = sqlite3.connect(db_path())
        rows = c.execute("SELECT winner, score FROM shadow_verdict").fetchall()
        c.close()
        assert rows == [("primary", 1)]


class TestVerdictEndpoint:
    def test_aggregates_under_route_rate(self, client):
        for pid, winner in [("p1", "shadow"), ("p2", "shadow"),
                            ("p3", "primary"), ("p4", "tie")]:
            telemetry.record_verdict(
                primary_request_id=pid, shadow_request_id="s_" + pid,
                session_id="sess", team_id=None, prompt_hash="h",
                picked_tier="cheap", shadow_tier="mid", winner=winner,
                score=5, judge_model="m")
        r = client.get("/admin/shadow_verdicts")
        assert r.status_code == 200
        body = r.json()
        assert body["judged"] == 4
        assert body["shadow_wins"] == 2
        assert body["under_route_rate_pct"] == 50.0
        assert body["over_route_rate_pct"] == 25.0

    def test_http_auto_shadow_triggers_on_disagreement(self, client, monkeypatch):
        """Non-stream request with rule≠classifier disagreement auto-fires a
        shadow to the classifier's tier — no x-clearview-shadow header needed."""
        from app import main
        monkeypatch.setenv("CLEARVIEW_AUTO_SHADOW", "disagree")
        # Force the classifier to disagree with the routed tier.
        monkeypatch.setattr(main, "would_have_tier", lambda *a, **k: "frontier")

        seen = []

        # Capture synchronously: _run_shadow(**kw) is evaluated before the
        # create_task that schedules it, so a plain recorder sees the kwargs
        # without depending on the background task actually running.
        def _fake_shadow(**kw):
            seen.append(kw)
            return None

        monkeypatch.setattr(main, "_run_shadow", _fake_shadow)
        monkeypatch.setattr(main.asyncio, "create_task", lambda coro: None)
        monkeypatch.setattr(
            main.litellm, "completion",
            lambda **kw: FakeCompletion(content="ok", prompt_tokens=4, completion_tokens=2))

        r = client.post("/v1/chat/completions",
                        json={"messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 200
        assert seen
        assert seen[0]["shadow_tier"] == "frontier"

    def test_run_shadow_records_verdict_when_judge_enabled(self, client, monkeypatch):
        from app import main
        monkeypatch.setenv("CLEARVIEW_AUTO_SHADOW_JUDGE", "1")

        async def _fake_upstream(kwargs):
            return {"choices": [{"message": {"role": "assistant", "content": "shadow answer"}}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 3}}

        monkeypatch.setattr(main, "_acall_upstream", _fake_upstream)
        monkeypatch.setattr(main.shadow_judge, "judge",
                            lambda **kw: {"score": 5, "winner": "shadow"})

        import asyncio
        asyncio.run(main._run_shadow(
            shadow_tier="mid", primary_request_id="pX", primary_model="openai/gpt-4o-mini",
            primary_tier="cheap", messages=[{"role": "user", "content": "q"}],
            body={"messages": []}, session_id="s", client_id=None, requested="auto",
            prompt_text="q", primary_text="primary answer"))

        import sqlite3
        from app.config import db_path
        c = sqlite3.connect(db_path())
        row = c.execute(
            "SELECT winner, score, shadow_tier, picked_tier FROM shadow_verdict "
            "WHERE primary_request_id='pX'").fetchone()
        c.close()
        assert row == ("shadow", 5, "mid", "cheap")

    def test_rule_hits_share(self, client):
        for reason, tier in [("rule:tiny_prompt", "cheap"),
                             ("rule:tiny_prompt", "cheap"),
                             ("rule:stack_trace", "mid")]:
            telemetry.record(telemetry.CallRecord(
                session_id="rh", route_reason=reason, picked_tier=tier))
        r = client.get("/admin/rule_hits")
        assert r.status_code == 200
        body = r.json()
        assert body["total_calls"] == 3
        top = body["rows"][0]
        assert top["route_reason"] == "rule:tiny_prompt"
        assert top["hits"] == 2
        assert top["share_pct"] == pytest.approx(66.67, abs=0.01)
