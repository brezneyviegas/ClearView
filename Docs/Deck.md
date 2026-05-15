---
marp: true
theme: default
paginate: true
backgroundColor: "#0d0f12"
color: "#e6edf3"
style: |
  section { font-family: ui-sans-serif, system-ui, -apple-system, sans-serif; padding: 60px; }
  h1, h2, h3 { color: #e6edf3; letter-spacing: -0.01em; }
  h1 { font-size: 2.4em; border-bottom: 2px solid #41d985; padding-bottom: 0.2em; }
  h2 { color: #41d985; font-size: 1.6em; }
  code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  pre { background: #14181d; border: 1px solid #1f262d; border-radius: 6px; padding: 16px; font-size: 0.75em; }
  code { background: #14181d; color: #ffb84d; padding: 1px 6px; border-radius: 3px; }
  table { border-collapse: collapse; font-size: 0.85em; }
  th { color: #8b95a1; text-align: left; padding: 8px 12px; border-bottom: 1px solid #1f262d; text-transform: uppercase; font-size: 0.75em; letter-spacing: 0.1em; }
  td { padding: 10px 12px; border-bottom: 1px solid #1f262d; font-family: ui-monospace, monospace; }
  strong { color: #41d985; }
  em { color: #ffb84d; font-style: normal; }
  blockquote { border-left: 3px solid #ff6b9d; padding-left: 16px; color: #8b95a1; }
  .pill { display: inline-block; background: #14181d; border: 1px solid #1f262d; border-radius: 999px; padding: 4px 12px; font-family: ui-monospace, monospace; font-size: 0.7em; color: #8b95a1; }
  .green { color: #41d985; }
  .amber { color: #ffb84d; }
  .pink { color: #ff6b9d; }
---

<!-- _class: lead -->

# ClearView

## LLM cost-router middleware

<span class="pill">✓ MVP</span> &nbsp; <span class="pill">✓ subscription mode</span> &nbsp; <span class="pill">✓ multi-tenant</span>

Routes every prompt to the cheapest capable model.
Surfaces tokens, cost, latency, savings in real time.

---

## The problem

- Teams pay flat subs **+** per-token across many providers
- Devs/agents pick model by **habit**, not cost-fit. Frontier called for trivial prompts.
- Subsidised era ending — cost discipline = competitive edge
- **No unified view** of spend, token burn, latency

> "We don't know how much we'd save if we just used Haiku for renames."

---

## The solution

One drop-in OpenAI-compatible proxy. Set `OPENAI_BASE_URL` and stop overpaying.

```
┌─ Client (Cursor / Continue / curl / SDK) ─┐
                       │
                       ▼
┌─────────────── ClearView ──────────────────┐
│  Rules → Classifier → Health → Pick tier   │
│  Budget gate → Cache → Dispatch → Telemetry│
└──────────┬──────────────────┬──────────────┘
           ▼                  ▼
   Pay-per-token API     Claude CLI sub
   (Anthropic, OpenAI,   (Pro/Max plan,
    Gemini, Ollama)       $0 native)
```

Zero client code changes. Same OpenAI API surface.

---

## Live numbers — today

From the demo session running right now:

| KPI | Value |
|-----|-------|
| Calls | 51 routed (eval) |
| Routing accuracy | **100%** |
| Drift vs always-Opus | **18.9%** (dry) → **96%+** (sub mode) |
| Latency overhead | <100ms p95 routing |
| Cost per call (sub mode) | **$0.00 native** |
| Plan-equiv saved (sub mode) | $0.43/min |

Numbers visible live at `/admin/explorer` →

---

## Architecture (1 of 2)

![h:520](architecture.md)

Request flow: client → budget gate → cache → routing pipeline (rules / classifier / health / pick) → dispatcher (CLI vs litellm) → providers → escalation → cache write → telemetry.

> Full mermaid diagrams in `Docs/architecture.md`.

---

## Architecture (2 of 2)

Cost accounting in **three modes**:

- **Paid API**: `native = real $`, drift % = real savings vs always-frontier baseline
- **Subscription** (`CLEARVIEW_USE_CLAUDE_CLI=1`): `native = $0`, `synth = notional API price` (what API *would* have charged) — drift % = subscription value extracted
- **Cache hit**: `native = $0`, `synth = $0`, `plan_equiv preserved` → drift = 100%, displayed as full savings

> Single table, one row per upstream call. Sub-second SQL aggregates feed Prometheus + Bloomberg-style explorer.

---

## Routing pipeline

1. **Rule layer** (deterministic, fast)
   - tokens < 200 + no code → `cheap`
   - keywords `refactor|architect|debug stack|prove` → `mid`
   - tokens ≥ 4000 → `frontier`
   - header `x-clearview-tier: <tier>` → override

2. **Classifier fallback** (when rules ambiguous)
   - one Haiku call: "rate complexity 1-5"
   - score → tier

3. **Health filter** + escalation up the ladder when keys missing

4. **Empty-response retry** on cheap → bump one tier, capped by `max_retries`

---

## The killer trick — subscription mode

ClearView routes Anthropic models through **the locally installed `claude` CLI** instead of the REST API.

```bash
CLEARVIEW_USE_CLAUDE_CLI=1 uvicorn app.main:app
```

| Mode | Cost per call | Provider lock-in | Rate limits |
|------|--------------|------------------|-------------|
| Pay-per-token API | Real $ | Per-vendor | API quota |
| **Subscription** | **$0 native** | Pro/Max plan only | Sub 5-hour window |

Same model quality. Same response format. Just plumbed through CLI subprocess + JSON parse.

**Streaming works** via `--output-format stream-json` → NDJSON → OpenAI SSE.

---

## Telemetry — what we log

Per-call row in SQLite:

```
request_id, session_id, ts, team_id,
picked_tier, picked_provider, picked_model, route_reason,
tokens_in, tokens_out,
native_cost_usd,         ← real money spent
synth_cost_usd,          ← notional (sub mode shows what API would charge)
plan_equiv_cost_usd,     ← always-Opus baseline for drift % math
output_cost_per_1k, latency_ms,
escalated, consensus_flag, shadow_of, status, prompt_hash
```

Powers `/admin/stats`, `/admin/ticker`, `/admin/timeseries`, `/admin/calls_detail`, `/metrics` (Prometheus), `/admin/shadow_compare`.

---

## The dashboard — Cost Ticker

Bloomberg-style live market view of token spend:

- **Ticker tape** — model symbols scrolling, ▲▼ direction vs prior window, click → drill-down
- **Burn rate** — `$/min` ticking live (native vs plan-equiv vs synth, savings rate)
- **Candle charts** — OHLC on `$/1k out` per model, spot cost spikes
- **Leaderboard** — top burners + top savers
- **Shadow A/B panel** — primary↔shadow pair diff
- **Per-call modal** — tier pills, shadow badges, route reason, prompt hash

Fetch-polling every 5s. PAUSE toggle. Empty states everywhere.

---

## A/B shadow routing

> "How sure are we Haiku could replace Sonnet on this workload?"

```bash
curl -H "x-clearview-shadow: frontier" ...
```

ClearView serves the routed response **and** fires the same prompt against the shadow tier in the background. Both calls logged. Operators see paired diff in `/admin/shadow_compare`.

| Primary | Shadow | Δ Cost | Δ Latency |
|---------|--------|--------|-----------|
| HAIKU45 $0.001 | OPUS47 $0.114 | +$0.113 | -1285ms |

Client sees zero latency impact (fire-and-forget `asyncio.create_task`).

---

## Multi-tenant — per-team quotas

```bash
POST /admin/teams { "name": "acme", "daily_usd_cap": 5.0,
                    "allowed_tiers": ["cheap", "mid"] }
→ id: cv_team_<32 hex>
```

Then every call:

```bash
Authorization: Bearer cv_team_<token>
```

- **Daily + monthly USD caps** per team
- **Allowed tiers** gate — 403 if request resolves to disallowed tier
- **Cache scoped per team** — no cross-team prompt replay
- **Full attribution** — every CallRecord tagged `team_id`
- **`?team=` filter** on all admin views

---

## Exact-match prompt cache

```python
sha256(messages + model + temperature + team_id) → response
```

- TTL configurable (default 1 hour)
- Streaming supported — buffered write, one-chunk SSE replay on hit
- Cache hits log row with `native = $0`, `plan_equiv preserved` → full savings attribution
- `cache_hits` + `cache_savings_usd` in `/admin/stats`

**Demo workload**: agent loops with repeat prompts see ~30-50% extra savings just from this. No semantic match yet — that's Wave 4.

---

## Eval harness + CI gate

51 labelled fixtures (cheap/mid/frontier) covering rules + classifier paths.

```bash
$ python -m eval.run_eval --gate eval/gate.json
Fixtures: 51  (live=False)
Overall accuracy:    51/51  (100.0%)
Rule-layer hits:     49   correct=49   accuracy=100.0%
Drift / savings:     18.9%
[gate] PASS  (eval/gate.json)
$ echo $?
0
```

CI fails on regression: routing accuracy floor, drift floor, cost ceiling. `pytest` test runs the same harness for fast feedback.

---

## Stack — boringly chosen

| Layer | Choice | Why |
|-------|--------|-----|
| API | FastAPI | async, OpenAPI for free |
| Provider abstraction | litellm | one call, all vendors, cost tables built-in |
| DB | SQLite | one file, no infra, fine to ~50M rows |
| Config | YAML + Pydantic | hot-swappable policy.yaml |
| UI | Server-rendered HTML + vanilla JS + Chart.js | zero build step |
| Auth | Bearer tokens | standard, no OAuth complexity |
| Sub bypass | `claude` CLI subprocess | leverages existing investment |

**No new dependencies for any of the features added this week.** Plain Python stdlib + the 5 listed packages.

---

## What's live right now

Pushed to `github.com/brezneyviegas/ClearView`:

- **Process entry**: `uvicorn app.main:app` (single file, 1000 lines)
- **HTTP**: 10 endpoints (3 client-facing, 7 admin)
- **Tests**: 60+ pytest tests, monkeypatched litellm, no network
- **Eval**: 51-fixture harness w/ CI gate
- **Docs**: 4 mermaid architecture diagrams in `Docs/architecture.md`
- **Skill**: `/journal` Claude Code skill auto-writes session entries

Built across **3 waves of parallel agents** (~2 days elapsed).

---

## Roadmap

Near term (1-2 weeks):

- **Embedding cache** — semantic match for paraphrased prompts (+30-50% savings on agent loops)
- **Frontend team selector** — dropdown + spend-vs-cap meter in explorer header
- **Cost Ticker polish** — tier-aware audio bell on price changes (trader vibe)
- **Word-boundary fix** in keyword matcher

Mid term:

- Next.js dashboard for finance/ops (read-only, chart-heavy)
- Chat UI for non-technical users
- Per-team timezone for monthly cap reset
- A/B shadow streaming pairing

---

## What I want from you

1. **Pilot one workload** through ClearView this week. Cursor + Continue + agent loops are the cheapest to swap.
2. **Open question**: do we self-host a single instance or offer a SaaS slice? Multi-tenant is built, infra cost is near-zero.
3. **Headcount ask**: half a sprint of design help on the dashboard charts. Backend is done.

> Goal: 30%+ verified savings on dev workload within 30 days, with zero developer friction. Eval gate keeps regression at bay.

---

<!-- _class: lead -->

# Demo time

```bash
# Point your IDE at:
export OPENAI_BASE_URL=http://localhost:8000/v1

# Watch live:
open http://localhost:8000/admin/explorer
```

Questions?

`github.com/brezneyviegas/ClearView`
