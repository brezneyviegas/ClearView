"""Plan/execute stage routing: frontier plans, local (ollama) executes.

Covers StagesCfg config, detect_stage() auto-detection, route(stage=...),
the ollama runtime health probe, and the local→cheap escalation fallback.
"""
from __future__ import annotations

import pytest

from app.config import Policy
from app.router import (
    _provider_available,
    build_availability,
    detect_stage,
    route,
)
from tests.conftest import MIN_POLICY_DICT


def _stage_policy(**stages) -> Policy:
    d = {**MIN_POLICY_DICT}
    d["tiers"] = {
        "local": ["ollama/llama3.2"],
        **MIN_POLICY_DICT["tiers"],
    }
    d["stages"] = {"enabled": True, "plan": "frontier", "execute": "local",
                   "auto_detect": True, **stages}
    return Policy(**d)


@pytest.fixture(autouse=True)
def _reset_availability():
    """Stage tests build availability themselves; restore the module global."""
    from app import router
    saved = dict(router._AVAILABLE)
    router._ollama_probe_cache = None
    yield
    router._AVAILABLE = saved
    router._ollama_probe_cache = None


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestStagesCfg:
    def test_default_disabled(self, policy):
        assert policy.stages.enabled is False

    def test_real_policy_has_stages_and_local_tier(self, real_policy):
        assert real_policy.stages.enabled is True
        assert real_policy.stages.plan == "frontier"
        assert real_policy.stages.execute == "local"
        assert "local" in real_policy.tiers
        assert real_policy.tiers["local"] == ["ollama/llama3.2"]


# ---------------------------------------------------------------------------
# detect_stage
# ---------------------------------------------------------------------------

class TestDetectStage:
    def test_disabled_returns_none(self, policy):
        msgs = [{"role": "tool", "content": "ok"}]
        assert detect_stage(msgs, "execute", policy) is None

    def test_header_plan_wins(self):
        pol = _stage_policy()
        assert detect_stage([{"role": "user", "content": "hi"}], "plan", pol) == "plan"

    def test_header_execute_wins(self):
        pol = _stage_policy()
        assert detect_stage([{"role": "user", "content": "hi"}], "execute", pol) == "execute"

    def test_header_case_insensitive(self):
        pol = _stage_policy()
        assert detect_stage([], "  PLAN ", pol) == "plan"

    def test_invalid_header_falls_through_to_auto(self):
        pol = _stage_policy()
        msgs = [{"role": "tool", "content": "result"}]
        assert detect_stage(msgs, "bogus", pol) == "execute"

    def test_tool_role_message_flags_execute(self):
        pol = _stage_policy()
        msgs = [
            {"role": "user", "content": "fix the bug"},
            {"role": "assistant", "content": "plan: 1. read file"},
            {"role": "tool", "content": "file contents..."},
        ]
        assert detect_stage(msgs, None, pol) == "execute"

    def test_assistant_tool_calls_flags_execute(self):
        pol = _stage_policy()
        msgs = [
            {"role": "user", "content": "fix the bug"},
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "x", "type": "function",
                             "function": {"name": "read", "arguments": "{}"}}]},
        ]
        assert detect_stage(msgs, None, pol) == "execute"

    def test_plain_conversation_is_not_a_stage(self):
        pol = _stage_policy()
        msgs = [{"role": "user", "content": "what is 2+2"}]
        assert detect_stage(msgs, None, pol) is None

    def test_auto_detect_off_needs_header(self):
        pol = _stage_policy(auto_detect=False)
        msgs = [{"role": "tool", "content": "result"}]
        assert detect_stage(msgs, None, pol) is None
        assert detect_stage(msgs, "execute", pol) == "execute"


# ---------------------------------------------------------------------------
# route(stage=...)
# ---------------------------------------------------------------------------

