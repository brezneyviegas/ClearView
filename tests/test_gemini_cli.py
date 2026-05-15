"""Tests for the Gemini CLI subscription-bypass adapter.

No subprocess execution: we monkeypatch subprocess.run and
asyncio.create_subprocess_exec to feed canned JSON.
"""
from __future__ import annotations

import asyncio
import subprocess
from types import SimpleNamespace

import pytest


# Canned `gemini -o json` output — single JSON object on stdout, leading log
# line included to exercise the lenient parser.
_OK_STDOUT = """\
Ripgrep is not available. Falling back to GrepTool.
{
  "session_id": "abc-123",
  "response": "OK",
  "stats": {
    "models": {
      "gemini-2.5-flash": {
        "api": {"totalRequests": 1, "totalErrors": 0, "totalLatencyMs": 1100},
        "tokens": {
          "input": 1544,
          "prompt": 1544,
          "candidates": 35,
          "total": 1680,
          "cached": 0,
          "thoughts": 101,
          "tool": 0
        }
      }
    }
  }
}
"""


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------

class TestGating:
    def test_is_enabled_default_off(self, monkeypatch):
        from app.providers import gemini_cli
        monkeypatch.delenv("CLEARVIEW_USE_GEMINI_CLI", raising=False)
        assert gemini_cli.is_enabled() is False

    def test_is_enabled_when_flag_set(self, monkeypatch):
        from app.providers import gemini_cli
        monkeypatch.setenv("CLEARVIEW_USE_GEMINI_CLI", "1")
        assert gemini_cli.is_enabled() is True

    def test_is_available_model_matches_gemini_and_google_prefixes(self):
        from app.providers import gemini_cli
        assert gemini_cli.is_available_model("gemini/gemini-1.5-flash") is True
        assert gemini_cli.is_available_model("google/gemini-2.5-pro") is True

    def test_is_available_model_rejects_other_providers(self):
        from app.providers import gemini_cli
        assert gemini_cli.is_available_model("anthropic/claude-haiku-4-5") is False
        assert gemini_cli.is_available_model("openai/gpt-4o-mini") is False
        assert gemini_cli.is_available_model("ollama/llama3.2") is False
        assert gemini_cli.is_available_model("") is False
        assert gemini_cli.is_available_model(None) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Model id mapping
# ---------------------------------------------------------------------------

class TestModelMapping:
    def test_strips_gemini_prefix(self, monkeypatch):
        from app.providers import gemini_cli
        monkeypatch.delenv("CLEARVIEW_GEMINI_MODEL", raising=False)
        assert gemini_cli._to_cli_model("gemini/gemini-1.5-flash") == "gemini-1.5-flash"

    def test_strips_google_prefix(self, monkeypatch):
        from app.providers import gemini_cli
        monkeypatch.delenv("CLEARVIEW_GEMINI_MODEL", raising=False)
        assert gemini_cli._to_cli_model("google/gemini-2.5-pro") == "gemini-2.5-pro"

    def test_env_override_wins(self, monkeypatch):
        from app.providers import gemini_cli
        monkeypatch.setenv("CLEARVIEW_GEMINI_MODEL", "gemini-3.1-flash")
        assert gemini_cli._to_cli_model("gemini/whatever") == "gemini-3.1-flash"


# ---------------------------------------------------------------------------
# Output parser
# ---------------------------------------------------------------------------

class TestParseOutput:
    def test_parses_full_object(self):
        from app.providers import gemini_cli
        parsed = gemini_cli._parse_output(_OK_STDOUT)
        assert parsed["response"] == "OK"
        assert "gemini-2.5-flash" in parsed["stats"]["models"]

    def test_parser_raises_on_empty(self):
        from app.providers import gemini_cli
        with pytest.raises(RuntimeError, match="empty stdout"):
            gemini_cli._parse_output("")

    def test_parser_raises_on_garbage(self):
        from app.providers import gemini_cli
        with pytest.raises(RuntimeError, match="could not parse"):
            gemini_cli._parse_output("not json at all")


class TestExtractUsage:
    def test_picks_first_model_entry(self):
        from app.providers import gemini_cli
        parsed = {
            "stats": {"models": {
                "gemini-2.5-flash": {"tokens": {"input": 10, "candidates": 3, "thoughts": 2}}
            }}
        }
        usage = gemini_cli._extract_usage(parsed)
        assert usage["input"] == 10
        assert usage["candidates"] == 3

    def test_returns_empty_when_no_stats(self):
        from app.providers import gemini_cli
        assert gemini_cli._extract_usage({}) == {}
        assert gemini_cli._extract_usage({"stats": {}}) == {}


