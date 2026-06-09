# IDE Setup

ClearView is a single entry LLM gateway. Point your tools at ClearView once,
and it routes across providers for you.

## One-Time Setup

Start ClearView locally:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Then configure IDEs and coding tools with:

```text
Base URL: http://localhost:8000/v1
API key: clearview-local
Model: clearview-auto
```

For tools that read environment variables:

```bash
export OPENAI_BASE_URL=http://localhost:8000/v1
export OPENAI_API_KEY=clearview-local
export OPENAI_MODEL=clearview-auto
```

`clearview-client.env.example` contains the same client-side values. Keep it
separate from `.env`: `.env` is for real provider keys used by the ClearView
server, while client tools only need a dummy key that points them at ClearView.

## VS Code

This repo includes checked-in VS Code configuration:

- `ClearView: run gateway` starts the local gateway on `127.0.0.1:8000`.
- `ClearView: debug gateway` starts the same server under the debugger.
- `ClearView: doctor` checks provider keys, local CLIs, and Ollama.
- `ClearView: test` runs `pytest -q`.
- `ClearView: lint` runs `ruff check .`.
- `ClearView: print IDE gateway config` prints the values to paste into an AI
  editor extension.

For AI extensions such as Continue or other OpenAI-compatible clients, use the
same three values:

```text
Base URL: http://localhost:8000/v1
API key: clearview-local
Model: clearview-auto
```

## Generate config for your tool

ClearView prints a ready-to-paste snippet for common AI editors:

```bash
python -m app.doctor --ide continue   # ~/.continue/config.yaml model entry
python -m app.doctor --ide cline      # Cline OpenAI-compatible JSON
python -m app.doctor --ide cursor     # Cursor base-URL override steps
python -m app.doctor --ide aider      # aider env + flags
python -m app.doctor --ide openai     # generic OPENAI_* env vars
python -m app.doctor --ide anthropic  # for tools hardcoded to Anthropic
python -m app.doctor --ide gemini     # for tools hardcoded to Gemini
```

If the gateway is locked (see below), the snippet uses your real client key
instead of the `clearview-local` dummy.

## Seeing which model handled a request

Every chat response carries headers so you can confirm routing without the
dashboard:

```text
x-clearview-tier        local | cheap | mid | frontier
x-clearview-model       the model that actually served the request
x-clearview-request-id  correlate with telemetry / /admin/calls_detail
```

Watch routing live at `http://localhost:8000/admin/explorer`.

## Plan/execute stage routing (frontier plans, local executes)

With `stages.enabled: true` in `policy.yaml`, ClearView routes agent workflows
in two phases: planning turns go to the frontier tier, execution turns go to
the `local` tier (ollama). Request header:

```text
x-clearview-stage: plan      # force the frontier planning tier
x-clearview-stage: execute   # force the local execution tier
```

Without the header, `auto_detect` flags a turn as `execute` when the message
history already carries tool results or assistant `tool_calls` — i.e. the
agent loop is underway and the plan has been made. `x-clearview-tier` still
overrides everything.

Resilience: with `CLEARVIEW_OLLAMA_PROBE=1`, execute turns fall back to the
cheap cloud tier when ollama isn't running; a refusal or suspiciously short
local answer is retried one tier up (`quality_escalated` in route_reason).

## Locking the gateway (shared networks)

By default any client key is accepted (open local dev). To require a key — e.g.
when running ClearView for a team on a LAN — set an allow-list:

```bash
# server-side (.env or process env)
CLEARVIEW_CLIENT_KEYS=team-shared-key,alice-key
```

Then clients must present one of those as their API key (the value they paste
into the IDE). Per-team `cv_team_*` bearer tokens and `/chat` cookie logins are
always allowed. Unset = open.

## Other IDEs

Use the OpenAI-compatible settings whenever the tool supports a custom base URL:

```text
Base URL: http://localhost:8000/v1
API key: clearview-local
Model: clearview-auto
```

Only use provider-specific compatibility settings when a tool is hardcoded to a
provider protocol:

```bash
export ANTHROPIC_BASE_URL=http://localhost:8000
export ANTHROPIC_API_KEY=clearview-local

export GOOGLE_GEMINI_BASE_URL=http://localhost:8000
export GEMINI_API_KEY=clearview-local
```

