# ClearView

## One-liner
LLM cost-router middleware. Sits between client (IDE, chat, app) and providers. Routes each prompt to cheapest capable model. Surfaces tokens, cost, latency, limits.

## Problem
- Teams pay flat subs + per-token across many LLM providers.
- Devs/agents pick model by habit, not cost-fit. Frontier model used for trivial prompts.
- Subsidised pricing era ending. Cost discipline now competitive edge.
- No unified view of spend, token burn, latency across providers.

## Solution
Middleware proxy speaking OpenAI-compatible REST. Client points base URL at ClearView. ClearView:
1. Classifies prompt complexity (rules + classifier fallback).
2. Picks model from policy (cheapest tier that meets quality bar).
3. Forwards to provider (Anthropic, OpenAI, Google, Ollama, extensible).
4. Streams response back unchanged.
5. Logs request/response metadata: provider, model, tokens in/out, cost USD, latency ms, route reason.

## Users
- **Devs**: swap `OPENAI_BASE_URL` in Cursor/Continue/SDK. Zero code change.
- **Non-tech**: chat UI on top of same router (post-MVP).
- **Ops/Finance**: dashboard for spend, model mix, savings vs. baseline.

## Workflows
- **Happy path**: trivial prompt (e.g. "rename var") → Haiku/Flash/gpt-4o-mini. Saves 10-50x vs frontier.
- **Negative path**: misclassified hard prompt → cheap model fails or returns weak answer → cost wasted on retry. Mitigation: confidence floor on classifier, escalate-on-failure, per-route quality eval, user override header.

## Differentiation
- Provider-agnostic. Not locked to one vendor.
- Transparent telemetry per call. Show *why* this model was picked.
- Policy as code: org sets price ceilings, allowed models, fallback chains.

## Non-goals (v1)
- Fine-tuning, training, embeddings routing.
- Multi-tenant SaaS billing.
- Prompt caching beyond what providers offer natively.

---

## MVP scope

### Stack
- Python 3.11 + FastAPI
- `litellm` for unified provider calls (handles auth, streaming, cost tables)
- SQLite for telemetry (Postgres later)
- Pydantic for config/policy schema
- Pytest for eval harness

### Endpoints (OpenAI-compatible)
- `POST /v1/chat/completions` — main route, streaming + non-streaming
- `GET /v1/models` — lists virtual models (e.g. `clearview-auto`, `clearview-cheap`, `clearview-quality`)
- `GET /admin/stats` — cost/token/latency rollups (JSON)

### Routing pipeline
1. **Rule layer** (fast, deterministic):
   - Token count < 500 + no code blocks → cheap tier
   - Keywords (`refactor`, `architect`, `proof`, `debug this stack trace`) → mid/frontier
   - Explicit override header `x-clearview-tier: cheap|mid|frontier` → bypass
2. **Classifier fallback** (when rules ambiguous):
   - Single Haiku call: "rate complexity 1-5, output digit only"
   - Map score → tier
3. **Model pick from tier**: cheapest available + healthy provider.
4. **Failure escalate**: if response empty/error/below confidence, retry one tier up. Log escalation.

### Providers v1
- Anthropic: Haiku (cheap), Sonnet (mid), Opus (frontier)
- OpenAI: gpt-4o-mini (cheap), gpt-4o (mid/frontier)
- Google: Gemini Flash (cheap), Gemini Pro (mid)
- Ollama: local llama/qwen (free tier, opt-in)

### Telemetry schema
```
request_id, session_id, ts, client_id, virtual_model,
picked_provider, picked_model, route_reason,
tokens_in, tokens_out,
native_cost_usd,           -- actual cost of model picked
plan_equiv_cost_usd,       -- baseline (always-frontier) cost for same call
drift_pct,                 -- (plan_equiv - native) / plan_equiv  (savings %)
output_cost_per_1k,        -- native_cost_usd / (tokens_out / 1000)
latency_ms, escalated, consensus_flag, status, prompt_hash
```

### Config (`policy.yaml`)
```yaml
tiers:
  cheap:  [claude-haiku, gpt-4o-mini, gemini-flash, ollama/qwen]
  mid:    [claude-sonnet, gpt-4o, gemini-pro]
  frontier: [claude-opus, gpt-4o]
rules:
  - if: tokens < 500
    then: cheap
  - if: contains_any [refactor, architect, prove, derive]
    then: mid
classifier:
  model: claude-haiku
  enabled: true
budget:
  daily_usd_cap: 50
  on_breach: reject
```

### MVP cut list (must-have)
1. FastAPI skeleton + `/v1/chat/completions` proxying via litellm
2. Provider keys via env, basic config loader
3. Rule engine + classifier fallback
4. Telemetry write to SQLite (extended schema above)
5. `/admin/stats` JSON endpoint (cost by model, request count, p50/p95 latency)
6. **Cost Explorer UI** (`/admin/explorer`): server-rendered HTML+HTMX page
   - Header KPIs: native total, plan-equiv total, drift % (savings), tokens out, best $/1k out
   - Session selector ("01 - DONE" style) + progress bar
   - Per-call table: voice, model, time, in tok, out tok, native $, plan-equiv $, $/1k out
   - Auto-refresh every 5s while session active
