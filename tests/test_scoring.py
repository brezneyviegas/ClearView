"""Stock-market composite provider scoring (#13)."""
from __future__ import annotations

import pytest

from app import router, scoring, telemetry


def _seed(bucket, provider, tier, *, n, outcome, cost, latency, tokens_out):
    """Seed n provider_score samples with quality outcome + per-call metrics."""
    for _ in range(n):
        telemetry.record_provider_outcome(
            bucket=bucket, provider=provider, tier=tier, outcome=outcome,
            cost=cost, latency_ms=latency, tokens_out=tokens_out)


class TestWeights:
    def test_renormalize_to_one(self, monkeypatch):
        monkeypatch.setenv("CLEARVIEW_SCORE_W_QUALITY", "1")
        monkeypatch.setenv("CLEARVIEW_SCORE_W_COST", "1")
        monkeypatch.setenv("CLEARVIEW_SCORE_W_LATENCY", "0")
        monkeypatch.setenv("CLEARVIEW_SCORE_W_BURN", "0")
        w = scoring.weights()
        assert abs(sum(w.values()) - 1.0) < 1e-9
        assert w["quality"] == pytest.approx(0.5)


class TestNorm:
    def test_higher_better(self):
        out = scoring._norm({"a": 10, "b": 0}, higher_better=True)
        assert out["a"] == 1.0 and out["b"] == 0.0

    def test_lower_better_inverts(self):
        out = scoring._norm({"a": 10, "b": 0}, higher_better=False)
        assert out["a"] == 0.0 and out["b"] == 1.0

    def test_all_equal_is_neutral(self):
        out = scoring._norm({"a": 5, "b": 5}, higher_better=False)
        assert out == {"a": 0.5, "b": 0.5}


class TestComposite:
    def test_cheaper_faster_wins_when_quality_ties(self, tmp_db):
        # Quality tie; gemini far cheaper + faster + less verbose → higher score.
        b = "cheap:rule:tiny_prompt"
        _seed(b, "anthropic", "cheap", n=10, outcome="tie", cost=0.13, latency=35000, tokens_out=200)
        _seed(b, "gemini", "cheap", n=10, outcome="tie", cost=0.02, latency=4000, tokens_out=80)
        scores = scoring.composite_scores(b)
        assert scores["gemini"]["score"] > scores["anthropic"]["score"]
        # gemini best on every lower-is-better axis → 1.0 there
        assert scores["gemini"]["cost"] == 1.0 and scores["gemini"]["latency"] == 1.0

    def test_quality_can_outweigh_cost(self, tmp_db, monkeypatch):
        monkeypatch.setenv("CLEARVIEW_SCORE_W_QUALITY", "0.9")
        monkeypatch.setenv("CLEARVIEW_SCORE_W_COST", "0.1")
        monkeypatch.setenv("CLEARVIEW_SCORE_W_LATENCY", "0")
        monkeypatch.setenv("CLEARVIEW_SCORE_W_BURN", "0")
        b = "mid:rule:x"
        _seed(b, "anthropic", "mid", n=10, outcome="win", cost=0.20, latency=5000, tokens_out=300)
        _seed(b, "openai", "mid", n=10, outcome="loss", cost=0.01, latency=5000, tokens_out=300)
        scores = scoring.composite_scores(b)
        assert scores["anthropic"]["score"] > scores["openai"]["score"]


class TestBestByComposite:
    def test_needs_two_eligible(self, tmp_db):
        b = "cheap:rule:y"
        _seed(b, "gemini", "cheap", n=10, outcome="tie", cost=0.01, latency=100, tokens_out=10)
        # only one provider with data → None (nothing to rank)
        assert scoring.best_by_composite(b, ["anthropic", "gemini"], 5) is None

    def test_ranks_two(self, tmp_db):
        b = "cheap:rule:z"
        _seed(b, "anthropic", "cheap", n=10, outcome="tie", cost=0.13, latency=4000, tokens_out=80)
        _seed(b, "gemini", "cheap", n=10, outcome="tie", cost=0.02, latency=4000, tokens_out=80)
        assert scoring.best_by_composite(b, ["anthropic", "gemini"], 5) == "gemini"


class TestRouterUsesComposite:
    def test_scoring_picks_cheaper_provider(self, policy, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
        monkeypatch.setenv("GEMINI_API_KEY", "x")
        monkeypatch.setenv("CLEARVIEW_PROVIDER_SCORING", "1")
        monkeypatch.setenv("CLEARVIEW_PROVIDER_MIN_N", "5")
        policy.tiers = {"cheap": ["anthropic/claude-haiku-4-5", "gemini/gemini-1.5-flash"],
                        "mid": ["anthropic/claude-sonnet-4-6"],
                        "frontier": ["anthropic/claude-opus-4-7"]}
        router.build_availability(policy)
        b = "cheap:rule:tiny_prompt"
        _seed(b, "anthropic", "cheap", n=10, outcome="tie", cost=0.13, latency=4000, tokens_out=80)
        _seed(b, "gemini", "cheap", n=10, outcome="tie", cost=0.02, latency=4000, tokens_out=80)
        # composite favors gemini (cheaper, quality tie) → picked over first-listed anthropic
        assert router._pick_model("cheap", policy, b) == "gemini/gemini-1.5-flash"


class TestEndpoint:
    def test_provider_scores_has_composite(self, client):
        b = "cheap:rule:tiny_prompt"
        _seed(b, "anthropic", "cheap", n=10, outcome="tie", cost=0.13, latency=4000, tokens_out=80)
        _seed(b, "gemini", "cheap", n=10, outcome="tie", cost=0.02, latency=4000, tokens_out=80)
        body = client.get("/admin/provider_scores").json()
        provs = body["buckets"][b]
        assert "composite" in provs[0] and "score_breakdown" in provs[0]
        assert provs[0]["provider"] == "gemini"  # ranked top by composite
