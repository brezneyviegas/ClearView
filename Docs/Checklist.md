# ClearView — Work Checklist

Living tracker of outstanding work vs `.claude/Idea.md` and the deck roadmap.
Check items off as they land. Add new items as scope shifts.

Status legend: `[ ]` open · `[~]` in progress · `[x]` done · `[-]` dropped

---

## Priority queue

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

_Last touched: 2026-05-20 (Layer 3 + zero-config setup). Update this date when you change the list._
