"""Unit tests for app.pricing."""
from __future__ import annotations

import pytest

from app import pricing
from app.pricing import cost_for, cost_per_1k_out, drift_pct


class TestCostFor:
    def test_unknown_model_falls_back_to_conservative_estimate(self, monkeypatch):
        # Force litellm to raise so we hit the fallback path.
        def _boom(**_kw):
            raise RuntimeError("model not in pricing table")

        monkeypatch.setattr(pricing.litellm, "completion_cost", _boom)
        # 1M in, 1M out → $5 + $15 = $20.
        cost = cost_for("totally/unknown-model", 1_000_000, 1_000_000)
        assert cost == pytest.approx(20.0)

    def test_unknown_model_zero_tokens(self, monkeypatch):
        monkeypatch.setattr(pricing.litellm, "completion_cost",
                            lambda **kw: (_ for _ in ()).throw(RuntimeError("nope")))
        assert cost_for("totally/unknown-model", 0, 0) == 0.0

    def test_known_model_uses_litellm(self, monkeypatch):
        monkeypatch.setattr(pricing.litellm, "completion_cost",
                            lambda **kw: 0.1234)
        assert cost_for("openai/gpt-4o-mini", 100, 50) == pytest.approx(0.1234)

    def test_ollama_models_are_free(self, monkeypatch):
        # Even if the pricing table would charge, ollama/* should short-circuit to 0.
        called = {"n": 0}

        def _track(**_kw):
            called["n"] += 1
            return 9.99

        monkeypatch.setattr(pricing.litellm, "completion_cost", _track)
        assert cost_for("ollama/qwen2.5", 1000, 1000) == 0.0
        assert called["n"] == 0  # confirms we didn't even call litellm

    def test_ollama_chat_prefix_is_free(self, monkeypatch):
        monkeypatch.setattr(pricing.litellm, "completion_cost",
                            lambda **kw: 9.99)
        assert cost_for("ollama_chat/llama3", 500, 500) == 0.0


class TestCostPer1kOut:
    def test_zero_tokens_out_returns_zero(self):
        # Guard against div-by-zero when an upstream call produced no output.
        assert cost_per_1k_out(0.5, 0) == 0.0

    def test_negative_tokens_returns_zero(self):
        assert cost_per_1k_out(0.5, -10) == 0.0

    def test_normal_case(self):
        # $0.10 native cost over 500 tokens out → $0.20 / 1k.
        assert cost_per_1k_out(0.10, 500) == pytest.approx(0.20)

    def test_thousand_tokens(self):
        assert cost_per_1k_out(0.05, 1000) == pytest.approx(0.05)


class TestDriftPct:
    def test_zero_baseline_returns_zero(self):
        # No baseline → no drift to report.
        assert drift_pct(0.5, 0.0) == 0.0

    def test_negative_baseline_returns_zero(self):
        assert drift_pct(0.5, -1.0) == 0.0

    def test_savings_positive(self):
        # Native $1, baseline $4 → saved 75%.
        assert drift_pct(1.0, 4.0) == pytest.approx(75.0)

    def test_no_savings(self):
        assert drift_pct(1.0, 1.0) == pytest.approx(0.0)

    def test_overspend_negative_drift(self):
        # Native $5, baseline $4 → -25% (we spent more than the plan).
        assert drift_pct(5.0, 4.0) == pytest.approx(-25.0)
