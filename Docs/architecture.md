# ClearView — Architecture

## End-to-end request flow

```mermaid
flowchart TB
    Client["Client<br/>(Cursor / Continue / curl / SDK)<br/>OPENAI_BASE_URL=http://clearview"]

    subgraph CV["ClearView (FastAPI)"]
        EP["POST /v1/chat/completions"]
        Budget{"daily_usd_cap<br/>under limit?"}
        Cache{"exact-match<br/>prompt cache hit?<br/>(sha256 messages+model+temp)"}

        subgraph Routing["Routing pipeline"]
            direction TB
            Rules["1 Rule layer<br/>tokens / keywords / code-fence /<br/>x-clearview-tier header"]
            Classifier["2 Classifier fallback<br/>Haiku rates 1-5 → tier"]
            Health["3 Health filter<br/>keep models whose<br/>provider key is present<br/>(ollama always avail)"]
            Pick["4 Pick first available<br/>in tier (escalate up if empty)"]
        end

        Dispatch{"Dispatcher<br/>_call_upstream()"}
        Shadow["Shadow task<br/>asyncio.create_task<br/>(x-clearview-shadow header)"]
        Escalate{"upstream error<br/>OR empty content?"}
        CacheWrite["write to prompt_cache"]
        Telemetry[("SQLite calls table<br/>request_id, tier, model,<br/>tokens, native$, synth$,<br/>plan_equiv$, drift%,<br/>shadow_of, consensus_flag")]
    end

    subgraph Providers
        CLI["claude CLI<br/>--system-prompt &quot;&quot;<br/>--exclude-dynamic-system-prompt-sections"]
        LiteLLM["litellm.completion()<br/>(REST, pay-per-token)"]
        Sub["Claude Pro/Max subscription<br/>native_cost = $0<br/>synth_cost = notional API price"]
        Anthropic["Anthropic API"]
        OpenAI["OpenAI API"]
        Gemini["Gemini API"]
        Ollama["Ollama (local, free)"]
    end

    subgraph Admin["Admin surfaces"]
        Stats["/admin/stats"]
        Ticker["/admin/ticker<br/>tape · burn rate · candles · leaderboard"]
        TS["/admin/timeseries"]
        Detail["/admin/calls_detail"]
        ShadowCmp["/admin/shadow_compare"]
        Metrics["/metrics<br/>Prometheus"]
        Explorer["/admin/explorer<br/>fetch-poll 5s"]
    end

    Client -- HTTP --> EP
    EP --> Budget
    Budget -- "over → 429 + Retry-After" --> Client
    Budget -- ok --> Cache
    Cache -- hit --> Telemetry
    Cache -- hit --> Client
    Cache -- miss --> Routing
    Routing --> Dispatch

    Dispatch -- "anthropic/* AND<br/>CLEARVIEW_USE_CLAUDE_CLI=1" --> CLI
    Dispatch -- else --> LiteLLM
    CLI --> Sub
    LiteLLM --> Anthropic
    LiteLLM --> OpenAI
    LiteLLM --> Gemini
    LiteLLM --> Ollama

    Sub --> Escalate
    Anthropic --> Escalate
    OpenAI --> Escalate
    Gemini --> Escalate
    Ollama --> Escalate

    Escalate -- "yes + retries left" --> Pick
    Escalate -- ok --> CacheWrite
    CacheWrite --> Telemetry
    CacheWrite --> Client

    Dispatch -. "x-clearview-shadow: tier" .-> Shadow
    Shadow --> LiteLLM
    Shadow --> Telemetry

    Telemetry --> Stats
    Telemetry --> Ticker
    Telemetry --> TS
    Telemetry --> Detail
    Telemetry --> ShadowCmp
    Telemetry --> Metrics

    Stats --> Explorer
    Ticker --> Explorer
    TS --> Explorer
    Detail --> Explorer
```

