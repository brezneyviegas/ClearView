"""End-to-end API tests against the FastAPI app via TestClient.

Every litellm.completion call is monkey-patched. No network. No real keys.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

# Import the test helper from conftest. conftest is auto-discovered by pytest;
# this direct import works because tests/__init__.py makes "tests" a package.
from tests.conftest import FakeCompletion  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _patch_completion(monkeypatch, returns):
    """Patch the litellm.completion symbol that app.main imported.

    `returns` is either a callable(**kwargs) -> response, or a static value
    (we wrap it).
    """
    from app import main
    if callable(returns):
        monkeypatch.setattr(main.litellm, "completion", returns)
    else:
        monkeypatch.setattr(main.litellm, "completion", lambda **kw: returns)


def _count_rows(db_path):
    with sqlite3.connect(str(db_path)) as c:
        return c.execute("SELECT COUNT(*) FROM calls").fetchone()[0]


def _all_rows(db_path):
    with sqlite3.connect(str(db_path)) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute("SELECT * FROM calls").fetchall()]


# ---------------------------------------------------------------------------
# Trivial endpoints
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


class TestModels:
    def test_lists_virtual_models(self, client):
        r = client.get("/v1/models")
        assert r.status_code == 200
        body = r.json()
        ids = {m["id"] for m in body["data"]}
        assert {"clearview-auto", "clearview-cheap", "clearview-mid",
                "clearview-frontier"}.issubset(ids)

    def test_includes_underlying_models(self, client):
        r = client.get("/v1/models")
        ids = {m["id"] for m in r.json()["data"]}
        # At least one underlying provider model from policy.yaml.
        assert any("/" in i for i in ids)


# ---------------------------------------------------------------------------
# Chat completions: non-streaming
# ---------------------------------------------------------------------------

class TestChatCompletionsBasic:
    def test_missing_messages_returns_400(self, client, monkeypatch):
        _patch_completion(monkeypatch, FakeCompletion())
        r = client.post("/v1/chat/completions", json={})
        assert r.status_code == 400

    def test_basic_call_returns_payload(self, client, monkeypatch, tmp_db):
        captured = {}

        def _fake(**kw):
            captured.update(kw)
            return FakeCompletion(content="hello back", prompt_tokens=3, completion_tokens=2)

        _patch_completion(monkeypatch, _fake)

        r = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 200
        body = r.json()
        # Forwarded payload includes choices/usage.
        assert body["choices"][0]["message"]["content"] == "hello back"
        assert body["usage"]["prompt_tokens"] == 3
        # Telemetry row written.
        assert _count_rows(tmp_db) == 1
        # The forwarded model is the picked one, NOT "clearview-auto".
        assert not captured["model"].startswith("clearview-")

    def test_telemetry_records_provider_and_tokens(self, client, monkeypatch, tmp_db):
        _patch_completion(monkeypatch,
                          FakeCompletion(prompt_tokens=11, completion_tokens=22))
        r = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers={"x-clearview-session": "sess1"},
        )
        assert r.status_code == 200
        rows = _all_rows(tmp_db)
        assert len(rows) == 1
        row = rows[0]
        assert row["session_id"] == "sess1"
        assert row["tokens_in"] == 11
        assert row["tokens_out"] == 22
        assert row["picked_provider"] in {"anthropic", "openai", "gemini", "ollama"}


class TestVirtualModelTierForcing:
    def test_clearview_cheap_forces_cheap_tier(self, client, monkeypatch, tmp_db):
        captured = {}

        def _fake(**kw):
            captured.update(kw)
            return FakeCompletion()

        _patch_completion(monkeypatch, _fake)

        # Use a prompt that would otherwise route mid (contains "refactor"):
        # virtual model tier override should win.
        r = client.post(
            "/v1/chat/completions",
            json={
                "model": "clearview-cheap",
                "messages": [{"role": "user", "content": "please refactor everything " * 100}],
            },
        )
        assert r.status_code == 200
        # The picked model must come from the cheap tier.
        from app.main import POLICY
        assert captured["model"] in POLICY.tiers["cheap"]
        # Telemetry route_reason flags the virtual model.
        rows = _all_rows(tmp_db)
        assert "virtual_model:clearview-cheap" in rows[0]["route_reason"]

    def test_unknown_virtual_tier_falls_through_to_router(self, client, monkeypatch):
        captured = {}

        def _fake(**kw):
            captured.update(kw)
            return FakeCompletion()

        _patch_completion(monkeypatch, _fake)

        r = client.post(
            "/v1/chat/completions",
            json={
                "model": "clearview-bogus",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        assert r.status_code == 200


class TestHeaderTierOverride:
    def test_x_clearview_tier_mid_honored(self, client, monkeypatch):
        captured = {}

        def _fake(**kw):
            captured.update(kw)
            return FakeCompletion()

        _patch_completion(monkeypatch, _fake)

        r = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "tiny"}]},
            headers={"x-clearview-tier": "mid"},
        )
        assert r.status_code == 200
        from app.main import POLICY
        assert captured["model"] in POLICY.tiers["mid"]


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------

class _FakeStreamChunk:
    def __init__(self, content: str = "", usage: dict | None = None):
        self._d = {
            "choices": [{"delta": {"content": content}, "index": 0}],
        }
        if usage is not None:
            self._d["usage"] = usage

    def model_dump(self):
        return self._d


class TestStreaming:
    def test_stream_emits_sse(self, client, monkeypatch, tmp_db):
        chunks = [
            _FakeStreamChunk("Hel"),
            _FakeStreamChunk("lo"),
            _FakeStreamChunk("", usage={"prompt_tokens": 4, "completion_tokens": 2}),
        ]
        _patch_completion(monkeypatch, iter(chunks))

        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}], "stream": True},
        ) as r:
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/event-stream")
            body = b"".join(r.iter_bytes()).decode()

        assert "data: " in body
        assert "[DONE]" in body
        # First chunk JSON should be parseable.
        first_data_line = next(
            line for line in body.splitlines()
            if line.startswith("data: ") and "[DONE]" not in line
        )
        json.loads(first_data_line[len("data: "):])

        # Telemetry row written after stream completes.
        assert _count_rows(tmp_db) == 1

    def test_streaming_shadow_pairs_to_primary_request(self, client, monkeypatch):
        from app import main
        seen = []

        async def _fake_shadow(**kw):
            seen.append(kw)

        chunks = [
            _FakeStreamChunk("Hi"),
            _FakeStreamChunk("", usage={"prompt_tokens": 4, "completion_tokens": 1}),
        ]
        _patch_completion(monkeypatch, iter(chunks))
        monkeypatch.setattr(main, "_run_shadow", _fake_shadow)

        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}], "stream": True},
            headers={"x-clearview-shadow": "frontier"},
        ) as r:
            assert r.status_code == 200
            request_id = r.headers["x-clearview-request-id"]
            _body = b"".join(r.iter_bytes())

        assert seen
        assert seen[0]["primary_request_id"] == request_id
        assert seen[0]["shadow_tier"] == "frontier"


class TestCompatibilityShims:
    def test_anthropic_messages_returns_anthropic_shape(self, client, monkeypatch, tmp_db):
        _patch_completion(
            monkeypatch,
            FakeCompletion(content="claude-shaped", prompt_tokens=9, completion_tokens=4),
        )

        r = client.post(
            "/v1/messages",
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 64,
                "messages": [{"role": "user", "content": "hi from claude"}],
            },
        )

        assert r.status_code == 200
        body = r.json()
        assert body["type"] == "message"
        assert body["role"] == "assistant"
        assert body["content"] == [{"type": "text", "text": "claude-shaped"}]
        assert body["usage"] == {"input_tokens": 9, "output_tokens": 4}
        assert _count_rows(tmp_db) == 1

    def test_openai_responses_returns_responses_shape(self, client, monkeypatch, tmp_db):
        _patch_completion(
            monkeypatch,
            FakeCompletion(content="responses-shaped", prompt_tokens=5, completion_tokens=3),
        )

        r = client.post(
            "/v1/responses",
            json={"model": "gpt-5.4", "input": "hi from codex", "max_output_tokens": 32},
        )

        assert r.status_code == 200
        body = r.json()
        assert body["object"] == "response"
        assert body["status"] == "completed"
        assert body["output_text"] == "responses-shaped"
        assert body["usage"]["input_tokens"] == 5
        assert body["usage"]["output_tokens"] == 3
        assert _count_rows(tmp_db) == 1

    def test_gemini_generate_content_returns_gemini_shape(self, client, monkeypatch, tmp_db):
        _patch_completion(
            monkeypatch,
            FakeCompletion(content="gemini-shaped", prompt_tokens=6, completion_tokens=2),
        )

        r = client.post(
            "/v1beta/models/gemini-1.5-pro:generateContent",
            json={
                "contents": [{"role": "user", "parts": [{"text": "hi from gemini"}]}],
                "generationConfig": {"maxOutputTokens": 32},
            },
        )

        assert r.status_code == 200
        body = r.json()
        assert body["candidates"][0]["content"]["role"] == "model"
        assert body["candidates"][0]["content"]["parts"] == [{"text": "gemini-shaped"}]
        assert body["usageMetadata"]["promptTokenCount"] == 6
        assert body["usageMetadata"]["candidatesTokenCount"] == 2
        assert _count_rows(tmp_db) == 1


# ---------------------------------------------------------------------------
# 502 / error path
# ---------------------------------------------------------------------------

class TestUpstreamErrors:
    def test_frontier_failure_returns_502_when_mock_fallback_disabled(
            self, client, monkeypatch, tmp_db):
        # CLEARVIEW_MOCK_ON_FAILURE=0 restores the hard-fail behaviour: a
        # frontier failure with no escalation target surfaces a 502.
        monkeypatch.setenv("CLEARVIEW_MOCK_ON_FAILURE", "0")

        def _boom(**_kw):
            raise RuntimeError("upstream blew up")

        _patch_completion(monkeypatch, _boom)

        r = client.post(
            "/v1/chat/completions",
            json={
                "model": "clearview-frontier",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        assert r.status_code == 502
        assert "upstream error" in r.json()["detail"]
        # A failure row should still be logged.
        rows = _all_rows(tmp_db)
        assert len(rows) == 1
        assert rows[0]["status"].startswith("error:")

    def test_frontier_failure_degrades_to_mock_by_default(self, client, monkeypatch, tmp_db):
        # Default behaviour: never hard-fail for lack of a backend — serve the
        # built-in mock instead of a 502.
        def _boom(**_kw):
            raise RuntimeError("upstream blew up")

        _patch_completion(monkeypatch, _boom)

        r = client.post(
            "/v1/chat/completions",
            json={
                "model": "clearview-frontier",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        assert r.status_code == 200
        assert r.json()["model"] == "mock/echo"
        assert "mock provider" in r.json()["choices"][0]["message"]["content"]

    def test_escalation_on_cheap_failure(self, client, monkeypatch, tmp_db):
        """Cheap failure → escalate to frontier; frontier succeeds."""
        from app.main import POLICY

        cheap_models = set(POLICY.tiers["cheap"])
        frontier_first = POLICY.tiers["frontier"][0]
        calls = []

        def _flaky(**kw):
            calls.append(kw["model"])
            if kw["model"] in cheap_models:
                raise RuntimeError("cheap is down")
            return FakeCompletion(content="rescued")

        _patch_completion(monkeypatch, _flaky)

        # Tiny prompt → routes cheap.
        r = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 200
        assert r.json()["choices"][0]["message"]["content"] == "rescued"
        assert calls[0] in cheap_models
        assert calls[-1] == frontier_first
        rows = _all_rows(tmp_db)
        assert rows[0]["escalated"] == 1
        assert ";escalated" in rows[0]["route_reason"]

    def test_refusal_response_escalates_one_tier(self, client, monkeypatch, tmp_db):
        from app.main import POLICY

        monkeypatch.setenv("CLEARVIEW_ROUTING_QUALITY", "0")
        cheap_models = set(POLICY.tiers["cheap"])
        mid_first = POLICY.tiers["mid"][0]
        calls = []

        def _refusal_then_ok(**kw):
            calls.append(kw["model"])
            if kw["model"] in cheap_models:
                return FakeCompletion(content="I can't help with that", prompt_tokens=20,
                                      completion_tokens=5)
            return FakeCompletion(content="Here is the complete fix.", prompt_tokens=22,
                                  completion_tokens=8)

        _patch_completion(monkeypatch, _refusal_then_ok)

        r = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )

        assert r.status_code == 200
        assert r.json()["choices"][0]["message"]["content"] == "Here is the complete fix."
        assert calls[0] in cheap_models
        assert calls[-1] == mid_first
        rows = _all_rows(tmp_db)
        assert rows[0]["escalated"] == 1
        assert ";quality_escalated" in rows[0]["route_reason"]


# ---------------------------------------------------------------------------
# Admin endpoints smoke
# ---------------------------------------------------------------------------

class TestAdmin:
    def test_admin_stats_empty(self, client):
        r = client.get("/admin/stats")
        assert r.status_code == 200
        body = r.json()
        assert body["kpis"]["calls"] == 0

    def test_admin_stats_after_call(self, client, monkeypatch):
        _patch_completion(monkeypatch, FakeCompletion())
        client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        r = client.get("/admin/stats")
        assert r.json()["kpis"]["calls"] == 1

    def test_admin_routing_quality(self, client, tmp_db):
        from app import telemetry

        telemetry.record(telemetry.CallRecord(
            session_id="rq",
            picked_tier="cheap",
            would_have_tier="mid",
            route_reason="rule:tiny_prompt",
        ))
        telemetry.record(telemetry.CallRecord(
            session_id="rq",
            picked_tier="mid",
            would_have_tier="mid",
            route_reason="rule:stack_trace",
        ))

        r = client.get("/admin/routing_quality", params={"session": "rq"})
        assert r.status_code == 200
        body = r.json()
        assert body["calls"] == 2
        assert body["disagreements"] == 1
        assert body["disagreement_rate_pct"] == 50.0
