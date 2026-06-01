"""Setup doctor — adapt ClearView to whatever the user actually has.

Probes the environment for usable backends and reports what's available:
  - provider API keys   (ANTHROPIC / OPENAI / GEMINI|GOOGLE)
  - subscription CLIs    (claude / codex / gemini binaries + opt-in flags)
  - local ollama         (HTTP ping on :11434)
  - the built-in mock    (always available, zero setup)

It can also generate a *tailored* policy.yaml: the existing policy with every
unreachable model pruned from its tiers, empty tiers backfilled with the mock
provider, and the classifier disabled if its model isn't reachable — so the
config matches the machine it runs on and nothing dead-ends.

CLI:
    python -m app.doctor                 # print the availability report
    python -m app.doctor --json          # machine-readable report
    python -m app.doctor --write         # back up + rewrite policy.yaml tailored
    python -m app.doctor --write --out custom.yaml
"""
from __future__ import annotations

import json
import os
import shutil
import time
import urllib.request
from pathlib import Path
from typing import Any

import yaml

from .config import Policy, load_policy

_OLLAMA_URL = "http://localhost:11434/api/tags"


def _ollama_running(timeout: float = 1.5) -> bool:
    url = os.environ.get("OLLAMA_API_BASE", "").rstrip("/")
    probe = (url + "/api/tags") if url else _OLLAMA_URL
    try:
        with urllib.request.urlopen(probe, timeout=timeout) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def _cli(bin_name: str, flag_env: str, bin_env: str | None = None) -> dict:
    name = (bin_env and os.environ.get(bin_env)) or bin_name
    return {
        "binary": name,
        "flag_env": flag_env,
        "installed": shutil.which(name) is not None,
        "enabled": os.environ.get(flag_env) == "1",
    }


def probe() -> dict:
    """Return a structured availability report. No mutation, fast, no network
    except a short ollama ping."""
    anthropic_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    openai_key = bool(os.environ.get("OPENAI_API_KEY"))
    gemini_key = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))

    claude = _cli("claude", "CLEARVIEW_USE_CLAUDE_CLI", "CLEARVIEW_CLAUDE_BIN")
    codex = _cli("codex", "CLEARVIEW_USE_CODEX_CLI", "CLEARVIEW_CODEX_BIN")
    gemini = _cli("gemini", "CLEARVIEW_USE_GEMINI_CLI", "CLEARVIEW_GEMINI_BIN")
    ollama_up = _ollama_running()

    providers = {
        "anthropic": {
            "api_key": anthropic_key, "cli": claude,
            "available": anthropic_key or claude["enabled"],
        },
        "openai": {
            "api_key": openai_key, "cli": codex,
            "available": openai_key or codex["enabled"],
        },
        "gemini": {
            "api_key": gemini_key, "cli": gemini,
            "available": gemini_key or gemini["enabled"],
        },
        "ollama": {"running": ollama_up, "available": ollama_up},
        "mock": {"always_available": True, "available": True},
    }

    recs: list[str] = []
    for prov, label, key_env in (
        ("anthropic", "Anthropic", "ANTHROPIC_API_KEY"),
        ("openai", "OpenAI", "OPENAI_API_KEY"),
        ("gemini", "Gemini", "GEMINI_API_KEY"),
    ):
        p = providers[prov]
        if not p["available"]:
            cli = p["cli"]
            if cli["installed"] and not cli["enabled"]:
                recs.append(f"{label}: CLI '{cli['binary']}' is installed — set "
                            f"{cli['flag_env']}=1 "
                            f"to use your subscription with no API key.")
            else:
                recs.append(f"{label}: set {key_env} (or install + enable the CLI) to use it.")
    if not providers["ollama"]["available"]:
        recs.append("ollama: not reachable on :11434 — run `ollama serve` for free local models.")
    if not any(providers[p]["available"] for p in ("anthropic", "openai", "gemini", "ollama")):
        recs.append("No real provider reachable — requests will be served by the built-in "
                    "mock provider. Configure one of the above for live answers.")

    return {"providers": providers, "recommendations": recs}


