"""Customizable setup: mock provider, graceful fallback, setup doctor."""
from __future__ import annotations

import pytest
import yaml

from app import doctor, pricing
from app.providers import mock as mock_provider
from app.router import _pick_model, _provider_available, build_availability
from app.config import load_policy


# ---------------------------------------------------------------------------
# Mock provider
# ---------------------------------------------------------------------------

class TestMockProvider:
    def test_handles_only_mock_models(self):
        assert mock_provider.is_available_model("mock/echo")
        assert not mock_provider.is_available_model("openai/gpt-4o")

    def test_completion_shape(self):
        r = mock_provider.completion("mock/echo", [{"role": "user", "content": "hi there"}])
        assert r["model"] == "mock/echo"
        assert r["_clearview_via"] == "mock"
        assert "hi there" in r["choices"][0]["message"]["content"]
        assert r["usage"]["completion_tokens"] > 0

    @pytest.mark.asyncio
    async def test_astream_yields_chunk_then_done(self):
        seen = []
        async for c in mock_provider.astream("mock/echo", [{"role": "user", "content": "x"}]):
            seen.append(c)
        assert seen[-1] == "[DONE]"
        assert seen[0]["choices"][0]["delta"]["content"]

    def test_pricing_is_free(self):
        assert pricing.cost_for("mock/echo", 100, 100) == 0.0

    def test_provider_always_available(self):
        assert _provider_available("mock/echo") is True


# ---------------------------------------------------------------------------
# Graceful fallback in _pick_model
# ---------------------------------------------------------------------------

class TestPickModelFallback:
    def test_falls_back_to_mock_when_nothing_available(self, policy, monkeypatch):
        monkeypatch.delenv("CLEARVIEW_USE_MOCK", raising=False)
        # No keys, no ollama, no CLIs → every tier prunes to empty.
        for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
                  "GOOGLE_API_KEY", "CLEARVIEW_USE_CLAUDE_CLI",
                  "CLEARVIEW_USE_CODEX_CLI", "CLEARVIEW_USE_GEMINI_CLI"):
            monkeypatch.delenv(k, raising=False)
        # Policy with no ollama/mock members so availability is genuinely empty.
        policy.tiers = {"cheap": ["openai/gpt-4o-mini"], "mid": ["openai/gpt-4o"],
                        "frontier": ["anthropic/claude-opus-4-7"]}
        build_availability(policy)
        assert _pick_model("cheap", policy) == "mock/echo"

    def test_explicit_mock_mode_routes_all_to_mock(self, policy, monkeypatch):
        monkeypatch.setenv("CLEARVIEW_USE_MOCK", "1")
        assert _pick_model("frontier", policy) == "mock/echo"

    def test_falls_back_down_to_available_cheaper_tier(self, policy, monkeypatch):
        monkeypatch.delenv("CLEARVIEW_USE_MOCK", raising=False)
        # Only cheap has an available (ollama, keyless) model; ask for frontier.
        policy.tiers = {"cheap": ["ollama/llama3.2"], "mid": ["openai/gpt-4o"],
                        "frontier": ["anthropic/claude-opus-4-7"]}
        for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        build_availability(policy)
        assert _pick_model("frontier", policy) == "ollama/llama3.2"


# ---------------------------------------------------------------------------
# Setup doctor
# ---------------------------------------------------------------------------

