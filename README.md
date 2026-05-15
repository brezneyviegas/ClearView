# ClearView

LLM cost-router middleware. Sits between a client and providers (Anthropic, OpenAI, Google, Ollama). Routes each prompt to the cheapest capable model. Surfaces tokens, cost, latency, and savings vs a baseline.

OpenAI-compatible REST. Drop-in for any tool that lets you set `OPENAI_BASE_URL`.

---

## Quick start

```bash
# 1. Install
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. Configure
cp .env.example .env
# Fill in ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY for the providers you want.

# 3. Run
uvicorn app.main:app --reload --port 8000

# 4. Open the cost explorer
open http://localhost:8000/admin/explorer
```

## Point your tool at ClearView

Anything that accepts an OpenAI-compatible base URL works:

```bash
export OPENAI_BASE_URL=http://localhost:8000/v1
export OPENAI_API_KEY=anything   # ClearView ignores; provider keys live in .env
```

Cursor / Continue / Aider / OpenAI SDK / Vercel AI SDK — all swap with the env var.

## CLI compatibility shims

ClearView also exposes lightweight provider-shape endpoints that translate into the
same router, budget checks, cache, and telemetry:

```bash
# Codex / OpenAI Responses-style clients
export OPENAI_BASE_URL=http://localhost:8000/v1
export OPENAI_API_KEY=anything

# Claude Messages-style clients
export ANTHROPIC_BASE_URL=http://localhost:8000
export ANTHROPIC_API_KEY=anything

# Gemini generateContent-style clients
export GOOGLE_GEMINI_BASE_URL=http://localhost:8000
export GEMINI_API_KEY=anything
```

Implemented compatibility endpoints:

- `POST /v1/responses`
- `POST /v1/messages`
- `POST /v1beta/models/{model}:generateContent`
- `POST /v1/models/{model}:generateContent`
- streaming variants for Anthropic/Gemini return a one-shot SSE stream

These shims cover normal text prompt/response traffic. Full Claude/Gemini tool-use
protocol parity is not implemented yet.

## Virtual models

| model id              | behavior                                     |
|-----------------------|----------------------------------------------|
| `clearview-auto`      | Full router (rules → classifier → escalate). |
| `clearview-cheap`     | Force cheap tier.                            |
| `clearview-mid`       | Force mid tier.                              |
| `clearview-frontier`  | Force frontier tier.                         |

Or use header `x-clearview-tier: cheap|mid|frontier` with `clearview-auto`.

## Routing pipeline

1. **Rule layer** — deterministic, fast. Token counts, keywords, code-fence presence, header overrides.
2. **Classifier fallback** — small LLM (Haiku by default) rates complexity 1-5 → tier.
3. **Escalation** — on error or empty response, retries one tier up. Logged.

Edit `policy.yaml` to change tiers, rules, classifier model, baseline.

## Cost explorer

`GET /admin/explorer` — server-rendered page modeled on the screenshot:

- KPIs: native total, plan-equiv total, drift % (savings), tokens out, best $/1k out.
- Per-call table: provider, model, route reason, latency, tokens in/out, native $, plan-equiv $, $/1k out.
- Session selector (`x-clearview-session: <name>` header on requests groups them).
- Auto-refresh every 5s.

`GET /admin/stats` returns the same data as JSON.

## Eval harness

```bash
python -m eval.run_eval         # routing-only, free, uses cost tables
python -m eval.run_eval --live  # actually calls providers
```

Reports routing accuracy, native cost, plan-equiv cost, drift %.

## Layout

```
app/
  main.py        FastAPI: /v1/chat/completions, /v1/models, /admin/*
  router.py      Rule engine + classifier fallback
  pricing.py     Cost calc (litellm) + drift / $/1k helpers
  telemetry.py   SQLite writer + stats aggregator
  config.py      Policy loader (Pydantic)
  templates/
    explorer.html
eval/
  fixtures.json  Labeled prompts (expected tier)
  run_eval.py    Harness
policy.yaml      Tiers, rules, classifier, baseline, budget
.env.example     Provider keys + paths
```

## Roadmap (post-MVP)

- Next.js dashboard (charts, session diff, model mix over time)
- Chat UI for non-technical users
- Embedding cache for repeat prompts
- Per-team API keys + quotas
- A/B shadow routing (compare models on the same prompt)
- Streaming token-cost display

## License

TBD.