def _model_available(model: str, providers: dict) -> bool:
    if model.startswith("mock/"):
        return True
    if model.startswith("ollama/") or model.startswith("ollama_chat/"):
        return providers["ollama"]["available"]
    if model.startswith("anthropic/"):
        return providers["anthropic"]["available"]
    if model.startswith("openai/"):
        return providers["openai"]["available"]
    if model.startswith("gemini/") or model.startswith("google/"):
        return providers["gemini"]["available"]
    return True  # unknown prefix: assume usable, litellm will surface errors


def tailor_policy(policy: Policy, report: dict) -> tuple[dict, list[str]]:
    """Return (policy_dict, notes): the policy with unreachable models pruned,
    empty tiers backfilled with the mock, classifier disabled if unreachable."""
    providers = report["providers"]
    data = policy.model_dump()
    notes: list[str] = []

    for tier, models in list(data.get("tiers", {}).items()):
        kept = [m for m in models if _model_available(m, providers)]
        dropped = [m for m in models if m not in kept]
        if dropped:
            notes.append(f"tier '{tier}': pruned unreachable {dropped}")
        if not kept:
            kept = ["mock/echo"]
            notes.append(f"tier '{tier}': no reachable model — backfilled with mock/echo")
        data["tiers"][tier] = kept

    clf = data.get("classifier", {})
    clf_model = clf.get("model", "")
    if clf.get("enabled") and not _model_available(clf_model, providers):
        clf["enabled"] = False
        notes.append(f"classifier: model '{clf_model}' unreachable — disabled "
                     "(routing falls back to rules + embedding classifier)")

    return data, notes


def write_tailored(out_path: str | None = None, policy_path: str | None = None) -> dict:
    """Generate a tailored policy and write it, backing up the target first."""
    policy = load_policy(policy_path)
    report = probe()
    data, notes = tailor_policy(policy, report)

    target = Path(out_path or policy_path or os.environ.get("CLEARVIEW_POLICY_PATH", "./policy.yaml"))
    backup = None
    if target.exists():
        backup = f"{target}.bak.{int(time.time())}"
        shutil.copy2(target, backup)
    target.write_text(yaml.safe_dump(data, sort_keys=False))
    return {"written": str(target), "backup": backup, "notes": notes,
            "report": report}


# --- IDE / client config generation ---------------------------------------

_BASE_URL_OPENAI = "http://localhost:8000/v1"
_BASE_URL_ROOT = "http://localhost:8000"
_DEFAULT_MODEL = "clearview-auto"

IDE_TOOLS = ("openai", "continue", "cline", "cursor", "aider", "anthropic", "gemini")


def _client_key() -> str:
    """The key a client should present. If the gateway is locked
    (CLEARVIEW_CLIENT_KEYS), use the first allowed key; else the dummy."""
    raw = os.environ.get("CLEARVIEW_CLIENT_KEYS", "").strip()
    if raw:
        first = raw.split(",")[0].strip()
        if first:
            return first
    return "clearview-local"


