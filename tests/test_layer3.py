"""Routing-accuracy Layer 3: feedback corpus, embedding classifier, tuner."""
from __future__ import annotations

import sqlite3

import pytest
import yaml

from app import embed_classifier, telemetry, tuner
from app.config import db_path, load_policy


# ---------------------------------------------------------------------------
# Feedback corpus
# ---------------------------------------------------------------------------

class TestFeedback:
    def test_record_denormalises_from_call(self, tmp_db):
        telemetry.record(telemetry.CallRecord(
            request_id="rq1", session_id="s", picked_tier="cheap",
            route_reason="rule:tiny_prompt", prompt_hash="h", team_id="t1"))
        row = telemetry.record_feedback(request_id="rq1", rating=-1, note="bad")
        assert row["picked_tier"] == "cheap"
        assert row["route_reason"] == "rule:tiny_prompt"
        assert row["rating"] == -1

    def test_record_clamps_rating(self, tmp_db):
        assert telemetry.record_feedback(request_id=None, rating=7)["rating"] == 1
        assert telemetry.record_feedback(request_id=None, rating=-3)["rating"] == -1

    def test_summary_rates(self, tmp_db):
        for r in (1, 1, -1):
            telemetry.record_feedback(request_id=None, rating=r)
        s = telemetry.feedback_summary()
        assert s["total"] == 3 and s["up"] == 2 and s["down"] == 1
        assert s["down_rate_pct"] == pytest.approx(33.33, abs=0.01)

    def test_endpoint_roundtrip(self, client):
        r = client.post("/feedback", json={"request_id": "x", "rating": -1})
        assert r.status_code == 200 and r.json()["ok"]
        assert client.post("/feedback", json={"rating": "nope"}).status_code == 400
        assert client.get("/admin/feedback").json()["down"] == 1


# ---------------------------------------------------------------------------
# Embedding classifier
# ---------------------------------------------------------------------------

class TestEmbedClassifier:
    def setup_method(self):
        embed_classifier.reset()

    def teardown_method(self):
        embed_classifier.reset()

    def test_disabled_returns_none(self, monkeypatch):
        monkeypatch.delenv("CLEARVIEW_EMBED_CLASSIFIER", raising=False)
        assert embed_classifier.classify("anything") is None

    def test_knn_vote(self, monkeypatch):
        monkeypatch.setenv("CLEARVIEW_EMBED_CLASSIFIER", "1")
        monkeypatch.setattr(embed_classifier._emb, "is_enabled", lambda: True)
        # Deterministic 2-D embeddings: tier encoded as axis.
        vecs = {"cheapish": [1.0, 0.0], "frontierish": [0.0, 1.0]}
        monkeypatch.setattr(embed_classifier._emb, "embed",
                            lambda t: vecs.get(t, [1.0, 0.0]))
        embed_classifier._INDEX = [
            ("cheap", [1.0, 0.0]), ("cheap", [0.9, 0.1]),
            ("frontier", [0.0, 1.0]), ("frontier", [0.1, 0.9]),
        ]
        monkeypatch.setattr(embed_classifier, "_k", lambda: 3)
        tier, conf = embed_classifier.classify("cheapish")
        assert tier == "cheap" and 0 < conf <= 1
        tier2, _ = embed_classifier.classify("frontierish")
        assert tier2 == "frontier"

    def test_empty_index_returns_none(self, monkeypatch):
        monkeypatch.setenv("CLEARVIEW_EMBED_CLASSIFIER", "1")
        monkeypatch.setattr(embed_classifier._emb, "is_enabled", lambda: True)
        embed_classifier._INDEX = []
        assert embed_classifier.classify("x") is None

    def test_router_uses_embed_when_llm_classifier_disabled(self, policy, monkeypatch):
        from app import router
        policy.classifier.enabled = False
        monkeypatch.setenv("CLEARVIEW_EMBED_CLASSIFIER", "1")
        monkeypatch.setattr(embed_classifier._emb, "is_enabled", lambda: True)
        monkeypatch.setattr(embed_classifier._emb, "embed", lambda t: [0.0, 1.0])
        embed_classifier._INDEX = [("frontier", [0.0, 1.0])]
        # Prompt that escapes all rules (has code fence, ~mid length).
        prompt = "```\nx\n```\n" + ("word " * 100)
        decision = router.route(prompt, policy)
        assert decision.tier == "frontier"
        assert decision.reason.startswith("embed_classifier:")