7. Eval script: 50 fixture prompts, compare cost+quality vs always-frontier baseline
8. README with `OPENAI_BASE_URL` swap instructions

### Post-MVP
- Web dashboard (Next.js) reading `/admin/stats`
- Chat UI for non-tech users
- Embedding cache for repeat prompts
- Per-team API keys + quotas
- Streaming token cost display
- A/B mode: shadow-route to compare models
- **Cost Ticker** — Bloomberg-style live market view of token spend (see below)

---

## Cost Ticker (stretch, demo-killer)

Treat models as ticker symbols. Treat token spend as a live market. Operators glance at the explorer and see prices move in real time.

### Concept
Each underlying model = symbol (e.g. `OPUS47`, `SON46`, `HAIKU45`, `4O`, `4OMINI`, `FLASH`, `LLAMA32`). Symbol's "price" = rolling cost per 1k output tokens over last N minutes. Direction arrow vs prior bucket. Volume = calls in window. Live tape scrolls along top of dashboard.

### UI surfaces

1. **Ticker tape** — full-width bar across top of `/admin/explorer`. CSS marquee, ~30s loop. Each cell:
   ```
   OPUS47  $75.00  ▲ 0.4%   |   SON46  $15.00  ▼ 0.1%   |   HAIKU45  $1.25  ─   |   ...
   ```
   Green ▲ for cost rising (bad — burning more), red ▼ for falling (good — cheaper routes winning), grey ─ flat. Pulse animation on price change. Click symbol → drill into model detail.

2. **Burn rate counter** — big neon number, top-right of explorer. `$0.0143 /min` ticking live. Recomputed every 5s from last-60s window. Two flavors side-by-side: `NATIVE` vs `PLAN-EQUIV`. Delta between them = savings rate.

3. **Candle chart per model** — open/high/low/close on cost-per-1k-out, 1-minute candles, last 60 minutes. Wick = min/max in bucket. Body green if avg cost ≤ session median, red if above. Lets ops spot cost spikes (e.g. one bad classifier route burning Opus).

4. **Leaderboard panel** — two columns:
   - **TOP BURNERS**: models with highest absolute spend in session. `OPUS47  $0.412  (3 calls)`
   - **TOP SAVERS**: routes that beat baseline by largest $ delta. `HAIKU45  saved $0.380 vs OPUS47 (12 calls)`

5. **Market open / close indicator** — header pill: `MARKET OPEN · 47 calls today` while requests flowing; `MARKET CLOSED · last trade 14m ago` when idle.

### Data layer

New endpoint: `GET /admin/ticker?window_sec=300` returns:
```json
{
  "tape": [
    {"symbol": "OPUS47", "model": "anthropic/claude-opus-4-7",
     "price_per_1k_out": 75.00, "delta_pct": 0.4, "direction": "up",
     "calls_window": 2, "spend_window_usd": 0.215},
    ...
  ],
  "burn_rate": {"native_per_min_usd": 0.0143, "plan_equiv_per_min_usd": 0.412,
                "savings_per_min_usd": 0.398},
  "candles": {
    "anthropic/claude-haiku-4-5": [
      {"ts": 1778356500, "open": 1.20, "high": 1.30, "low": 1.18, "close": 1.25, "n": 4},
      ...
    ]
  },
  "leaderboard": {
    "burners": [{"symbol": "OPUS47", "spend_usd": 0.412, "calls": 3}, ...],
    "savers":  [{"symbol": "HAIKU45", "saved_vs_baseline_usd": 0.380, "calls": 12}, ...]
  },
  "market_open": true,
  "last_trade_ts": 1778356630
}
```

Cheap aggregation — single SQL pass over `calls` table grouped by `picked_model` + `floor(ts/60)`. Cache 2s in process.

### Symbol mapping
Static dict in code: maps full provider model id → 6-char symbol. Falls back to uppercased last segment + truncate. Visible in explorer detail modal so operators can map back.

### Why this matters
- **Visceral**: stock-market chrome makes cost real. Easier to sell to finance/ops than a flat KPI grid.
- **Diagnostic**: a green candle on Opus during a quiet hour = misroute. Spotting it on a candle chart is faster than scanning 200 rows.
- **Demo win**: differentiator vs OpenRouter/Portkey dashboards (KPI-only).

### Build slice (1-2 days)
1. `/admin/ticker` endpoint + symbol map (1-2h)
2. Marquee tape + burn-rate counter on explorer (1-2h)
3. Candle chart per model (Chart.js financial plugin or hand-rolled SVG, 2-3h)
4. Leaderboard panel + market-open pill (1h)
5. Click-symbol drill-down modal (1h)

Depends on existing telemetry — no schema changes required.

---

## Success metrics (MVP demo)
- 30%+ cost reduction on mixed dev workload vs. always-Sonnet baseline
- <100ms routing overhead (p95) on top of provider latency
- Zero client code changes (just base URL swap)
- Quality regression <5% on eval set vs. always-frontier