# ---------------------------------------------------------------------------
# Response shaping
# ---------------------------------------------------------------------------

class TestShapeResponse:
    def test_returns_openai_chat_completion_dict(self):
        from app.providers import gemini_cli
        out = gemini_cli._shape_response("gemini/gemini-1.5-flash", {
            "response": "hello",
            "stats": {"models": {"gemini-1.5-flash": {"tokens": {
                "input": 50, "candidates": 7, "thoughts": 3,
            }}}},
        })
        assert out["object"] == "chat.completion"
        assert out["model"] == "gemini/gemini-1.5-flash"
        assert out["choices"][0]["message"]["content"] == "hello"
        # thoughts roll into completion_tokens.
        assert out["usage"]["prompt_tokens"] == 50
        assert out["usage"]["completion_tokens"] == 10
        assert out["_clearview_via"] == "gemini_cli"

    def test_handles_missing_usage_block(self):
        from app.providers import gemini_cli
        out = gemini_cli._shape_response("gemini/gemini-1.5-flash", {
            "response": "no stats here",
        })
        assert out["choices"][0]["message"]["content"] == "no stats here"
        assert out["usage"]["prompt_tokens"] == 0
        assert out["usage"]["completion_tokens"] == 0


# ---------------------------------------------------------------------------
# completion() — sync subprocess path
# ---------------------------------------------------------------------------

class TestCompletion:
    def test_completion_happy_path(self, monkeypatch):
        from app.providers import gemini_cli
        captured: dict = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            captured["stdin"] = kwargs.get("input", b"")
            return SimpleNamespace(returncode=0, stdout=_OK_STDOUT.encode(), stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        out = gemini_cli.completion(
            "gemini/gemini-1.5-flash",
            [{"role": "user", "content": "Reply with OK"}],
        )
        assert out["choices"][0]["message"]["content"] == "OK"
        assert out["_clearview_via"] == "gemini_cli"
        assert "-o" in captured["args"]
        assert "json" in captured["args"]
        assert "-y" in captured["args"]
        assert "--skip-trust" in captured["args"]
        assert b"Reply with OK" in captured["stdin"]

    def test_completion_raises_on_nonzero_exit(self, monkeypatch):
        from app.providers import gemini_cli

        def fake_run(args, **kwargs):
            return SimpleNamespace(returncode=2, stdout=b"", stderr=b"boom")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(RuntimeError, match="exited with code 2"):
            gemini_cli.completion(
                "gemini/gemini-1.5-flash",
                [{"role": "user", "content": "hi"}],
            )

    def test_completion_refuses_streaming(self):
        from app.providers import gemini_cli
        with pytest.raises(NotImplementedError):
            gemini_cli.completion(
                "gemini/gemini-1.5-flash",
                [{"role": "user", "content": "hi"}],
                stream=True,
            )

    def test_completion_honours_model_env_override(self, monkeypatch):
        from app.providers import gemini_cli
        captured: dict = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            return SimpleNamespace(returncode=0, stdout=_OK_STDOUT.encode(), stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setenv("CLEARVIEW_GEMINI_MODEL", "gemini-3.1-flash-lite")
        gemini_cli.completion(
            "gemini/gemini-1.5-flash",
            [{"role": "user", "content": "hi"}],
        )
        i = captured["args"].index("-m")
        assert captured["args"][i + 1] == "gemini-3.1-flash-lite"


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

    def kill(self):  # pragma: no cover
        pass


class TestAcompletion:
    def test_acompletion_happy_path(self, monkeypatch):
        from app.providers import gemini_cli

        async def fake_create(*args, **kwargs):
            return _FakeProc(stdout=_OK_STDOUT.encode())

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)

        out = asyncio.run(
            gemini_cli.acompletion(
                "gemini/gemini-1.5-flash",
                [{"role": "user", "content": "hi"}],
            )
        )
        assert out["choices"][0]["message"]["content"] == "OK"
        assert out["_clearview_via"] == "gemini_cli"

    def test_acompletion_raises_on_nonzero(self, monkeypatch):
        from app.providers import gemini_cli

        async def fake_create(*args, **kwargs):
            return _FakeProc(stdout=b"", stderr=b"nope", returncode=3)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)

        with pytest.raises(RuntimeError, match="exited with code 3"):
            asyncio.run(gemini_cli.acompletion(
                "gemini/gemini-1.5-flash",
                [{"role": "user", "content": "hi"}],
            ))

    def test_acompletion_refuses_streaming(self):
        from app.providers import gemini_cli
        with pytest.raises(NotImplementedError):
            asyncio.run(gemini_cli.acompletion(
                "gemini/gemini-1.5-flash",
                [{"role": "user", "content": "hi"}],
                stream=True,
            ))
