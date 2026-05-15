# ClearView — Work Checklist

Living tracker of outstanding work vs `.claude/Idea.md` and the deck roadmap.
Check items off as they land. Add new items as scope shifts.

Status legend: `[ ]` open · `[~]` in progress · `[x]` done · `[-]` dropped

---

## Priority queue

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

- [ ] **5. Quality-regression eval** — eval today only measures routing
  accuracy. Idea success metric: <5% quality regression vs always-frontier.
  - [ ] Capture frontier-baseline responses for each fixture
  - [ ] LLM-as-judge pass (Opus grades cheap-tier response vs frontier)
  - [ ] Add `quality_drift_pct` to gate.json thresholds
  - [ ] CI fails if quality regresses beyond floor

- [ ] **6. Routing-accuracy Layer 1 — tighten the pipeline** (start AFTER
  items 1–5). Cheapest near-term wins to drive misroute rate down. Do in
  this order:
  - [ ] Word-boundary regex on `contains_any` keyword matcher (fixes
        `refactor` matching `refactoreddata`)
  - [ ] Classifier confidence floor — ask Haiku for `score, confidence`;
        escalate one tier when confidence is low
  - [ ] Structural rules: detect stack traces, math symbols, file paths,
        URLs, multiline code without fences, imperative vs question shape
  - [ ] Refusal / short-output detector → escalate when cheap output
        length ≪ expected
  - [ ] `would_have_tier` telemetry column + `/admin/routing_quality`
        page (operator sees disagreement rate between rule-pick and
        classifier-pick over time)

  Layer 2 (later): LLM-as-judge auto-shadow + per-rule hit-rate table.
  Layer 3 (much later): embedding-based classifier + thumbs-up/down
  feedback corpus + online policy tuning.

---

## Idea-listed concerns still open

- [ ] **Confidence floor on classifier** — Idea calls out as mitigation for
      misclassified hard prompts. Today classifier always trusts its 1-5 score;
      no threshold-based escalation when the score is "borderline".
- [ ] **Routing-overhead p95 benchmark** — Idea target <100ms. Never measured
      in isolation (CLI sub paths inflate end-to-end). Add `performance/`
      micro-benchmark.

---

## Deck-roadmap items not yet built

- [ ] Per-team timezone for monthly cap reset (UTC-only today)
- [ ] Spend-vs-cap meter in `/chat` header
- [ ] Word-boundary fix in keyword matcher (`contains_any`)
- [ ] A/B shadow pairing for streaming primaries
- [ ] Next.js read-only dashboard for finance/ops (open question: do we need
      it given server-rendered explorer already exists?)
- [ ] Cost-ticker audio bell on price change (trader-vibe polish)

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

_Last touched: 2026-05-15. Update this date when you change the list._
