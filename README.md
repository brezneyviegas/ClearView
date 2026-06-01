# ClearView

ClearView is a single entry LLM gateway. Point your tools at ClearView once,
and it routes across providers for you.

ClearView sits between clients and providers, routes each prompt to the cheapest
capable model, and records tokens, cost, latency, savings, cache hits, and
routing decisions.

It exposes OpenAI-compatible APIs, compatibility shims for Anthropic/Gemini
clients, an operator dashboard, and a lightweight chat UI for teams.

---

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
# Optional: fill in provider keys or enable local CLI subscription adapters.
# ClearView runs with ZERO providers — see "Adapts to your setup" below.

uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### Adapts to your setup

ClearView works with whatever you have — and works with nothing at all:

```bash
python -m app.doctor          # probe keys / CLIs / ollama, get recommendations
python -m app.doctor --write  # rewrite policy.yaml tailored to this machine
```

- **No provider configured?** Requests are served by a built-in **mock** provider
  ($0, canned responses) so the dashboard, chat, and APIs all work out of the box.
  `CLEARVIEW_USE_MOCK=1` forces everything through it (offline demo).
- **A tier has no reachable model?** Routing gracefully falls back (up → down →
  mock) instead of erroring. Set `CLEARVIEW_MOCK_ON_FAILURE=0` to get hard 502s.
- `GET /admin/setup` returns the same probe report as JSON for the UI.

Open:

- Chat UI: `http://localhost:8000/chat`
- Cost explorer: `http://localhost:8000/admin/explorer`
- Health: `http://localhost:8000/health`

VS Code users can run `ClearView: bootstrap dev env`, then
`ClearView: run gateway` from the command palette. See
[`Docs/IDE_SETUP.md`](Docs/IDE_SETUP.md) for VS Code and other IDE setup.

Run tests:

```bash
pytest -q
python -m eval.run_eval
python performance/route_overhead.py --iterations 1000
```

## Client Setup

Use ClearView as your default OpenAI-compatible gateway:

```bash
export OPENAI_BASE_URL=http://localhost:8000/v1
export OPENAI_API_KEY=clearview-local
export OPENAI_MODEL=clearview-auto
```

Then choose a ClearView virtual model:

```text
clearview-auto
clearview-cheap
clearview-mid
clearview-frontier
```

Provider-specific client shims are also available for tools that require them.

Claude Messages-style clients:

```bash
export ANTHROPIC_BASE_URL=http://localhost:8000
export ANTHROPIC_API_KEY=clearview-local
```

Gemini generateContent-style clients:

```bash
export GOOGLE_GEMINI_BASE_URL=http://localhost:8000
export GEMINI_API_KEY=clearview-local
```

ClearView ignores the client-side dummy key for routing; real provider keys live
in `.env`, unless you use one of the subscription CLI adapters. Keep IDE client
settings separate from `.env`; `clearview-client.env.example` is the template
for tools that read environment variables.

## Main Features

- OpenAI-compatible `/v1/chat/completions`, `/v1/models`, and `/v1/responses`.
- Compatibility shims:
  - `POST /v1/messages`
  - `POST /v1beta/models/{model}:generateContent`
  - `POST /v1/models/{model}:generateContent`
  - Gemini streaming variants
- Virtual models:
  - `clearview-auto`
  - `clearview-cheap`
  - `clearview-mid`
  - `clearview-frontier`
- Direct configured model selection, for example `openai/gpt-4o`.
- `/chat` UI with team login, conversation history, provider selector, model
  selector, tier selector, streaming responses, and spend-vs-cap meter.
- Exact-match and semantic prompt cache.
- Per-team bearer tokens, quotas, allowed tiers, timezone-aware monthly caps.
- Shadow routing via `x-clearview-shadow`.
- Routing quality telemetry via `would_have_tier` and `/admin/routing_quality`.
- Prometheus metrics at `/metrics`.

## Routing Pipeline

Routing is configured in `policy.yaml`.

1. Rule layer:
   - explicit `x-clearview-tier` override
   - long prompt detection
   - stack traces
   - math/code/file path/URL structure
   - multiline code without fences
   - keyword and imperative-work rules
2. Classifier fallback:
   - model returns `score,confidence`
   - `score_to_tier` maps complexity to tier
   - confidence below `confidence_floor` escalates one tier
