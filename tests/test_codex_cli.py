"""Tests for the Codex CLI subscription-bypass adapter.

No subprocess execution: we monkeypatch subprocess.run and
asyncio.create_subprocess_exec to feed canned NDJSON.
"""
from __future__ import annotations

import asyncio
import subprocess
from types import SimpleNamespace

import pytest


# Canned event streams the adapter must parse correctly.
_OK_STDOUT = (
    '{"type":"thread.started","thread_id":"019..."}\n'
    '{"type":"turn.started"}\n'
    '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"OK"}}\n'
    '{"type":"turn.completed","usage":{"input_tokens":13487,'
    '"cached_input_tokens":12160,"output_tokens":5,"reasoning_output_tokens":0}}\n'
)

_ERROR_STDOUT = (
    '{"type":"thread.started","thread_id":"019..."}\n'
    '{"type":"turn.started"}\n'
    '{"type":"error","message":"model not allowed on this account"}\n'
    '{"type":"turn.failed","error":{"message":"model not allowed on this account"}}\n'
)


# ---------------------------------------------------------------------------
# Gating helpers
# ---------------------------------------------------------------------------

class TestGating:
    def test_is_enabled_default_off(self, monkeypatch):
        from app.providers import codex_cli
        monkeypatch.delenv("CLEARVIEW_USE_CODEX_CLI", raising=False)
        assert codex_cli.is_enabled() is False

    def test_is_enabled_when_flag_set(self, monkeypatch):
        from app.providers import codex_cli
        monkeypatch.setenv("CLEARVIEW_USE_CODEX_CLI", "1")
        assert codex_cli.is_enabled() is True

    def test_is_available_model_matches_openai_prefix(self):
        from app.providers import codex_cli
        assert codex_cli.is_available_model("openai/gpt-4o") is True
        assert codex_cli.is_available_model("openai/gpt-5.5") is True

    def test_is_available_model_rejects_other_providers(self):
        from app.providers import codex_cli
        assert codex_cli.is_available_model("anthropic/claude-haiku-4-5") is False
        assert codex_cli.is_available_model("gemini/gemini-1.5-flash") is False
        assert codex_cli.is_available_model("ollama/llama3.2") is False
        assert codex_cli.is_available_model("") is False


# ---------------------------------------------------------------------------
# Event parser
# ---------------------------------------------------------------------------

class TestParseEvents:
    def test_parse_happy_path(self):
        from app.providers import codex_cli
        text, usage, err = codex_cli._parse_events(_OK_STDOUT)
        assert text == "OK"
        assert usage["input_tokens"] == 13487
        assert usage["output_tokens"] == 5
        assert err is None

    def test_parse_concatenates_multiple_agent_messages(self):
        from app.providers import codex_cli
        stdout = (
            '{"type":"item.completed","item":{"type":"agent_message","text":"Hello, "}}\n'
            '{"type":"item.completed","item":{"type":"agent_message","text":"world."}}\n'
            '{"type":"turn.completed","usage":{"input_tokens":3,"output_tokens":2}}\n'
        )
        text, usage, err = codex_cli._parse_events(stdout)
        assert text == "Hello, world."
        assert err is None

    def test_parse_returns_error_message(self):
        from app.providers import codex_cli
        text, _usage, err = codex_cli._parse_events(_ERROR_STDOUT)
        assert text == ""
        assert err == "model not allowed on this account"

    def test_parse_ignores_non_json_lines(self):
        from app.providers import codex_cli
        stdout = (
            "Reading prompt from stdin...\n"
            "\n"
            '{"type":"item.completed","item":{"type":"agent_message","text":"hi"}}\n'
            '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}\n'
        )
        text, usage, err = codex_cli._parse_events(stdout)
        assert text == "hi"
        assert usage["input_tokens"] == 1
        assert err is None

    def test_parse_empty_input(self):
        from app.providers import codex_cli
        text, usage, err = codex_cli._parse_events("")
        assert text == ""
        assert usage == {}
        assert err is None


# ---------------------------------------------------------------------------
# Response shaping
# ---------------------------------------------------------------------------

class TestShapeResponse:
    def test_shape_returns_openai_chat_completion_dict(self):
        from app.providers import codex_cli
        out = codex_cli._shape_response(
            "openai/gpt-4o-mini",
            "hello",
            {"input_tokens": 10, "cached_input_tokens": 5, "output_tokens": 7,
             "reasoning_output_tokens": 3},
        )
        assert out["object"] == "chat.completion"
        assert out["model"] == "openai/gpt-4o-mini"
        assert out["choices"][0]["message"]["content"] == "hello"
        # cached_input_tokens roll into prompt_tokens.
        assert out["usage"]["prompt_tokens"] == 15
        # reasoning_output_tokens roll into completion_tokens.
        assert out["usage"]["completion_tokens"] == 10
        assert out["_clearview_via"] == "codex_cli"


