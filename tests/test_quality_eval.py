"""LLM-as-judge quality eval tests.

Network-free: every litellm.completion call is monkey-patched. We don't
trust the judge model to be deterministic in production; here we control it
so we can assert aggregation math + gate logic.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from eval import quality_eval as Q


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resp(text: str) -> dict:
    """litellm-shaped response with `text` as the assistant content."""
    return {"choices": [{"message": {"content": text}, "index": 0, "finish_reason": "stop"}]}


def _patch_completion(monkeypatch, by_model: dict[str, str], default: str = ""):
    """Patch litellm.completion to return canned text per model id.

    `by_model` keys are exact model strings. Anything not in the map gets
    `default` so unexpected models don't silently look like a working call.
    """
    import litellm

    def fake(**kwargs):
        text = by_model.get(kwargs.get("model"), default)
        return _resp(text)

    monkeypatch.setattr(litellm, "completion", fake)


def _fixtures() -> list[dict]:
    return [
        {"id": "fa", "prompt": "what is 2+2?", "expected_tier": "cheap"},
        {"id": "fb", "prompt": "explain CAP theorem", "expected_tier": "mid"},
    ]


@pytest.fixture
def policy():
    from app.config import Policy
    return Policy(
        tiers={
            "cheap": ["openai/gpt-4o-mini"],
            "mid": ["anthropic/claude-sonnet-4-6"],
            "frontier": ["anthropic/claude-opus-4-7"],
        },
        rules=[
            {"name": "tiny_prompt",
             "if": {"tokens_lt": 200, "no_code": True},
             "then": "cheap"},
        ],
        classifier={
            "enabled": False,
            "model": "anthropic/claude-haiku-4-5",
            "prompt": "",
            "score_to_tier": {},
        },
        escalation={"on_error": False, "on_empty_response": False, "max_retries": 0},
        budget={"daily_usd_cap": 50.0, "on_breach": "reject"},
        baseline_model="anthropic/claude-opus-4-7",
    )


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------

class TestGrade:
    def test_grade_extracts_digit(self, monkeypatch):
        _patch_completion(monkeypatch, {"judge/x": "5"})
        assert Q._grade("judge/x", "p", "ref", "cand") == 5

    def test_grade_returns_3_when_unparseable(self, monkeypatch):
        _patch_completion(monkeypatch, {"judge/x": "no digit here"})
        assert Q._grade("judge/x", "p", "ref", "cand") == 3

    def test_grade_finds_digit_in_verbose_output(self, monkeypatch):
        _patch_completion(monkeypatch, {"judge/x": "I would say 4 because..."})
        assert Q._grade("judge/x", "p", "ref", "cand") == 4

    def test_completion_text_handles_litellm_failure(self, monkeypatch):
        import litellm
        def boom(**kw):
            raise RuntimeError("network down")
        monkeypatch.setattr(litellm, "completion", boom)
        assert Q._completion_text("any/model", "p") == ""


# ---------------------------------------------------------------------------
# run_quality
# ---------------------------------------------------------------------------

class TestRunQuality:
    def test_aggregates_avg_and_drift(self, monkeypatch, policy):
        # Routed model picked by router will be openai/gpt-4o-mini (cheap
        # tier via tiny_prompt). Baseline = anthropic/claude-opus-4-7.
        _patch_completion(monkeypatch, {
            "openai/gpt-4o-mini": "cheap-answer",
            "anthropic/claude-opus-4-7": "baseline-answer",
            # Judge calls go to baseline_model by default unless overridden.
        }, default="3")  # judge returns 3

        out = Q.run_quality(
            policy, _fixtures(),
            judge_model="anthropic/claude-opus-4-7",
        )

        # Two fixtures, judge always says 3 (set via default since the judge
        # request also hits baseline model id, which we mapped to "baseline-answer";
        # but _grade falls back to 3 when no digit in "baseline-answer").
        assert out["fixtures"] == 2
        assert out["avg_score"] == pytest.approx(3.0, abs=0.01)
        # drift = (5 - 3) / 5 * 100 = 40.0
        assert out["quality_drift_pct"] == pytest.approx(40.0, abs=0.01)
        # All scores below 4.0 floor.
        assert out["below_floor_count"] == 2

    def test_skips_when_routed_equals_baseline(self, monkeypatch, policy):
        # Force the routed model to BE the baseline so we expect skipping.
        _patch_completion(monkeypatch, {}, default="")

        out = Q.run_quality(
            policy,
            [{"id": "f1", "prompt": "x", "expected_tier": "frontier"}],
            judge_model="anthropic/claude-opus-4-7",
            routed_model_override="anthropic/claude-opus-4-7",
        )

        assert out["skipped_same_model"] == 1
        assert out["avg_score"] == 5.0
        assert out["quality_drift_pct"] == 0.0
        assert out["rows"][0]["skipped"] is True

    def test_perfect_scores_when_judge_always_5(self, monkeypatch, policy):
        # Map every model to a response whose text contains "5" so the judge
        # parses 5 regardless of who it grades.
        import litellm

        def fake(**kw):
            model = kw.get("model")
            # When the judge is called, return "5".
            if "messages" in kw and isinstance(kw["messages"], list):
                content = kw["messages"][0]["content"]
                if "SCORE (1-5):" in content:
                    return _resp("5")
            # Routed + baseline calls return distinct text so judge actually
            # has something to compare.
            return _resp(f"answer from {model}")

        monkeypatch.setattr(litellm, "completion", fake)

        out = Q.run_quality(
            policy, _fixtures(),
            judge_model="anthropic/claude-opus-4-7",
        )
        assert out["avg_score"] == pytest.approx(5.0)
        assert out["quality_drift_pct"] == pytest.approx(0.0)
        assert out["below_floor_count"] == 0


# ---------------------------------------------------------------------------
# Fixture filtering
# ---------------------------------------------------------------------------

class TestFilterFixtures:
    def test_returns_all_when_no_ids(self):
        fx = _fixtures()
        assert Q.filter_fixtures(fx, None) == fx
        assert Q.filter_fixtures(fx, []) == fx

    def test_filters_by_id(self):
        fx = _fixtures()
        out = Q.filter_fixtures(fx, ["fb"])
        assert [f["id"] for f in out] == ["fb"]

    def test_ignores_unknown_ids(self):
        out = Q.filter_fixtures(_fixtures(), ["unknown", "fa"])
        assert [f["id"] for f in out] == ["fa"]


# ---------------------------------------------------------------------------
# Gate logic
# ---------------------------------------------------------------------------

class TestGate:
    def test_quality_thresholds_pass_when_above_floor(self):
        from eval.run_eval import gate
        results = {
            "overall_accuracy_pct": 100.0,
            "rule_accuracy_pct": 100.0,
            "classifier_accuracy_pct": 0.0,
            "classifier_hit_count": 0,
            "native_total_usd": 0.0,
            "drift_pct": 90.0,
            "quality": {
                "avg_score": 4.5,
                "quality_drift_pct": 10.0,
            },
        }
        thresholds = {
            "min_overall_accuracy_pct": 80,
            "min_rule_accuracy_pct": 95,
            "min_avg_quality_score": 4.0,
            "max_quality_drift_pct": 20.0,
            "max_native_total_usd": 0.5,
            "min_drift_pct": 30,
        }
        ok, fails = gate(results, thresholds, live=True)
        assert ok, fails

    def test_quality_thresholds_fail_when_avg_low(self):
        from eval.run_eval import gate
        results = {
            "overall_accuracy_pct": 100.0,
            "rule_accuracy_pct": 100.0,
            "classifier_accuracy_pct": 0.0,
            "classifier_hit_count": 0,
            "native_total_usd": 0.0,
            "drift_pct": 90.0,
            "quality": {"avg_score": 3.0, "quality_drift_pct": 40.0},
        }
        thresholds = {
            "min_overall_accuracy_pct": 80,
            "min_rule_accuracy_pct": 95,
            "min_avg_quality_score": 4.0,
            "max_quality_drift_pct": 20.0,
        }
        ok, fails = gate(results, thresholds, live=True)
        assert not ok
        assert any("avg_quality_score" in f for f in fails)
        assert any("quality_drift_pct" in f for f in fails)

    def test_quality_thresholds_skipped_when_no_quality_block(self):
        """Existing eval runs that don't pass --quality must still pass the
        gate (the quality keys in gate.json are silently ignored)."""
        from eval.run_eval import gate
        results = {
            "overall_accuracy_pct": 100.0,
            "rule_accuracy_pct": 100.0,
            "classifier_accuracy_pct": 0.0,
            "classifier_hit_count": 0,
            "native_total_usd": 0.0,
            "drift_pct": 90.0,
        }
        thresholds = {
            "min_overall_accuracy_pct": 80,
            "min_rule_accuracy_pct": 95,
            "min_avg_quality_score": 4.0,
            "max_quality_drift_pct": 20.0,
            "max_native_total_usd": 0.5,
            "min_drift_pct": 30,
        }
        ok, fails = gate(results, thresholds, live=True)
        assert ok, fails
