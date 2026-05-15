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

- [ ] **2. Gemini CLI subscription bypass** — parity with Claude + Codex.
  - [ ] `app/providers/gemini_cli.py` (`gemini -p "<prompt>" -o json -m <model>`)
  - [ ] Wire into `_call_upstream` / `_acall_upstream`
  - [ ] Router: `gemini/*` returns available when `CLEARVIEW_USE_GEMINI_CLI=1`
  - [ ] `_finalize_non_stream` recognises `_clearview_via=gemini_cli`
  - [ ] Smoke test through `/v1/chat/completions` + `/chat`

- [ ] **3. Embedding / semantic prompt cache** — biggest savings lever still
  on the table. Idea estimates +30–50% on agent loops.
  - [ ] Pick embedding source (`text-embedding-3-small` via OpenAI? local
        sentence-transformers? configurable)
  - [ ] Schema: vector blob column on `prompt_cache` or sibling table
  - [ ] Similarity threshold + TTL knobs in policy.yaml
  - [ ] Per-team scope preserved
  - [ ] Stats: surface semantic hit rate separately from exact-match
  - [ ] Cache-bust path when team policy / model changes

- [ ] **4. Streaming in `/chat` UI** — currently send is non-stream one-shot;
  matches Idea's "streaming token cost display" goal.
  - [ ] Wire `/chat/conversations/{cid}/send` to optionally stream
  - [ ] SSE consumer in `chat.html` (vanilla `fetch` reader loop)
  - [ ] Live token render in assistant bubble
  - [ ] Running per-turn $ updates as deltas arrive

- [ ] **5. Quality-regression eval** — eval today only measures routing
  accuracy. Idea success metric: <5% quality regression vs always-frontier.
  - [ ] Capture frontier-baseline responses for each fixture
  - [ ] LLM-as-judge pass (Opus grades cheap-tier response vs frontier)
  - [ ] Add `quality_drift_pct` to gate.json thresholds
  - [ ] CI fails if quality regresses beyond floor

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