# ---------------------------------------------------------------------------
# completion() — sync subprocess path
# ---------------------------------------------------------------------------

class TestCompletion:
    def test_completion_happy_path(self, monkeypatch):
        from app.providers import codex_cli

        captured: dict = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            captured["stdin"] = kwargs.get("input", b"")
            return SimpleNamespace(returncode=0, stdout=_OK_STDOUT.encode(), stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        out = codex_cli.completion(
            "openai/gpt-4o-mini",
            [{"role": "user", "content": "Reply with OK"}],
        )

        assert out["choices"][0]["message"]["content"] == "OK"
        assert out["_clearview_via"] == "codex_cli"
        # Args should include exec --json --skip-git-repo-check + model.
        assert "exec" in captured["args"]
        assert "--json" in captured["args"]
        assert "--skip-git-repo-check" in captured["args"]
        assert "-m" in captured["args"]
        # Prompt fed via stdin.
        assert b"Reply with OK" in captured["stdin"]

    def test_completion_raises_on_nonzero_exit(self, monkeypatch):
        from app.providers import codex_cli

        def fake_run(args, **kwargs):
            return SimpleNamespace(returncode=2, stdout=b"", stderr=b"boom")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(RuntimeError, match="exited with code 2"):
            codex_cli.completion("openai/gpt-4o", [{"role": "user", "content": "hi"}])

    def test_completion_raises_on_codex_error_event(self, monkeypatch):
        from app.providers import codex_cli

        def fake_run(args, **kwargs):
            return SimpleNamespace(returncode=0, stdout=_ERROR_STDOUT.encode(), stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(RuntimeError, match="model not allowed"):
            codex_cli.completion("openai/gpt-4o", [{"role": "user", "content": "hi"}])

    def test_completion_refuses_streaming(self, monkeypatch):
        from app.providers import codex_cli
        with pytest.raises(NotImplementedError):
            codex_cli.completion(
                "openai/gpt-4o", [{"role": "user", "content": "hi"}], stream=True,
            )

    def test_completion_honours_codex_model_env(self, monkeypatch):
        from app.providers import codex_cli
        captured: dict = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            return SimpleNamespace(returncode=0, stdout=_OK_STDOUT.encode(), stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setenv("CLEARVIEW_CODEX_MODEL", "gpt-5.7-custom")
        codex_cli.completion("openai/gpt-4o", [{"role": "user", "content": "hi"}])

        i = captured["args"].index("-m")
        assert captured["args"][i + 1] == "gpt-5.7-custom"


# ---------------------------------------------------------------------------
# acompletion() — async subprocess path
# ---------------------------------------------------------------------------

class _FakeProc:
    def __init__(self, stdout: bytes, stderr: bytes = b"", returncode: int = 0):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self, input: bytes = b""):
        return self._stdout, self._stderr

    def kill(self):  # pragma: no cover — unused in happy paths
        pass


class TestAcompletion:
    def test_acompletion_happy_path(self, monkeypatch):
        from app.providers import codex_cli

        async def fake_create(*args, **kwargs):
            return _FakeProc(stdout=_OK_STDOUT.encode())

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)

        out = asyncio.run(
            codex_cli.acompletion(
                "openai/gpt-4o-mini",
                [{"role": "user", "content": "hi"}],
            )
        )
        assert out["choices"][0]["message"]["content"] == "OK"
        assert out["_clearview_via"] == "codex_cli"

    def test_acompletion_raises_on_nonzero(self, monkeypatch):
        from app.providers import codex_cli

        async def fake_create(*args, **kwargs):
            return _FakeProc(stdout=b"", stderr=b"nope", returncode=3)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)

        with pytest.raises(RuntimeError, match="exited with code 3"):
            asyncio.run(
                codex_cli.acompletion(
                    "openai/gpt-4o", [{"role": "user", "content": "hi"}],
                )
            )

    def test_acompletion_refuses_streaming(self):
        from app.providers import codex_cli
        with pytest.raises(NotImplementedError):
            asyncio.run(codex_cli.acompletion(
                "openai/gpt-4o", [{"role": "user", "content": "hi"}], stream=True,
            ))
