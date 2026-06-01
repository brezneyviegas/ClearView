"""Phase 1: quality-learned provider selection within a tier."""
from __future__ import annotations

import pytest

from app import router, telemetry


@pytest.fixture
def two_provider_policy(policy, monkeypatch):
    """A tier with two available providers (anthropic first, openai second)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    policy.tiers = {
        "cheap": ["anthropic/claude-haiku-4-5", "openai/gpt-4o-mini"],
        "mid": ["anthropic/claude-sonnet-4-6", "openai/gpt-4o"],
        "frontier": ["anthropic/claude-opus-4-7"],
    }
    router.build_availability(policy)
    return policy


class TestBucket:
    def test_rule_family_keeps_name(self):
        assert router.bucket_for("mid", "rule:stack_trace") == "mid:rule:stack_trace"

    def test_classifier_collapses_score(self):
        assert router.bucket_for("mid", "classifier:score=3;confidence=0.9") == "mid:classifier"

    def test_default(self):
        assert router.bucket_for("cheap", "default:cheap") == "cheap:default"


class TestLearnedPick:
    def test_off_by_default_keeps_first(self, two_provider_policy, monkeypatch):
        monkeypatch.delenv("CLEARVIEW_PROVIDER_LEARNING", raising=False)
        # Even with data, learning off → first-listed (anthropic).
        for _ in range(20):
            telemetry.record_provider_outcome(
                bucket="cheap:rule:tiny_prompt", provider="openai", tier="cheap", outcome="win")
        m = router._pick_model("cheap", two_provider_policy, "cheap:rule:tiny_prompt")
        assert m == "anthropic/claude-haiku-4-5"

    def test_cold_start_keeps_first(self, two_provider_policy, monkeypatch):
        monkeypatch.setenv("CLEARVIEW_PROVIDER_LEARNING", "1")
        # No data → cold start → first-listed.
        m = router._pick_model("cheap", two_provider_policy, "cheap:rule:tiny_prompt")
        assert m == "anthropic/claude-haiku-4-5"

    def test_learns_winning_provider(self, two_provider_policy, monkeypatch):
        monkeypatch.setenv("CLEARVIEW_PROVIDER_LEARNING", "1")
        monkeypatch.setenv("CLEARVIEW_PROVIDER_MIN_N", "5")
        bucket = "cheap:rule:tiny_prompt"
        # openai wins consistently; anthropic loses.
        for _ in range(8):
            telemetry.record_provider_outcome(bucket=bucket, provider="openai", tier="cheap", outcome="win")
            telemetry.record_provider_outcome(bucket=bucket, provider="anthropic", tier="cheap", outcome="loss")
        m = router._pick_model("cheap", two_provider_policy, bucket)
        assert m == "openai/gpt-4o-mini"

    def test_below_min_samples_falls_back(self, two_provider_policy, monkeypatch):
        monkeypatch.setenv("CLEARVIEW_PROVIDER_LEARNING", "1")
        monkeypatch.setenv("CLEARVIEW_PROVIDER_MIN_N", "10")
        bucket = "cheap:rule:tiny_prompt"
        for _ in range(3):  # below threshold
            telemetry.record_provider_outcome(bucket=bucket, provider="openai", tier="cheap", outcome="win")
        m = router._pick_model("cheap", two_provider_policy, bucket)
        assert m == "anthropic/claude-haiku-4-5"

    def test_no_bucket_keeps_first(self, two_provider_policy, monkeypatch):
        monkeypatch.setenv("CLEARVIEW_PROVIDER_LEARNING", "1")
        assert router._pick_model("cheap", two_provider_policy) == "anthropic/claude-haiku-4-5"


class TestBestProvider:
    def test_picks_highest_winrate(self, tmp_db):
        b = "mid:rule:x"
        for _ in range(10):
            telemetry.record_provider_outcome(bucket=b, provider="gemini", tier="mid", outcome="win")
        for _ in range(10):
            telemetry.record_provider_outcome(bucket=b, provider="anthropic", tier="mid", outcome="loss")
        assert telemetry.best_provider(b, ["anthropic", "gemini"], 5) == "gemini"

    def test_none_when_insufficient(self, tmp_db):
        b = "mid:rule:y"
        telemetry.record_provider_outcome(bucket=b, provider="gemini", tier="mid", outcome="win")
        assert telemetry.best_provider(b, ["gemini"], 5) is None

    def test_respects_candidate_filter(self, tmp_db):
        b = "mid:rule:z"
        for _ in range(10):
            telemetry.record_provider_outcome(bucket=b, provider="gemini", tier="mid", outcome="win")
        # gemini has data but isn't a candidate → None
        assert telemetry.best_provider(b, ["anthropic", "openai"], 5) is None


# ---------------------------------------------------------------------------
# Phase 2: provider-level shadow writes provider_score
# ---------------------------------------------------------------------------

class TestProviderShadowScoring:
    def test_run_shadow_records_provider_outcome(self, client, monkeypatch):
        """Provider shadow: judge says shadow won → shadow provider gets a win,
        primary provider a loss, for the bucket."""
        from app import main
        monkeypatch.setenv("CLEARVIEW_AUTO_SHADOW_JUDGE", "1")

        async def _fake_upstream(kwargs):
            return {"choices": [{"message": {"role": "assistant", "content": "gemini answer"}}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 4}}

        monkeypatch.setattr(main, "_acall_upstream", _fake_upstream)
        monkeypatch.setattr(main.shadow_judge, "judge",
                            lambda **kw: {"score": 5, "winner": "shadow"})

        import asyncio
        asyncio.run(main._run_shadow(
            shadow_tier="mid", shadow_model_override="gemini/gemini-1.5-pro",
            score_bucket="mid:rule:x", primary_provider="anthropic",
            primary_request_id="pid", primary_model="anthropic/claude-sonnet-4-6",
            primary_tier="mid", messages=[{"role": "user", "content": "q"}],
            body={"messages": []}, session_id="s", client_id=None, requested="auto",
            prompt_text="q", primary_text="claude answer"))

        rates = telemetry._provider_winrates("mid:rule:x")
        assert rates["gemini"][1] == 1.0     # shadow won
        assert rates["anthropic"][1] == 0.0  # primary lost

    def test_trigger_off_by_default(self, monkeypatch):
        from app import main
        monkeypatch.delenv("CLEARVIEW_PROVIDER_SHADOW", raising=False)
        assert main._provider_shadow_alt("mid", "anthropic/claude-sonnet-4-6") is None

    def test_trigger_picks_different_provider(self, client, monkeypatch):
        from app import main
        monkeypatch.setenv("CLEARVIEW_PROVIDER_SHADOW", "1")
        from app import router
        # client fixture sets fake keys, so mid has anthropic + openai available.
        alt = main._provider_shadow_alt("mid", "anthropic/claude-sonnet-4-6")
        assert alt is not None and not alt.startswith("anthropic/")


# ---------------------------------------------------------------------------
# Phase 3: feedback -> provider_score + endpoint + explorer panel
# ---------------------------------------------------------------------------

class TestProviderLearningP3:
    def test_thumbs_up_credits_provider(self, client):
        from app import telemetry
        telemetry.record(telemetry.CallRecord(
            request_id="rid1", session_id="s", picked_tier="cheap",
            picked_provider="anthropic", route_reason="rule:tiny_prompt", prompt_hash="h"))
        client.post("/feedback", json={"request_id": "rid1", "rating": 1})
        rates = telemetry._provider_winrates("cheap:rule:tiny_prompt")
        assert rates["anthropic"] == (1, 1.0)

    def test_thumbs_down_is_loss(self, client):
        from app import telemetry
        telemetry.record(telemetry.CallRecord(
            request_id="rid2", session_id="s", picked_tier="mid",
            picked_provider="openai", route_reason="classifier:score=3", prompt_hash="h"))
        client.post("/feedback", json={"request_id": "rid2", "rating": -1})
        rates = telemetry._provider_winrates("mid:classifier")
        assert rates["openai"] == (1, 0.0)

    def test_provider_scores_endpoint_groups_by_bucket(self, client):
        from app import telemetry
        for _ in range(3):
            telemetry.record_provider_outcome(bucket="mid:rule:x", provider="gemini", tier="mid", outcome="win")
        telemetry.record_provider_outcome(bucket="mid:rule:x", provider="anthropic", tier="mid", outcome="loss")
        body = client.get("/admin/provider_scores").json()
        assert "mid:rule:x" in body["buckets"]
        provs = body["buckets"]["mid:rule:x"]
        assert provs[0]["provider"] == "gemini" and provs[0]["win_rate_pct"] == 100.0

    def test_explorer_has_provider_panel(self, client):
        html = client.get("/admin/explorer").text
        assert "PROVIDER LEARNING" in html
        assert "renderProviderLearning" in html