class TestDoctor:
    def test_probe_reports_mock_always_available(self, monkeypatch):
        for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setattr(doctor, "_ollama_running", lambda **k: False)
        rep = doctor.probe()
        assert rep["providers"]["mock"]["available"] is True
        assert rep["providers"]["anthropic"]["available"] is False
        assert any("mock provider" in r for r in rep["recommendations"])

    def test_probe_detects_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
        monkeypatch.setattr(doctor, "_ollama_running", lambda **k: False)
        assert doctor.probe()["providers"]["anthropic"]["available"] is True

    def test_tailor_prunes_and_backfills(self, monkeypatch):
        monkeypatch.setattr(doctor, "_ollama_running", lambda **k: False)
        for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
                  "GOOGLE_API_KEY", "CLEARVIEW_USE_CLAUDE_CLI",
                  "CLEARVIEW_USE_CODEX_CLI", "CLEARVIEW_USE_GEMINI_CLI"):
            monkeypatch.delenv(k, raising=False)
        pol = load_policy()
        report = doctor.probe()
        data, notes = doctor.tailor_policy(pol, report)
        # Every tier backfilled to mock; classifier disabled.
        for tier, models in data["tiers"].items():
            assert models == ["mock/echo"]
        assert data["classifier"]["enabled"] is False
        assert notes

    def test_tailor_keeps_available_provider(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
        monkeypatch.setattr(doctor, "_ollama_running", lambda **k: False)
        for k in ("OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        pol = load_policy()
        data, _notes = doctor.tailor_policy(pol, doctor.probe())
        # Anthropic models survive; non-anthropic pruned.
        assert any(m.startswith("anthropic/") for m in data["tiers"]["cheap"])
        assert all(not m.startswith("openai/") for m in data["tiers"]["mid"])

    def test_write_tailored_backs_up(self, tmp_path, monkeypatch):
        monkeypatch.setattr(doctor, "_ollama_running", lambda **k: False)
        src = tmp_path / "policy.yaml"
        import shutil, pathlib
        shutil.copy2(pathlib.Path("policy.yaml"), src)
        res = doctor.write_tailored(out_path=str(src), policy_path=str(src))
        assert res["backup"] is not None
        written = yaml.safe_load(src.read_text())
        assert "tiers" in written

    def test_endpoint(self, client):
        r = client.get("/admin/setup")
        assert r.status_code == 200
        assert "providers" in r.json()
        assert r.json()["providers"]["mock"]["available"] is True


# ---------------------------------------------------------------------------
# IDE config generator
# ---------------------------------------------------------------------------

class TestIdeConfig:
    def test_openai_env(self):
        out = doctor.ide_config("openai")
        assert "OPENAI_BASE_URL=http://localhost:8000/v1" in out
        assert "clearview-auto" in out

    def test_continue_is_valid_yaml(self):
        out = doctor.ide_config("continue")
        body = "\n".join(l for l in out.splitlines() if not l.strip().startswith("#"))
        cfg = yaml.safe_load(body)
        m = cfg["models"][0]
        assert m["provider"] == "openai"
        assert m["apiBase"] == "http://localhost:8000/v1"
        assert m["model"] == "clearview-auto"

    def test_cline_is_valid_json(self):
        import json
        d = json.loads(doctor.ide_config("cline"))
        assert d["openAiBaseUrl"] == "http://localhost:8000/v1"
        assert d["openAiModelId"] == "clearview-auto"

    def test_unknown_tool_raises(self):
        with pytest.raises(ValueError):
            doctor.ide_config("notarealide")

    def test_uses_client_key_when_locked(self, monkeypatch):
        monkeypatch.setenv("CLEARVIEW_CLIENT_KEYS", "team-key,alice")
        assert "team-key" in doctor.ide_config("openai")
        assert "clearview-local" not in doctor.ide_config("openai")


# ---------------------------------------------------------------------------
# Optional client-key gate
# ---------------------------------------------------------------------------

class TestClientKeyGate:
    BODY = {"model": "clearview-auto", "messages": [{"role": "user", "content": "hi"}]}

    def test_open_by_default(self, client, monkeypatch):
        monkeypatch.delenv("CLEARVIEW_CLIENT_KEYS", raising=False)
        r = client.post("/v1/chat/completions", json=self.BODY)
        assert r.status_code == 200

    def test_locked_rejects_missing_and_wrong(self, client, monkeypatch):
        monkeypatch.setenv("CLEARVIEW_CLIENT_KEYS", "secret-1,secret-2")
        assert client.post("/v1/chat/completions", json=self.BODY).status_code == 401
        r = client.post("/v1/chat/completions", json=self.BODY,
                        headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401

    def test_locked_allows_listed_key(self, client, monkeypatch):
        monkeypatch.setenv("CLEARVIEW_CLIENT_KEYS", "secret-1,secret-2")
        r = client.post("/v1/chat/completions", json=self.BODY,
                        headers={"Authorization": "Bearer secret-2"})
        assert r.status_code == 200
