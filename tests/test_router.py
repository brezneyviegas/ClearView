"""Unit tests for app.router.

Covers the rule predicate (_eval_rule), the model picker, and the top-level
route() function over the rule layer + classifier fallback.
"""
from __future__ import annotations

import pytest

from app import router
from app.router import RouteDecision, _eval_rule, _pick_model, route


# ---------------------------------------------------------------------------
# _eval_rule
# ---------------------------------------------------------------------------

class TestEvalRule:
    def test_tokens_lt_true(self):
        # ~5 tokens (20 chars / 4)
        assert _eval_rule({"tokens_lt": 200}, "hi there friend", None) is True

    def test_tokens_lt_false_when_too_long(self):
        long_prompt = "x" * 1000  # ~250 tokens
        assert _eval_rule({"tokens_lt": 200}, long_prompt, None) is False

    def test_tokens_gte_true(self):
        long_prompt = "x" * 16001  # ~4000 tokens
        assert _eval_rule({"tokens_gte": 4000}, long_prompt, None) is True

    def test_tokens_gte_false(self):
        assert _eval_rule({"tokens_gte": 4000}, "short", None) is False

    def test_no_code_true_when_no_fence(self):
        assert _eval_rule({"no_code": True}, "just text", None) is True

    def test_no_code_false_with_code_fence(self):
        prompt = "explain ```python\nprint(1)\n```"
        assert _eval_rule({"no_code": True}, prompt, None) is False

    def test_no_code_falsy_skips_check(self):
        # When no_code is False/missing, the rule shouldn't reject prompts with fences.
        prompt = "```code```"
        assert _eval_rule({"no_code": False}, prompt, None) is True

    def test_contains_any_match(self):
        assert _eval_rule(
            {"contains_any": ["refactor", "architect"]},
            "please refactor this for me",
            None,
        ) is True

    def test_contains_any_case_insensitive(self):
        assert _eval_rule(
            {"contains_any": ["REFACTOR"]}, "Refactor this", None
        ) is True

    def test_contains_any_no_match(self):
        assert _eval_rule(
            {"contains_any": ["refactor"]}, "hello there", None
        ) is False

    def test_header_present(self):
        assert _eval_rule({"header": "x-clearview-tier"}, "anything", "mid") is True

    def test_header_absent(self):
        assert _eval_rule({"header": "x-clearview-tier"}, "anything", None) is False

    def test_combined_conditions_all_true(self):
        assert _eval_rule(
            {"tokens_lt": 200, "no_code": True}, "tiny prompt", None
        ) is True

    def test_combined_conditions_one_fails(self):
        assert _eval_rule(
            {"tokens_lt": 200, "no_code": True}, "```code```", None
        ) is False


# ---------------------------------------------------------------------------
# _pick_model
# ---------------------------------------------------------------------------

class TestPickModel:
    def test_returns_first_in_tier(self, policy):
        assert _pick_model("cheap", policy) == "openai/gpt-4o-mini"
        assert _pick_model("mid", policy) == "openai/gpt-4o"
        assert _pick_model("frontier", policy) == "anthropic/claude-opus-4-7"

    def test_unknown_tier_falls_back_to_cheap(self, policy):
        assert _pick_model("nonexistent", policy) == "openai/gpt-4o-mini"


# ---------------------------------------------------------------------------
# route()
# ---------------------------------------------------------------------------

class TestRoute:
    def test_tiny_prompt_routes_cheap(self, policy):
        decision = route("hi there", policy)
        assert decision.tier == "cheap"
        assert decision.model == "openai/gpt-4o-mini"
        assert decision.reason.startswith("rule:")
        assert "tiny_prompt" in decision.reason

    def test_complex_keyword_routes_mid(self, policy):
        # Need >=200 tokens (>=800 chars approx) to skip tiny_prompt rule, and
        # must contain "refactor" so complex_keywords matches.
        prompt = "please refactor this codebase " + ("x " * 800)
        decision = route(prompt, policy)
        assert decision.tier == "mid"
        assert "complex_keywords" in decision.reason

    def test_header_override_valid_tier(self, policy):
        decision = route("any prompt", policy, header_tier="frontier")
        assert decision.tier == "frontier"
        assert decision.reason == "rule:explicit_override"

    def test_header_override_invalid_tier_falls_through(self, policy):
        # Invalid header tier means the explicit_override rule's tier check fails;
        # subsequent rules apply. "tiny_prompt" should win for short input.
        decision = route("hi", policy, header_tier="bogus")
        assert decision.tier == "cheap"
        # Reason should NOT be explicit_override since the tier wasn't valid.
        assert "explicit_override" not in decision.reason

    def test_long_prompt_routes_frontier(self, policy):
        prompt = "x" * 16001  # ~4000 tokens
        decision = route(prompt, policy)
        assert decision.tier == "frontier"
        assert "long_prompt" in decision.reason

    def test_classifier_fallback(self, policy, monkeypatch):
        """When no rule matches, the classifier kicks in. Mock it to return '4'."""
        # Build a prompt that escapes all rules:
        #   - has a code fence so tiny_prompt fails
        #   - no keywords
        #   - not >= 4000 tokens
        prompt = "```\nfoo\n```\n" + ("word " * 100)

        class _Resp:
            def __getitem__(self, k):
                return {"choices": [{"message": {"content": "4"}}]}[k]

        monkeypatch.setattr(router.litellm, "completion", lambda **kw: _Resp())
        decision = route(prompt, policy)
        assert decision.tier == "mid"  # score 4 → mid
        assert decision.reason == "classifier:score=4"

    def test_classifier_failure_defaults_to_3(self, policy, monkeypatch):
        prompt = "```\nfoo\n```\n" + ("word " * 100)

        def _boom(**_kw):
            raise RuntimeError("network down")

        monkeypatch.setattr(router.litellm, "completion", _boom)
        decision = route(prompt, policy)
        # _classify returns 3 on exception → "mid".
        assert decision.tier == "mid"
        assert decision.reason == "classifier:score=3"

    def test_classifier_clamps_out_of_range_digits(self, policy, monkeypatch):
        prompt = "```\nfoo\n```\n" + ("word " * 100)

        class _Resp:
            def __getitem__(self, k):
                return {"choices": [{"message": {"content": "9"}}]}[k]

        monkeypatch.setattr(router.litellm, "completion", lambda **kw: _Resp())
        decision = route(prompt, policy)
        # 9 clamps to 5 → frontier.
        assert decision.tier == "frontier"
        assert decision.reason == "classifier:score=5"

    def test_default_when_classifier_disabled(self, policy, monkeypatch):
        # Disable classifier and feed a prompt with no rule match.
        policy.classifier.enabled = False
        # Drop all rules so nothing matches.
        policy.rules = []
        decision = route("anything", policy)
        assert decision.tier == "cheap"
        assert decision.reason == "default:cheap"


def test_routedecision_dataclass_fields():
    d = RouteDecision(tier="cheap", model="x", reason="rule:foo")
    assert d.tier == "cheap"
    assert d.model == "x"
    assert d.reason == "rule:foo"