# ---------------------------------------------------------------------------
# Tuner
# ---------------------------------------------------------------------------

def _insert_feedback(reason, tier, rating):
    import time
    c = sqlite3.connect(db_path())
    c.execute("INSERT INTO feedback (request_id, ts, rating, picked_tier, route_reason) "
              "VALUES (?,?,?,?,?)", (None, time.time(), rating, tier, reason))
    c.commit()
    c.close()


class TestTuner:
    def test_analyze_proposes_rule_bump_on_downvotes(self, tmp_db, monkeypatch):
        monkeypatch.setenv("CLEARVIEW_TUNE_MIN_FEEDBACK", "5")
        monkeypatch.setenv("CLEARVIEW_TUNE_DOWNVOTE_PCT", "50")
        pol = load_policy()  # real policy.yaml: tiny_prompt -> cheap
        for _ in range(8):
            _insert_feedback("rule:tiny_prompt", "cheap", -1)
        proposals = tuner.analyze(pol)
        bumps = [p for p in proposals if p.kind == "rule_tier_bump"]
        assert bumps and bumps[0].target == "tiny_prompt"
        assert bumps[0].current == "cheap" and bumps[0].proposed == "mid"

    def test_analyze_proposes_floor_bump_on_under_route(self, tmp_db, monkeypatch):
        monkeypatch.setenv("CLEARVIEW_TUNE_MIN_PAIRS", "5")
        monkeypatch.setenv("CLEARVIEW_TUNE_UNDER_ROUTE_PCT", "60")
        pol = load_policy()
        for i in range(8):
            telemetry.record_verdict(
                primary_request_id=f"p{i}", shadow_request_id=f"s{i}",
                session_id="s", team_id=None, prompt_hash="h",
                picked_tier="cheap", shadow_tier="mid", winner="shadow",
                score=5, judge_model="m")
        proposals = tuner.analyze(pol)
        floors = [p for p in proposals if p.kind == "confidence_floor_bump"]
        assert floors and floors[0].proposed > floors[0].current

    def test_apply_backs_up_and_mutates_then_revert(self, tmp_db, tmp_path, monkeypatch):
        # Work on a throwaway copy of the real policy so the repo file is safe.
        src = load_policy()  # validates repo policy.yaml parses
        pol_path = tmp_path / "policy.yaml"
        import shutil, pathlib
        shutil.copy2(pathlib.Path("policy.yaml"), pol_path)
        monkeypatch.setenv("CLEARVIEW_POLICY_PATH", str(pol_path))
        pol = load_policy(str(pol_path))

        p = tuner.Proposal(kind="rule_tier_bump", target="tiny_prompt",
                           current="cheap", proposed="mid", reason="test")
        res = tuner.apply([p])
        assert res["applied"] == 1 and res["backup_path"]
        after = yaml.safe_load(pol_path.read_text())
        bumped = [r for r in after["rules"] if r["name"] == "tiny_prompt"][0]
        assert bumped["then"] == "mid"

        rev = tuner.revert()
        assert rev["reverted"] is True
        restored = yaml.safe_load(pol_path.read_text())
        orig = [r for r in restored["rules"] if r["name"] == "tiny_prompt"][0]
        assert orig["then"] == "cheap"

    def test_apply_noop_when_no_proposals(self, tmp_db):
        res = tuner.apply([])
        assert res["applied"] == 0 and res["backup_path"] is None


class TestTuneEndpoints:
    def test_dry_run_then_history(self, client):
        r = client.get("/admin/tune")
        assert r.status_code == 200
        assert r.json()["dry_run"] is True
        assert "proposals" in r.json()
        assert client.get("/admin/tune/history").json()["history"] == []
