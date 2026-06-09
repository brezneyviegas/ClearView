# ClearView — Work Checklist

Living tracker of outstanding work vs `.claude/Idea.md` and the deck roadmap.
Check items off as they land. Add new items as scope shifts.

Status legend: `[ ]` open · `[~]` in progress · `[x]` done · `[-]` dropped

---

## Priority queue

- [x] **16. OSS onboarding — fresh clone to running in 1–2 commands.** (2026-06-09)
  - [x] Fresh-user simulation: cloned to /tmp, followed README with zero
        keys/CLIs. Friction found: stock `python3` was 3.10 vs required 3.11+
        (install fails); app INFO logs invisible under plain uvicorn (startup
        hints swallowed by root WARNING level).
  - [x] `Makefile`: `make setup` (picks newest python ≥3.11, venv, deps,
        .env from example, version guard with clear error), `make run`,
        `make doctor`, `make test`, `make clean`.
  - [x] `app/__main__.py`: `python -m app` entrypoint honouring
        CLEARVIEW_HOST/PORT.
  - [x] `.env.example` rewritten: all knobs documented (CLI adapters, ollama
        probe, mock, caches, provider learning/scoring, admin token), optional
        ones commented out, safe zero-config defaults.
  - [x] `Dockerfile` + `docker-compose.yml` (`--profile local` adds ollama).
        Verified: image builds, container serves mock/echo with no providers.
  - [x] First-run UX: lifespan attaches an INFO handler to the clearview
        logger (when operator hasn't configured logging) and logs explorer/chat
        URLs + a doctor hint when no real provider is reachable.
  - [x] README Quick Start rewritten: make path, manual path, docker path,
        python version stated, zero-config promise up front.
  - [x] Verified end-to-end on fresh clone: `make setup` → `python -m app` →
        /health ok, /v1/chat/completions served (by local ollama via probe —
        zero-key machine with ollama gets free local model; without ollama,
        mock), explorer + chat 200. Suite 359 pass.

- [x] **14. Plan/execute stage routing** (frontier plans, local ollama executes).
  - [x] `StagesCfg` in config (`stages:` block — enabled/plan/execute/auto_detect);
        `local` tier (`ollama/llama3.2`) added to policy.yaml + `_TIER_ORDER`
        (below cheap: normal traffic never drops in, execute starts there).
  - [x] `router.detect_stage(messages, header, policy)`: explicit
        `x-clearview-stage: plan|execute` header wins; auto-detect flags
        execute when history carries tool-role messages or assistant
        tool_calls (agent loop = plan already made). `route(..., stage=)`
        maps stage→tier, reason `stage:plan|execute`. `x-clearview-tier`
        still overrides everything.
  - [x] Runtime ollama health probe (`CLEARVIEW_OLLAMA_PROBE=1`, 30s TTL,
        urllib on `OLLAMA_BASE_URL/api/tags`): ollama down → local tier empty
        → execute escalates up to cheap cloud instead of erroring.
  - [x] main.py wiring; stage-routed turns skip `would_have_tier` +
        auto-shadow (deliberate picks, not complexity judgements — avoids
        cloud shadow on every high-volume execute turn).
  - [x] Quality gate verified: main.py had its own `_TIER_ORDER` without
        "local" — `_next_tier("local")` returned None so weak local output
        never escalated. Fixed: main now imports router's ladder. E2e test:
        ollama refusal → retried one tier up → `quality_escalated` in
        route_reason.
  - [x] End-to-end HTTP tests: `x-clearview-stage` header (plan + execute),
        auto-detect from tool history, tier-header precedence, quality
        escalation — 5 tests through `/v1/chat/completions` with the real
        policy.yaml.
  - [x] Stage header documented in `Docs/IDE_SETUP.md` (new section).
  - [-] Telemetry `stage` column: skipped — route_reason carries `stage:*`,
        explorer shows it per-call; add a column only if rollups ever needed.
  - [x] 27 tests (`tests/test_stage_routing.py`). 359 pass.
  - [x] Live-verified (2026-06-09, real server + real models): `plan` header →
        frontier (claude-opus-4-8 via CLI); `execute` header → local
        (ollama/llama3.2, real generation); auto-detect from tool-call
        history → local. Ollama started on the machine for this.

- [x] **15. Carried-over fixes.**
  - [x] `_provider_shadow_alt` now rotates through ALL alternate providers
        (random pick over one candidate per provider — Gemini gets sampled
        in a 3-provider tier).
  - [x] `_AVAILABLE` test-isolation bug: autouse conftest fixture
        saves/restores `router._AVAILABLE` per test (kills order-dependent
        failures between test_api.py and test_router.py).

- [x] **12. Quality-learned provider selection** (route to the right *provider*,
  not just the right tier — "sometimes Gemini, sometimes Codex").
  - [x] **P1 foundation:** `provider_score` table `(bucket,provider)→wins/losses/ties/n`;
        `telemetry.best_provider()` (cached); `router.bucket_for(tier,reason)`
        (`tier:route_reason_family`); `_pick_model(tier,policy,bucket)` prefers the
        learned-winner provider among *available* models in the tier, cold-start
        safe. Env `CLEARVIEW_PROVIDER_LEARNING=1`, `CLEARVIEW_PROVIDER_MIN_N=8`.
        Zero behavior change unless flag + data. 11 tests.
  - [x] **P2 capture:** provider-level auto-shadow — shadow an alternate available
        provider in the SAME tier, LLM-judge winner, tally both into
        `provider_score[bucket]`. `CLEARVIEW_PROVIDER_SHADOW=1` (+ `_RATE`), needs
        judge on. Extends `_run_shadow` (override + score_bucket). 3 tests + live
        closed-loop verify (cold→gemini after 8 judged wins). 297 pass.
  - [x] **P3 close the loop:** thumbs feedback attributed to the serving provider
        feeds `provider_score` (up=win, down=loss); `/admin/provider_scores`
        endpoint (grouped by bucket) + explorer **PROVIDER LEARNING** panel with
        per-bucket win-rate bars. 4 tests. 301 pass.

- [x] **11. IDE / client onboarding flow.** (verified end-to-end live)
  - [x] OpenAI-compatible entry point + dummy client key accepted; `clearview-auto`
        routes via rules/classifier to a real configured LLM (non-stream + stream).
  - [x] Per-extension config generator: `python -m app.doctor --ide
        <continue|cline|cursor|aider|openai|anthropic|gemini>` emits paste-ready
        snippets (Continue YAML + Cline JSON validated by tests).
  - [x] Routing visibility: `x-clearview-tier|model|request-id` headers on all
        chat responses; documented in `Docs/IDE_SETUP.md`.
  - [x] Optional gateway lock: `CLEARVIEW_CLIENT_KEYS` allow-list (`_client_key_allowed`);
        `cv_team_*` bearers + `/chat` cookies always allowed; unset = open dev.
  - [x] 8 tests (IDE config + client-key gate) in `tests/test_setup.py`. 283 pass.
  - Manual GUI step (install Continue/Cline and click) left to the user; the exact
    request those extensions send is verified live against the gateway.

- [x] **13. "Stock-market" composite provider scoring.**
  - [x] `provider_score` now accumulates `sum_cost/sum_latency_ms/sum_tokens_out`
        alongside wins/ties (self-contained — fixes the broken calls-table join
        where shadow rows carried a different route_reason).
  - [x] `app/scoring.py`: per-bucket composite multiplier = weighted blend of
        normalized quality + cost + latency + burn (each min-max'd across the
        bucket's providers, lower-is-better inverted). Env weights
        `CLEARVIEW_SCORE_W_{QUALITY,COST,LATENCY,BURN}` (default .5/.25/.15/.10).
  - [x] Router `_select_from`: composite (if `CLEARVIEW_PROVIDER_SCORING=1`) →
        win-rate → first-listed. Shadow + feedback both feed metrics.
  - [x] `/admin/provider_scores` returns composite + normalized breakdown;
        explorer PROVIDER LEARNING panel shows the ×multiplier + q/$/l/b.
  - [x] 10 tests (`tests/test_scoring.py`). 311 pass. Verified on real telemetry.
  - Carried-over fix still open: rotate `_provider_shadow_alt` through ALL
    alternates (Gemini still never sampled in a 3-provider tier).
  - Evolve provider learning beyond win/loss into a **total score per provider
    per bucket** blending: quality (judge win-rate), **cost** (native/synth $),
    **latency** (ms), and **token burn** (tokens per useful answer).
  - Combine into a single weighted score → a **multiplier** that ranks providers;
    the multiplier defines the route path (like a ticker pick). Tunable weights
    (`CLEARVIEW_SCORE_W_QUALITY/COST/LATENCY/BURN`).
  - Data already captured per call in telemetry (`native_cost_usd`,
    `synth_cost_usd`, `latency_ms`, `tokens_in/out`) + `provider_score`
    (quality). Need: aggregate per (bucket, provider) → normalized sub-scores →
    weighted total → feed `_pick_model`/`best_provider`.
  - Surface in explorer PROVIDER LEARNING panel: per-provider score breakdown
    (quality/cost/latency/burn) + the composite multiplier.
  - Prereq fix carried over: rotate `_provider_shadow_alt` through ALL alternate
    providers (today only the first non-served one — Gemini never sampled in a
    3-provider tier).

- [x] **10. Widen classifier reach — narrow `medium_prompt` catch-all.**
  - `medium_prompt` narrowed `tokens_lt: 1500` → `tokens_lt: 500, no_code: true`.
    Prompts 500+ tokens (or shorter but code-bearing) that no rule catches now
    reach the classifier instead of being floored to cheap. All
    medium_prompt-dependent eval fixtures were <200 tokens (tiny_prompt covers
    them) — no fixture churn needed.
  - Eval gate re-run: PASS — 50/51 overall (98.0%), rule layer 100%. Full
    suite 359 pass.

- [x] **9. Adapt to user's setup (zero-config) + setup doctor.**
  - [x] Built-in mock/echo provider (`app/providers/mock.py`): canned $0
        responses, always callable, no keys/CLI/ollama. `CLEARVIEW_USE_MOCK=1`
        routes everything to it (offline demo); non-stream + stream + pricing $0.
  - [x] Graceful tier fallback (`router._pick_model`): escalate up → drop down →
        any declared tier → mock. App never dead-ends on missing providers.
  - [x] On-failure mock fallback (`main`): upstream error with no real
        escalation target serves the mock instead of 502 (default on;
        `CLEARVIEW_MOCK_ON_FAILURE=0` restores hard 502).
  - [x] Setup doctor (`app/doctor.py`): probes keys, CLIs (claude/codex/gemini),
        ollama; reports availability + targeted recommendations; generates a
        tailored policy.yaml (prune unreachable models, backfill empty tiers
        with mock, disable unreachable classifier). `python -m app.doctor
        [--json|--write --out]`. `/admin/setup` returns the report + tailor notes.
  - [x] 15 tests (`tests/test_setup.py`) + reworked upstream-error tests. 275 pass.


- [x] **1. Test coverage for chat + codex_cli** — `tests/test_chat.py` (22 tests)
  + `tests/test_codex_cli.py` (18 tests). Full suite: 137 passing.
  - [x] Unit: `chat.py` table CRUD (create, list, append, delete, scoping)
  - [x] Route: `/chat/login` → cookie set, `/chat/logout` → cleared
  - [x] Route: `/chat/conversations` CRUD scoped per team
  - [x] Route: `/chat/conversations/{cid}/send` happy path with stubbed upstream
  - [x] Auth: cookie fallback in `_resolve_team` when no Authorization header
  - [x] `codex_cli._parse_events` against canned NDJSON
  - [x] `codex_cli.completion` with mocked subprocess.run
  - [x] `codex_cli.acompletion` with mocked asyncio.create_subprocess_exec
  - [x] Gating + model-prefix availability checks

- [x] **2. Gemini CLI subscription bypass** — parity with Claude + Codex.
  - [x] `app/providers/gemini_cli.py` (`gemini -p "<prompt>" -o json -m <model>`)
  - [x] Wire into `_call_upstream` / `_acall_upstream`
  - [x] Router: `gemini/*` returns available when `CLEARVIEW_USE_GEMINI_CLI=1`
  - [x] `_finalize_non_stream` recognises `_clearview_via=gemini_cli`
  - [x] Smoke test through `/v1/chat/completions` + `/chat`

- [x] **3. Embedding / semantic prompt cache** — `app/embeddings.py` +
  `cache.semantic_lookup()`. Configurable backend (`openai` via litellm or
  `local` via lazy-loaded sentence-transformers). 0.95 default cosine
  threshold. Per-team scoped. Surfaced as `semantic_hits` +
  `semantic_savings_usd` in `/admin/stats`.
  - [x] Configurable backend (openai|local|disabled)
  - [x] `embedding BLOB` column on `prompt_cache` (idempotent migration)
  - [x] Threshold + scan-limit env knobs
        (`CLEARVIEW_SEMANTIC_THRESHOLD`, `CLEARVIEW_SEMANTIC_SCAN_LIMIT`,
        `CLEARVIEW_SEMANTIC_CACHE=0` to disable)
  - [x] Per-team scope preserved (cosine pass filtered by team_id)
  - [x] Stats: `semantic_hits` + `semantic_savings_usd` separate from exact
  - [x] Tests: 7 embeddings + 11 semantic-cache (incl. end-to-end paraphrase
        intercept). Full suite 158 → 183.

- [x] **4. Streaming in `/chat` UI** — new `POST /chat/conversations/{cid}/send_stream`
  endpoint. Forwards upstream chat.completion.chunk SSE through, then emits a
  custom `{"type":"metadata", ...}` event with per-turn cost before `[DONE]`.
  - [x] `send_stream` route that wraps `_handle_chat_completions(..., stream=True)`
  - [x] SSE consumer in `chat.html` (vanilla `fetch` reader loop)
  - [x] Live token render in assistant bubble
  - [x] Metadata event hydrates footer + session cost
  - [x] `x-clearview-request-id` header surfaced on streaming response so the
        chat send_stream can hydrate cost numbers from telemetry
  - [x] 4 new tests (TestChatSendStream): delta+metadata emission, persisted
        assistant message, 404 on unknown conv, 401 without login

- [x] **5. Quality-regression eval** — `eval/quality_eval.py` LLM-as-judge
  module. `python -m eval.run_eval --live --quality` calls routed + baseline
  + judge per fixture, grades 1–5, aggregates avg score + quality_drift_pct.
  - [x] `run_quality(policy, fixtures, judge_model, baseline_model)`
  - [x] Routed/baseline/judge dispatch via `litellm.completion`
  - [x] `_grade()` parses digit, defaults to 3 on flaky judge output
  - [x] Skips fixtures where routed == baseline (no info)
  - [x] `--quality` + `--quality-fixtures` + `--judge-model` CLI flags
  - [x] `min_avg_quality_score` + `max_quality_drift_pct` in gate.json
  - [x] `gate()` honours them only when results contain a `quality` block
        and `--live` is set (backwards-compatible with old gate runs)
  - [x] 13 tests (`tests/test_quality_eval.py`): grade parsing, aggregation,
        same-model skip, perfect-score path, fixture filter, gate pass/fail
        + backward-compatibility

- [x] **6. Routing-accuracy Layer 1 — tighten the pipeline** (start AFTER
  items 1–5). Cheapest near-term wins to drive misroute rate down. Do in
  this order:
  - [x] Word-boundary regex on `contains_any` keyword matcher (fixes
        `refactor` matching `refactoreddata`)
  - [x] Classifier confidence floor — ask Haiku for `score, confidence`;
        escalate one tier when confidence is low
  - [x] Structural rules: detect stack traces, math symbols, file paths,
        URLs, multiline code without fences, imperative vs question shape
  - [x] Refusal / short-output detector → escalate when cheap output
        length ≪ expected
  - [x] `would_have_tier` telemetry column + `/admin/routing_quality`
        page (operator sees disagreement rate between rule-pick and
        classifier-pick over time)

- [x] **7. Routing-accuracy Layer 2 — auto-shadow + judge + hit-rate.**
  - [x] Auto-shadow on rule/classifier disagreement, env-gated
        (`CLEARVIEW_AUTO_SHADOW=disagree`, `CLEARVIEW_AUTO_SHADOW_RATE`),
        manual `x-clearview-shadow` header still wins
  - [x] LLM-as-judge on the pair (`app/shadow_judge.py`), grades shadow vs
        served primary 1-5 → winner primary/shadow/tie. `CLEARVIEW_AUTO_SHADOW_JUDGE=1`,
        `CLEARVIEW_SHADOW_JUDGE_MODEL` (default policy baseline)
  - [x] `shadow_verdict` table + `telemetry.record_verdict()` (misroute corpus)
  - [x] `/admin/shadow_verdicts` — under/over-route rate, by-pair breakdown, recent
  - [x] `/admin/rule_hits` — per-rule fire count + share
  - [x] 17 tests (`tests/test_auto_shadow.py`): gate, judge, verdict storage,
        HTTP auto-trigger, end-to-end judge→verdict
  - [x] Explorer ROUTING QUALITY panel: disagree/under-route/over-route/judged
        cards + per-rule hit-rate bars (`/admin/rule_hits` + `_verdicts`)
  - Note: streaming primaries skip the judge (text not materialized at
    shadow-launch); shadow itself still records.

- [x] **8. Routing-accuracy Layer 3 — embed classifier + feedback + tuner.**
  - [x] Thumbs feedback corpus: `feedback` table + `telemetry.record_feedback()`
        (denormalises tier/reason/prompt_hash from the calls row) +
        `POST /feedback` (client-facing, no admin auth) + `/admin/feedback` summary
  - [x] Embedding classifier (`app/embed_classifier.py`): cosine-weighted kNN
        over labelled corpus (seeded from `eval/fixtures.json`), reuses
        `app.embeddings`. Added as router fallback when LLM classifier disabled,
        env-gated `CLEARVIEW_EMBED_CLASSIFIER=1`. `embed_would_have_tier()` signal.
  - [x] Online tuner (`app/tuner.py`, auto-apply + guardrails): analyses
        feedback down-votes (rule tier bump) + shadow under-route verdicts
        (confidence-floor bump) → mutates policy.yaml. Backs up to
        `policy.yaml.bak.<ts>`, logs to `tuner_log`, `revert()` restores.
        `/admin/tune` (dry-run; POST `?apply=1` applies + hot-reloads policy),
        `/admin/tune/revert`, `/admin/tune/history`. Conservative env thresholds
        (`CLEARVIEW_TUNE_MIN_FEEDBACK/_DOWNVOTE_PCT/_MIN_PAIRS/_UNDER_ROUTE_PCT`).
  - [x] 13 tests (`tests/test_layer3.py`) + end-to-end local verify. 260 pass.
  - Note: with `medium_prompt` catch-all in policy.yaml, the embed-classifier
    fallback only fires for 1500–4000-token non-matching prompts (narrow by
    design — it's a fallback, not the primary path).

  All three routing-accuracy layers (1, 2, 3) now built.

---

## Hardening (done)

- [x] **Classifier silent-swallow** — `_classify` now logs (warning + exc_info)
      on litellm failure before falling back to mid tier. Ops can see classifier
      outages instead of silent tier-3 routing.
- [x] **Soft-cap race documented** — budget enforcement block in `main.py` notes
      the read-check-then-act window (5s TTL cache, cost known only post-call);
      hard cap would need estimate-reserve-reconcile. Accepted for a soft guard.

---

## Idea-listed concerns still open

- [x] **Confidence floor on classifier** — Idea calls out as mitigation for
      misclassified hard prompts. Today classifier always trusts its 1-5 score;
      no threshold-based escalation when the score is "borderline".
- [x] **Routing-overhead p95 benchmark** — Idea target <100ms. Never measured
      in isolation (CLI sub paths inflate end-to-end). Add `performance/`
      micro-benchmark.

---

## Deck-roadmap items not yet built

- [x] Per-team timezone for monthly cap reset
- [x] Spend-vs-cap meter in `/chat` header
- [x] Word-boundary fix in keyword matcher (`contains_any`)
- [x] A/B shadow pairing for streaming primaries
- [-] Next.js read-only dashboard for finance/ops (open question: do we need
      it given server-rendered explorer already exists?)
- [x] Cost-ticker audio bell on price change (trader-vibe polish)

---

## Done (recent)

- [x] Chat UI for non-tech users (`/chat`, cookie auth, sidebar, per-turn cost)
- [x] Codex CLI subscription bypass (`CLEARVIEW_USE_CODEX_CLI=1`)
- [x] Claude CLI subscription bypass (`CLEARVIEW_USE_CLAUDE_CLI=1`)
- [x] Compatibility shims (`/v1/messages`, `/v1/responses`, `:generateContent`)
- [x] Per-team auth + quotas + allowed tiers
- [x] Cost Ticker (tape, burn rate, candles, leaderboard, drill-down)
- [x] Shadow A/B route (`x-clearview-shadow`, `/admin/shadow_compare`)
- [x] Exact-match prompt cache (`prompt_cache` table)
- [x] Glossary doc + Marp deck

---

_Last touched: 2026-06-09 (plan/execute stage routing + carried-over fixes). Update this date when you change the list._
