"""Alternate upstream provider adapters.

Currently houses the Claude Code CLI adapter, which lets ClearView run on a
Claude Pro/Max subscription with zero per-call API spend.
"""
from . import claude_cli  # noqa: F401
from . import codex_cli  # noqa: F401
from . import gemini_cli  # noqa: F401

__all__ = ["claude_cli", "codex_cli", "gemini_cli"]
