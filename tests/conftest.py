"""Shared fixtures for the ClearView test suite.

All tests run with a fresh sqlite db, no network. Fixtures here are the only
place tests should reach for: a Policy, a TestClient, or a tmp DB path.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# DB isolation
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """Point CLEARVIEW_DB_PATH at a per-test sqlite file and re-init the schema.

    Autouse so even tests that never import telemetry don't accidentally write
    to the repo's clearview.db.
    """
    db_file = tmp_path / "clearview-test.db"
    monkeypatch.setenv("CLEARVIEW_DB_PATH", str(db_file))

    # Late import so the env var is visible when telemetry resolves db_path().
    from app import cache, telemetry
    telemetry.init_db()
    cache.init_db()
    yield db_file


# ---------------------------------------------------------------------------
# Policy fixtures
# ---------------------------------------------------------------------------

MIN_POLICY_DICT = {
    "tiers": {
        "cheap": ["openai/gpt-4o-mini", "ollama/qwen2.5"],
        "mid": ["openai/gpt-4o"],
        "frontier": ["anthropic/claude-opus-4-7"],
    },
    "rules": [
        {"name": "explicit_override",
         "if": {"header": "x-clearview-tier"},
         "then": "header_value"},
        {"name": "long_prompt",
         "if": {"tokens_gte": 4000},
         "then": "frontier"},
        {"name": "stack_trace",
         "if": {"stack_trace": True},
         "then": "mid"},
        {"name": "math_or_proof",
         "if": {"math_symbols": True},
         "then": "mid"},
        {"name": "url_context",
         "if": {"url": True},
         "then": "mid"},
        {"name": "code_context",
         "if": {"file_path": True},
         "then": "mid"},
        {"name": "unfenced_multiline_code",
         "if": {"multiline_code_no_fence": True},
         "then": "mid"},
        {"name": "tiny_prompt",
         "if": {"tokens_lt": 200, "no_code": True},
         "then": "cheap"},
        {"name": "complex_keywords",
         "if": {"contains_any": ["refactor", "architect"]},
         "then": "mid"},
        {"name": "imperative_work",
         "if": {"imperative": True, "tokens_gte": 200},
         "then": "mid"},
    ],
    "classifier": {
        "enabled": True,
        "model": "openai/gpt-4o-mini",
        "prompt": "Rate 1-5: {prompt}",
        "confidence_floor": 0.65,
        "score_to_tier": {1: "cheap", 2: "cheap", 3: "mid", 4: "mid", 5: "frontier"},
    },
    "escalation": {"on_error": True, "on_empty_response": True, "max_retries": 1},
    "budget": {"daily_usd_cap": 50.0, "on_breach": "reject"},
    "baseline_model": "anthropic/claude-opus-4-7",
}


@pytest.fixture
def policy():
    """A minimal Policy that doesn't depend on the on-disk policy.yaml."""
    from app.config import Policy
    return Policy(**MIN_POLICY_DICT)


@pytest.fixture
def real_policy():
    """The actual policy.yaml — useful for integration smoke tests."""
    from app.config import load_policy
    repo_root = Path(__file__).resolve().parent.parent
    return load_policy(str(repo_root / "policy.yaml"))


# ---------------------------------------------------------------------------
# Fake litellm response helpers
# ---------------------------------------------------------------------------

class _FakeUsage:
    def __init__(self, prompt_tokens: int, completion_tokens: int):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens

    def model_dump(self):
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
        }


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content
        self.role = "assistant"


class _FakeChoice:
    def __init__(self, content: str):
        self.message = _FakeMessage(content)
        self.index = 0
        self.finish_reason = "stop"


class FakeCompletion:
    """Stand-in for a litellm ModelResponse that supports both attr and dict-style access."""

    def __init__(self, content: str = "hello", prompt_tokens: int = 5, completion_tokens: int = 7):
        self.choices = [_FakeChoice(content)]
        self.usage = _FakeUsage(prompt_tokens, completion_tokens)
        self.id = "fake-id"
        self.model = "fake-model"
        self.object = "chat.completion"

    def model_dump(self):
        return {
            "id": self.id,
            "object": self.object,
            "model": self.model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": self.choices[0].message.content},
                "finish_reason": "stop",
            }],
            "usage": self.usage.model_dump(),
        }


@pytest.fixture
def fake_completion_factory():
    return FakeCompletion


# ---------------------------------------------------------------------------
# TestClient
# ---------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch, tmp_db):
    """FastAPI TestClient with the lifespan context exercised.

    We force CLEARVIEW_POLICY_PATH at the actual repo policy.yaml so app.main
    can boot without us having to write a temp file.
    """
    repo_root = Path(__file__).resolve().parent.parent
    monkeypatch.setenv("CLEARVIEW_POLICY_PATH", str(repo_root / "policy.yaml"))
    # Provider calls are monkeypatched in tests. Fake keys keep startup
    # availability aligned with the configured policy without hitting network.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("GEMINI_API_KEY", "test")

    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as c:
        yield c