3. Health filter:
   - drops providers without API keys unless a CLI adapter is enabled
4. Escalation:
   - upstream error
   - empty response
   - refusal or suspiciously short output

Route reasons are stored in telemetry, for example:

```text
rule:stack_trace
classifier:score=2;confidence=0.40
direct_model:openai/gpt-4o
rule:tiny_prompt;quality_escalated
```

## Provider Modes

Paid API mode uses LiteLLM and normal provider keys:

```bash
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...
GEMINI_API_KEY=...
```

Subscription CLI adapters route provider-prefixed models through local CLI tools
instead of paid REST APIs:

```bash
CLEARVIEW_USE_CLAUDE_CLI=1
CLEARVIEW_USE_CODEX_CLI=1
CLEARVIEW_USE_GEMINI_CLI=1
```

Optional adapter settings:

```bash
CLEARVIEW_CLAUDE_BIN=claude
CLEARVIEW_CODEX_BIN=codex
CLEARVIEW_CODEX_MODEL=gpt-5.5
CLEARVIEW_GEMINI_BIN=gemini
CLEARVIEW_GEMINI_MODEL=
CLEARVIEW_CLI_TIMEOUT_SEC=120
```

Subscription-mode rows use `native_cost_usd = 0` and record notional API cost in
`synth_cost_usd`.

## Admin And Ops

Admin endpoints are open in local dev. Set `CLEARVIEW_ADMIN_TOKEN` to require a
bearer token for `/admin/*`.

Useful endpoints:

- `GET /admin/stats`
- `GET /admin/explorer`
- `GET /admin/ticker`
- `GET /admin/timeseries`
- `GET /admin/calls_detail`
- `GET /admin/shadow_compare`
- `GET /admin/routing_quality`
- `GET /metrics`

The explorer includes KPI cards, per-call rows, sparklines, cost ticker tape,
burn rate, candle charts, shadow comparison, drill-down panels, and an optional
price-change bell.

## Teams

Create a team:

```bash
python -m app.teams create \
  --name acme \
  --daily-cap 5 \
  --monthly-cap 100 \
  --tiers cheap,mid \
  --timezone America/New_York
```

Use the returned token:

```bash
Authorization: Bearer cv_team_<token>
```

Team capabilities:

- daily and monthly USD caps
- timezone-aware monthly reset
- allowed tier list
- spend attribution in telemetry
- team-scoped exact/semantic cache
- cookie login for `/chat`

## Cache

Exact-match cache is enabled by default:

```bash
CLEARVIEW_CACHE_ENABLED=1
CLEARVIEW_CACHE_TTL_SEC=3600
```

Semantic cache is also available:

```bash
CLEARVIEW_SEMANTIC_CACHE=1
CLEARVIEW_SEMANTIC_THRESHOLD=0.95
CLEARVIEW_SEMANTIC_SCAN_LIMIT=500
CLEARVIEW_EMBEDDING_BACKEND=openai   # openai | local | disabled
CLEARVIEW_EMBEDDING_MODEL=text-embedding-3-small
```

Cache hits are logged with cost savings and surfaced in `/admin/stats`.

## Eval And Benchmarks

Routing eval:

```bash
python -m eval.run_eval
python -m eval.run_eval --gate eval/gate.json
```

Live quality eval:

```bash
python -m eval.run_eval --live --quality
```

Router overhead benchmark:

```bash
python performance/route_overhead.py --iterations 10000
```

## Project Layout

```text
app/
  main.py                  FastAPI app, API shims, admin endpoints, chat UI routes
  router.py                rule engine, classifier fallback, availability
  pricing.py               LiteLLM cost helpers
  telemetry.py             SQLite calls table and aggregations
  cache.py                 exact and semantic cache storage
  embeddings.py            embedding backends and cosine helpers
  teams.py                 team tokens, quotas, timezone reset
  chat.py                  persisted chat conversations/messages
  providers/
    claude_cli.py
    codex_cli.py
    gemini_cli.py
  templates/
    chat.html
    explorer.html
eval/
  fixtures.json
  run_eval.py
  quality_eval.py
performance/
  route_overhead.py
tests/
policy.yaml
```

## License

TBD.