## Shadow route (concurrent comparison)

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant CV as ClearView
    participant Primary as Primary upstream<br/>(routed model)
    participant ShadowU as Shadow upstream<br/>(header-specified tier)
    participant DB as SQLite

    C->>CV: POST /v1/chat/completions<br/>x-clearview-shadow: frontier
    CV->>Primary: dispatch routed model<br/>(e.g. Haiku via CLI)
    Primary-->>CV: response
    CV->>DB: write primary CallRecord<br/>(request_id=R1, picked_tier=cheap)
    CV-->>C: return response (unchanged latency)
    Note over CV,ShadowU: After response sent —<br/>asyncio.create_task fires
    CV->>ShadowU: litellm.acompletion(<br/>frontier model)
    ShadowU-->>CV: response
    CV->>DB: write shadow CallRecord<br/>(shadow_of=R1, consensus_flag=1,<br/>picked_tier=frontier)
    Note over DB: /admin/shadow_compare<br/>joins on shadow_of →<br/>cost & latency diff pairs
```

## Cost accounting (3 modes)

```mermaid
flowchart LR
    subgraph PaidAPI["Paid API key mode"]
        P1["Anthropic key set"] --> P2["native_cost_usd = real $"]
        P2 --> P3["drift_pct = (plan_equiv - native) / plan_equiv"]
    end

    subgraph SubMode["Subscription mode<br/>(CLEARVIEW_USE_CLAUDE_CLI=1)"]
        S1["Claude Pro/Max sub"] --> S2["native_cost_usd = 0<br/>synth_cost_usd = notional API price"]
        S2 --> S3["drift_pct = (plan_equiv - synth) / plan_equiv<br/>shows what API would have cost"]
    end

    subgraph CacheHit["Prompt cache hit"]
        H1["sha256 match within TTL"] --> H2["native = 0, synth = 0<br/>plan_equiv preserved"]
        H2 --> H3["drift_pct = 100%<br/>full plan-equiv → savings"]
    end
```

## Component map

```mermaid
flowchart LR
    subgraph Code["app/"]
        main["main.py<br/>FastAPI app, endpoints,<br/>dispatcher, budget gate"]
        router["router.py<br/>RouteDecision, rules,<br/>classifier, _pick_model,<br/>build_availability"]
        pricing["pricing.py<br/>litellm cost table +<br/>ollama=$0 short-circuit"]
        telemetry["telemetry.py<br/>schema + migrations,<br/>record, stats,<br/>today_spend, metrics_snapshot"]
        cache["cache.py<br/>exact-match prompt cache<br/>(sqlite + TTL)"]
        cli["providers/claude_cli.py<br/>subscription adapter<br/>subprocess.run + JSON parse"]
        templates["templates/<br/>explorer.html<br/>_empty.html"]
    end

    subgraph Config
        policyf["policy.yaml<br/>tiers + rules +<br/>classifier + budget +<br/>baseline_model"]
        env[".env<br/>provider keys +<br/>CLEARVIEW_* gates"]
    end

    subgraph Eval
        evalf["eval/<br/>fixtures.json +<br/>run_eval.py"]
    end

    subgraph Tests
        tests["tests/<br/>conftest + test_*.py<br/>(monkeypatched litellm)"]
    end

    main --> router
    main --> cache
    main --> telemetry
    main --> pricing
    main --> cli
    router --> pricing
    cache --> telemetry
    policyf --> router
    env --> main
    env --> cli
```

## Surface map (admin)

| Endpoint | Purpose | Used by |
|----------|---------|---------|
| `GET /v1/models` | OpenAI-compat model list (virtual + underlying) | clients |
| `POST /v1/chat/completions` | Main proxy | clients |
| `GET /health` | Liveness | infra |
| `GET /admin/stats` | Aggregate KPIs + last 200 rows | explorer |
| `GET /admin/timeseries` | Per-minute buckets for sparklines | explorer |
| `GET /admin/calls_detail` | Same rows + extra fields for modal | explorer |
| `GET /admin/ticker` | Tape, burn rate, candles, leaderboard | explorer |
| `GET /admin/shadow_compare` | Primary↔shadow pairs joined on `shadow_of` | ops |
| `GET /admin/explorer` | Server-rendered Bloomberg-style page | humans |
| `GET /metrics` | Prometheus text format | scrape |

Admin endpoints respect `CLEARVIEW_ADMIN_TOKEN` (bearer auth). `/metrics` always open per convention.