def ide_config(tool: str) -> str:
    """Return a ready-to-paste config snippet for a given IDE / client tool."""
    tool = (tool or "openai").lower()
    key = _client_key()

    if tool == "openai":
        return (f"export OPENAI_BASE_URL={_BASE_URL_OPENAI}\n"
                f"export OPENAI_API_KEY={key}\n"
                f"export OPENAI_MODEL={_DEFAULT_MODEL}")

    if tool == "continue":  # ~/.continue/config.yaml (modern Continue format)
        return (
            "# ~/.continue/config.yaml — add under `models:`\n"
            "models:\n"
            "  - name: ClearView (auto)\n"
            "    provider: openai\n"
            f"    apiBase: {_BASE_URL_OPENAI}\n"
            f"    apiKey: {key}\n"
            f"    model: {_DEFAULT_MODEL}\n"
            "    roles: [chat, edit, apply]")

    if tool == "cline":  # VS Code Cline — OpenAI Compatible provider
        return json.dumps({
            "apiProvider": "openai",
            "openAiBaseUrl": _BASE_URL_OPENAI,
            "openAiApiKey": key,
            "openAiModelId": _DEFAULT_MODEL,
        }, indent=2)

    if tool == "cursor":  # Cursor → Settings → Models → OpenAI override
        return (
            "Cursor → Settings → Models → 'Override OpenAI Base URL':\n"
            f"  Base URL:  {_BASE_URL_OPENAI}\n"
            f"  API Key:   {key}\n"
            f"  Model:     add a custom model named '{_DEFAULT_MODEL}'\n"
            "Note: Cursor's cloud features may bypass a localhost base URL; "
            "local/custom-model mode works.")

    if tool == "aider":  # aider uses OpenAI-compatible env
        return (
            f"export OPENAI_API_BASE={_BASE_URL_OPENAI}\n"
            f"export OPENAI_API_KEY={key}\n"
            f"aider --model openai/{_DEFAULT_MODEL}")

    if tool == "anthropic":  # tools hardcoded to the Anthropic protocol
        return (f"export ANTHROPIC_BASE_URL={_BASE_URL_ROOT}\n"
                f"export ANTHROPIC_API_KEY={key}\n"
                f"export ANTHROPIC_MODEL={_DEFAULT_MODEL}")

    if tool == "gemini":  # tools hardcoded to the Gemini protocol
        return (f"export GOOGLE_GEMINI_BASE_URL={_BASE_URL_ROOT}\n"
                f"export GEMINI_API_KEY={key}\n"
                f"export GEMINI_MODEL={_DEFAULT_MODEL}")

    raise ValueError(f"unknown tool '{tool}'. Known: {', '.join(IDE_TOOLS)}")


def _format_report(report: dict) -> str:
    lines = ["ClearView setup doctor", "=" * 40, "", "Providers:"]
    for name, p in report["providers"].items():
        mark = "✓" if p.get("available") else "✗"
        detail = ""
        if "cli" in p:
            cli = p["cli"]
            detail = (f"  key={'yes' if p.get('api_key') else 'no'}"
                      f"  cli={'installed' if cli['installed'] else 'absent'}"
                      f"/{'on' if cli['enabled'] else 'off'}")
        elif "running" in p:
            detail = f"  running={'yes' if p['running'] else 'no'}"
        lines.append(f"  [{mark}] {name}{detail}")
    if report["recommendations"]:
        lines += ["", "Recommendations:"]
        lines += [f"  - {r}" for r in report["recommendations"]]
    else:
        lines += ["", "All set — at least one real provider is reachable."]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="app.doctor")
    ap.add_argument("--json", action="store_true", help="emit the report as JSON")
    ap.add_argument("--write", action="store_true",
                    help="write a tailored policy.yaml (backs up the target first)")
    ap.add_argument("--out", default=None, help="output path for --write")
    ap.add_argument("--ide", default=None, metavar="TOOL",
                    help=f"print client config for an IDE/tool ({', '.join(IDE_TOOLS)})")
    args = ap.parse_args(argv)

    if args.ide:
        try:
            snippet = ide_config(args.ide)
        except ValueError as e:
            print(str(e)); return 2
        print(snippet)
        print("\n# Routing visibility: every response carries headers\n"
              "#   x-clearview-tier   (cheap|mid|frontier)\n"
              "#   x-clearview-model  (the model that actually served it)\n"
              "#   x-clearview-request-id\n"
              "# Watch live routing at http://localhost:8000/admin/explorer")
        return 0

    if args.write:
        result = write_tailored(out_path=args.out)
        print(json.dumps(result, indent=2) if args.json else
              f"Wrote tailored policy to {result['written']}"
              + (f" (backup: {result['backup']})" if result['backup'] else "")
              + ("\n" + "\n".join(f"  - {n}" for n in result["notes"]) if result["notes"] else ""))
        return 0

    report = probe()
    print(json.dumps(report, indent=2) if args.json else _format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
