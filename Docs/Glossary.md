# ClearView Glossary

Quick reference for the jargon that shows up in code, docs, the deck, and the admin UI.
Grouped by area. Cross-refs in **bold**.

---

## Routing

### Tier
A capability bucket of models, ordered cheap → mid → frontier. Defined in `policy.yaml`.
The router picks a **tier** first, then a specific model inside it via the **health filter**.
- `cheap`: Haiku, gpt-4o-mini, Gemini Flash, Ollama
- `mid`: Sonnet, gpt-4o, Gemini Pro
- `frontier`: Opus, gpt-4o

### Rule layer
Deterministic top-down rules in `policy.yaml` under `rules:`. First match wins.
Examples: token-length thresholds, keyword matches (`refactor`, `architect`…), explicit
`x-clearview-tier` header override. Fast, no LLM call.

### Classifier fallback
When no rule matches, a single Haiku call rates the prompt complexity 1–5.
Score maps to a tier via `classifier.score_to_tier`. Cheap insurance for ambiguous prompts.

### Health filter
Drops models whose provider API key is missing from env. Ollama is always considered
available (local). Runs after tier selection so we never pick a model we can't actually call.

### Pick
Within the chosen tier, take the first healthy model. If empty, escalate up one tier
(cheap → mid → frontier) until a model is available.

### Escalation
Re-pick on failure. Two triggers, both controlled by `escalation:` in policy:
1. **On error** — upstream raised.
2. **On empty response** — upstream returned no content.
Capped by `max_retries`. The retry row in telemetry gets `escalated=1`.

### Dispatcher
`_call_upstream()` in `app/main.py`. Decides per-call whether to invoke:
- the **Claude CLI** (subscription mode, when `CLEARVIEW_USE_CLAUDE_CLI=1` and model is `anthropic/*`), or
- **litellm** REST (everything else).

### Route reason
Human-readable string stored on each CallRecord explaining *why* this tier was picked
(`rule:tiny_prompt`, `classifier:score=4`, `header_override`, etc.). Surfaced in the
per-call modal.

---

## Upstream / Shadow

### Upstream
The actual LLM provider call that fulfils a request. "Primary upstream" is the routed
model the client gets back. "Shadow upstream" is the comparison call (see below).

### Shadow (route / task / `shadow_of`)
A/B comparison feature. Client sends header `x-clearview-shadow: <tier>`. ClearView:
1. Serves the primary response as normal (zero added latency).
2. Fires the **same prompt** at the shadow tier via `asyncio.create_task` after responding.
3. Logs a second CallRecord with `shadow_of = <primary request_id>` and `consensus_flag = 1`.

`/admin/shadow_compare` joins primary↔shadow rows on `shadow_of` to show paired cost/latency
diffs. Used to answer questions like "could Haiku replace Sonnet on this workload?"

### Consensus flag
Boolean on shadow rows. Marks the row as a paired comparison entry rather than a real
user-facing call. Lets the UI filter shadow noise out of headline KPIs.

---

## Cost accounting

Three modes — every call resolves into one of them. The cost columns differ per mode.

### Native cost (`native_cost_usd`)
Real money actually spent on this call.
- Paid API mode: real `$` from litellm's cost table.
- Subscription mode: **`0`** — the Pro/Max sub already paid for it.
- Cache hit: **`0`**.

### Synth cost (`synth_cost_usd`)
"Synthetic" or notional — what the call *would* have cost on the pay-per-token API.
Only meaningful in **subscription mode**: shows the dollar value extracted from the sub.
- Paid API mode: `0` (native is the real number).
- Subscription mode: notional API price.
- Cache hit: `0`.

### Plan-equiv cost (`plan_equiv_cost_usd`)
What this same call would have cost if routed to the **baseline model** (always-Opus by
default; configurable via `baseline_model` in policy). The denominator for **drift %**.
Preserved on cache hits so a cache replay still counts toward savings.

### Drift %
`(plan_equiv − native) / plan_equiv × 100`, or `(plan_equiv − synth) / plan_equiv × 100`
in subscription mode. The percentage of always-frontier spend you avoided by routing
smartly. 100% means the call was free; 0% means you used the baseline anyway.