class TestStageRoute:
    def test_execute_routes_local(self):
        pol = _stage_policy()
        build_availability(pol)
        d = route("apply the diff to app/x.py", pol, stage="execute")
        assert d.tier == "local"
        assert d.model == "ollama/llama3.2"
        assert d.reason == "stage:execute"

    def test_plan_routes_frontier(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        pol = _stage_policy()
        build_availability(pol)
        d = route("hi", pol, stage="plan")
        assert d.tier == "frontier"
        assert d.reason == "stage:plan"

    def test_stage_beats_rules(self):
        # A tiny prompt would rule-route cheap; execute stage overrides.
        pol = _stage_policy()
        build_availability(pol)
        d = route("ok", pol, stage="execute")
        assert d.tier == "local"

    def test_header_tier_beats_stage(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        pol = _stage_policy()
        build_availability(pol)
        d = route("ok", pol, header_tier="mid", stage="execute")
        assert d.tier == "mid"
        assert d.reason == "rule:explicit_override"

    def test_stage_ignored_when_disabled(self):
        pol = _stage_policy(enabled=False)
        build_availability(pol)
        d = route("hello there", pol, stage="execute")
        assert d.reason != "stage:execute"

    def test_unknown_stage_tier_falls_through(self):
        pol = _stage_policy(execute="nonexistent_tier")
        build_availability(pol)
        d = route("hello there", pol, stage="execute")
        assert not d.reason.startswith("stage:")


# ---------------------------------------------------------------------------
# Ollama probe + fallback
# ---------------------------------------------------------------------------

class TestOllamaProbe:
    def test_probe_off_assumes_available(self, monkeypatch):
        monkeypatch.delenv("CLEARVIEW_OLLAMA_PROBE", raising=False)
        assert _provider_available("ollama/llama3.2") is True

    def test_probe_on_down_marks_unavailable(self, monkeypatch):
        from app import router
        monkeypatch.setenv("CLEARVIEW_OLLAMA_PROBE", "1")
        monkeypatch.setattr(router, "_ollama_up", lambda: False)
        assert _provider_available("ollama/llama3.2") is False

    def test_probe_on_up_marks_available(self, monkeypatch):
        from app import router
        monkeypatch.setenv("CLEARVIEW_OLLAMA_PROBE", "1")
        monkeypatch.setattr(router, "_ollama_up", lambda: True)
        assert _provider_available("ollama/llama3.2") is True

    def test_probe_result_is_cached(self, monkeypatch):
        from app import router
        calls = {"n": 0}

        def fake_urlopen(*a, **k):
            calls["n"] += 1
            raise OSError("down")

        import urllib.request
        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        router._ollama_probe_cache = None
        assert router._ollama_up() is False
        assert router._ollama_up() is False  # second hit served from cache
        assert calls["n"] == 1

    def test_execute_falls_back_to_cheap_when_ollama_down(self, monkeypatch):
        from app import router
        monkeypatch.setenv("CLEARVIEW_OLLAMA_PROBE", "1")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setattr(router, "_ollama_up", lambda: False)
        pol = _stage_policy()
        build_availability(pol)
        d = route("apply the diff", pol, stage="execute")
        assert d.tier == "local"          # the decision stays "execute → local"
        assert d.model == "openai/gpt-4o-mini"  # but the pick escalates to cheap


# ---------------------------------------------------------------------------
# End-to-end HTTP: /v1/chat/completions with stage routing (real policy.yaml)
# ---------------------------------------------------------------------------

def _patch_completion(monkeypatch, returns):
    from app import main
    from tests.conftest import FakeCompletion
    if callable(returns):
        monkeypatch.setattr(main.litellm, "completion", returns)
    else:
        monkeypatch.setattr(main.litellm, "completion", lambda **kw: returns)
    return FakeCompletion


class TestStageHTTP:
    def test_execute_header_routes_local(self, client, monkeypatch):
        from tests.conftest import FakeCompletion
        _patch_completion(monkeypatch, FakeCompletion("done, diff applied cleanly here"))
        r = client.post(
            "/v1/chat/completions",
            json={"model": "clearview-auto",
                  "messages": [{"role": "user", "content": "apply this diff"}]},
            headers={"x-clearview-stage": "execute"},
        )
        assert r.status_code == 200
        assert r.headers["x-clearview-tier"] == "local"
        assert r.headers["x-clearview-model"] == "ollama/llama3.2"

    def test_auto_detect_tool_history_routes_local(self, client, monkeypatch, tmp_db):
        import sqlite3
        from tests.conftest import FakeCompletion
        _patch_completion(monkeypatch, FakeCompletion("edit applied, moving to next step"))
        r = client.post(
            "/v1/chat/completions",
            json={"model": "clearview-auto",
                  "messages": [
                      {"role": "user", "content": "fix the bug in app/x.py"},
                      {"role": "assistant", "content": None,
                       "tool_calls": [{"id": "1", "type": "function",
                                       "function": {"name": "read_file",
                                                    "arguments": "{}"}}]},
                      {"role": "tool", "tool_call_id": "1", "content": "def f(): ..."},
                      {"role": "user", "content": "now apply the fix"},
                  ]},
        )
        assert r.status_code == 200
        assert r.headers["x-clearview-tier"] == "local"
        with sqlite3.connect(str(tmp_db)) as c:
            reason = c.execute(
                "SELECT route_reason FROM calls ORDER BY ts DESC LIMIT 1"
            ).fetchone()[0]
        assert reason.startswith("stage:execute")

    def test_plan_header_routes_frontier(self, client, monkeypatch):
        from tests.conftest import FakeCompletion
        _patch_completion(monkeypatch, FakeCompletion("plan: 1. read 2. patch 3. test"))
        r = client.post(
            "/v1/chat/completions",
            json={"model": "clearview-auto",
                  "messages": [{"role": "user", "content": "hi"}]},
            headers={"x-clearview-stage": "plan"},
        )
        assert r.status_code == 200
        assert r.headers["x-clearview-tier"] == "frontier"

    def test_tier_header_beats_stage_header(self, client, monkeypatch):
        from tests.conftest import FakeCompletion
        _patch_completion(monkeypatch, FakeCompletion("mid answer with plenty of words"))
        r = client.post(
            "/v1/chat/completions",
            json={"model": "clearview-auto",
                  "messages": [{"role": "user", "content": "hello"}]},
            headers={"x-clearview-stage": "execute", "x-clearview-tier": "mid"},
        )
        assert r.status_code == 200
        assert r.headers["x-clearview-tier"] == "mid"

    def test_weak_local_output_escalates(self, client, monkeypatch, tmp_db):
        """Quality gate: a refusal from the local execute model must retry one
        tier up (local → cheap) and flag quality_escalated."""
        import sqlite3
        from app import main
        from tests.conftest import FakeCompletion

        calls = []

        def fake_completion(**kw):
            calls.append(kw["model"])
            if kw["model"].startswith("ollama/"):
                return FakeCompletion("I can't help with that.")
            return FakeCompletion("patch applied, tests pass, here is the diff")

        monkeypatch.setattr(main.litellm, "completion", fake_completion)
        r = client.post(
            "/v1/chat/completions",
            json={"model": "clearview-auto",
                  "messages": [{"role": "user", "content": "apply the planned fix"}]},
            headers={"x-clearview-stage": "execute"},
        )
        assert r.status_code == 200
        assert calls[0] == "ollama/llama3.2"
        assert len(calls) >= 2                      # retried one tier up
        assert r.headers["x-clearview-tier"] == "cheap"
        with sqlite3.connect(str(tmp_db)) as c:
            reason = c.execute(
                "SELECT route_reason FROM calls ORDER BY ts DESC LIMIT 1"
            ).fetchone()[0]
        assert "quality_escalated" in reason
