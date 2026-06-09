"""Scenario-based routing tests.

Each test uses a realistic prompt and asserts the expected tier AND the exact
model that should be selected from the real policy.yaml.  Tests act as a
living contract: if policy.yaml changes routing or swaps a model, these break
and force a conscious update.
"""
from __future__ import annotations

import pytest

from app.router import RouteDecision, route


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CHEAP_MODEL = "anthropic/claude-haiku-4-5"
MID_MODEL = "anthropic/claude-sonnet-4-6"
FRONTIER_MODEL = "anthropic/claude-opus-4-8"


def _route(prompt: str, *, header: str | None = None, policy) -> RouteDecision:
    return route(prompt, policy, header_tier=header)


# ---------------------------------------------------------------------------
# Cheap tier — trivial / short prompts
# ---------------------------------------------------------------------------

class TestCheapRouting:
    def test_simple_greeting(self, real_policy):
        d = _route("hi", policy=real_policy)
        assert d.tier == "cheap"
        assert d.model == CHEAP_MODEL
        assert "tiny_prompt" in d.reason

    def test_one_liner_question(self, real_policy):
        d = _route("What is the capital of France?", policy=real_policy)
        assert d.tier == "cheap"
        assert d.model == CHEAP_MODEL

    def test_rename_request(self, real_policy):
        d = _route("Rename the variable foo to bar.", policy=real_policy)
        assert d.tier == "cheap"
        assert d.model == CHEAP_MODEL

    def test_medium_prose_no_code(self, real_policy):
        # ~400 chars, no code, no special signals → medium_prompt rule
        prompt = "Please summarise this paragraph: " + ("lorem ipsum " * 30)
        d = _route(prompt, policy=real_policy)
        assert d.tier == "cheap"
        assert d.model == CHEAP_MODEL

    def test_explicit_cheap_override(self, real_policy):
        d = _route("complex long prompt " * 200, policy=real_policy, header="cheap")
        assert d.tier == "cheap"
        assert d.model == CHEAP_MODEL
        assert d.reason == "rule:explicit_override"


# ---------------------------------------------------------------------------
# Mid tier — structural signals
# ---------------------------------------------------------------------------

class TestMidRouting:
    def test_stack_trace(self, real_policy):
        prompt = (
            'Traceback (most recent call last):\n'
            '  File "app/main.py", line 42, in handler\n'
            '    result = process(data)\n'
            'ValueError: invalid literal for int()\n'
            'How do I fix this?'
        )
        d = _route(prompt, policy=real_policy)
        assert d.tier == "mid"
        assert d.model == MID_MODEL
        assert d.reason == "rule:stack_trace"

    def test_math_proof(self, real_policy):
        d = _route("Prove that x^2 + y^2 = z^2 has no integer solutions for n > 2.", policy=real_policy)
        assert d.tier == "mid"
        assert d.model == MID_MODEL
        assert d.reason == "rule:math_or_proof"

    def test_url_context(self, real_policy):
        d = _route("Summarise the content at https://example.com/api-docs", policy=real_policy)
        assert d.tier == "mid"
        assert d.model == MID_MODEL
        assert d.reason == "rule:url_context"

    def test_file_path_context(self, real_policy):
        d = _route("Fix the bug in app/router.py", policy=real_policy)
        assert d.tier == "mid"
        assert d.model == MID_MODEL
        assert d.reason == "rule:code_context"

    def test_unfenced_python_function(self, real_policy):
        prompt = "def calculate_total(items):\n    return sum(i.price for i in items)"
        d = _route(prompt, policy=real_policy)
        assert d.tier == "mid"
        assert d.model == MID_MODEL
        assert d.reason == "rule:unfenced_multiline_code"

    def test_refactor_keyword(self, real_policy):
        # Need >200 tokens worth of chars so tiny_prompt doesn't fire first.
        prompt = "Please refactor this module to use dependency injection. " + ("context " * 100)
        d = _route(prompt, policy=real_policy)
        assert d.tier == "mid"
        assert d.model == MID_MODEL
        assert "complex_keywords" in d.reason

    def test_architect_keyword(self, real_policy):
        prompt = "Help me architect a scalable microservices system. " + ("details " * 100)
        d = _route(prompt, policy=real_policy)
        assert d.tier == "mid"
        assert d.model == MID_MODEL

    def test_prove_keyword(self, real_policy):
        # Needs >200 tokens so tiny_prompt doesn't fire before complex_keywords.
        prompt = "Can you prove this algorithm terminates in O(n log n)? " + ("context " * 120)
        d = _route(prompt, policy=real_policy)
        assert d.tier == "mid"
        assert d.model == MID_MODEL

    def test_explicit_mid_override(self, real_policy):
        d = _route("hi", policy=real_policy, header="mid")
        assert d.tier == "mid"
        assert d.model == MID_MODEL
        assert d.reason == "rule:explicit_override"


# ---------------------------------------------------------------------------
# Frontier tier — long prompts + override
# ---------------------------------------------------------------------------

class TestFrontierRouting:
    def test_very_long_prompt(self, real_policy):
        # >4000 tokens (~16000 chars at 4 chars/token)
        prompt = "Analyse this codebase: " + ("x " * 8500)
        d = _route(prompt, policy=real_policy)
        assert d.tier == "frontier"
        assert d.model == FRONTIER_MODEL
        assert "long_prompt" in d.reason

    def test_explicit_frontier_override(self, real_policy):
        d = _route("hi", policy=real_policy, header="frontier")
        assert d.tier == "frontier"
        assert d.model == FRONTIER_MODEL
        assert d.reason == "rule:explicit_override"


# ---------------------------------------------------------------------------
# Model identity — verify exact model strings in policy
# ---------------------------------------------------------------------------

class TestModelIdentity:
    """Assert the first model in each tier matches the expected string.

    These break intentionally when policy.yaml model names change, forcing a
    conscious decision rather than a silent drift.
    """

    def test_cheap_first_model(self, real_policy):
        assert real_policy.tiers["cheap"][0] == CHEAP_MODEL

    def test_mid_first_model(self, real_policy):
        assert real_policy.tiers["mid"][0] == MID_MODEL

    def test_frontier_first_model(self, real_policy):
        assert real_policy.tiers["frontier"][0] == FRONTIER_MODEL

    def test_no_legacy_gemini_15(self, real_policy):
        all_models = [m for tier in real_policy.tiers.values() for m in tier]
        legacy = [m for m in all_models if "1.5" in m]
        assert legacy == [], f"Legacy Gemini 1.5 models still in policy: {legacy}"

    def test_no_legacy_opus_47(self, real_policy):
        all_models = [m for tier in real_policy.tiers.values() for m in tier]
        old = [m for m in all_models if "opus-4-7" in m]
        assert old == [], f"Stale opus-4-7 still in policy: {old}"