### Baseline model
The yardstick used for drift % math. Set via `baseline_model` in `policy.yaml`. Change
it to compare savings against a different "what if we just used X for everything?" world.

### Subscription mode
Set `CLEARVIEW_USE_CLAUDE_CLI=1`. Anthropic models route through the locally installed
`claude` CLI subprocess (using a Pro/Max plan) instead of the REST API. `native_cost_usd`
goes to `0`; `synth_cost_usd` shows what API would have charged. Streaming works via
`claude --output-format stream-json`.

---

## Cache

### Prompt cache (exact-match)
`sha256(messages + model + temperature + team_id)` → cached response. TTL configurable.
On hit: `native = 0`, `synth = 0`, `plan_equiv` preserved → drift = 100% logged as full
savings. Per-team scoped — no cross-team prompt replay.

### Prompt hash (`prompt_hash`)
The sha256 key above. Surfaced in the per-call modal for ops to spot dedup-worthy
patterns.

---

## Budget / multi-tenant

### Budget gate
Pre-flight check before each call. Sums today's `native_cost_usd` and compares against
`budget.daily_usd_cap`. On breach:
- `reject` (default) → `429 Too Many Requests` + `Retry-After`.
- `warn` → log only.
- `allow` → no-op.

### Team
Multi-tenant tenant. Created via `POST /admin/teams`. Each team gets a bearer token
(`cv_team_<32 hex>`) clients send as `Authorization: Bearer …`.

### Quota
Per-team `daily_usd_cap` + `monthly_usd_cap`. Enforced like the global budget gate but
scoped to `team_id`. Breach → 403.

### Allowed tiers
Per-team gate — list of tier names the team may use. If the router resolves to a
disallowed tier, the request is rejected with 403 before any upstream call fires.

---

## Telemetry / dashboard

### CallRecord
One row per upstream call in the SQLite `calls` table. Schema lives in `app/telemetry.py`.

### `request_id` / `session_id`
- `request_id`: unique per HTTP call into ClearView. Also the join key for shadow pairs.
- `session_id`: client-supplied (or per-process) grouping. Used for "session burn" rollups.

### `picked_*` columns
`picked_tier`, `picked_provider`, `picked_model` — the resolved routing decision logged
on the row.

### `output_cost_per_1k`
Per-1k-token output price for the picked model. Drives the candle chart Y-axis.

### Burn rate
`$ / minute` ticking live in `/admin/ticker`. Three series — native, synth, plan-equiv —
plus a "savings rate" delta.

### Candle (OHLC)
Open/High/Low/Close chart on `output_cost_per_1k` per model per time bucket. Spots
price spikes (e.g. provider raised prices, or routing started picking a pricier model).

### Leaderboard
Two ranked lists in the ticker: top spenders (highest native) and top savers (highest
drift %).

### Ticker tape
Scrolling band of model symbols at the top of `/admin/explorer`. ▲▼ vs prior window.
Click a symbol → drill-down modal for that model.

---

## Eval

### Fixtures (`eval/fixtures.json`)
Labelled prompts with expected tier. 51 today. Cover the rule paths and the classifier
fallback path.

### Eval harness (`eval/run_eval.py`)
Runs every fixture through the router (default: dry — no upstream calls, classifier
deterministic-stubbed). Reports routing accuracy, rule-hit rate, drift %.

### Gate (`eval/gate.json`)
CI thresholds: minimum accuracy, minimum drift %, maximum cost. `run_eval --gate`
exits non-zero on regression, failing CI.

---

## Misc

### Provider
The vendor behind a model: `anthropic`, `openai`, `gemini`, `ollama`. Slash-prefixed in
config (`anthropic/claude-haiku-4-5`).

### `escalated`
Boolean on a CallRecord. `1` means this row is the retry that succeeded after a primary
failed/empty response. Useful for spotting flaky upstreams.

### `status`
String — `ok`, `error`, `empty`. The terminal state for the upstream call.

### `x-clearview-tier`
Request header — explicit tier override. Skips the rule + classifier layers entirely.

### `x-clearview-shadow`
Request header — triggers a **shadow** call against the named tier. See above.

### `CLEARVIEW_ADMIN_TOKEN`
Env var. Bearer token guarding `/admin/*` endpoints. `/metrics` stays open per
Prometheus convention.
