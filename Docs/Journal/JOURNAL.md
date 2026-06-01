# 2026-05-21 00:01
## Completed
- Routing-accuracy **Layer 2** (auto-shadow + LLM judge): shadow on rule/classifier disagreement, judge grades shadow vs served 1-5 → `shadow_verdict` misroute corpus. `/admin/shadow_verdicts`, `/admin/rule_hits` + explorer ROUTING QUALITY panel. (commits e36cbed, f410d3d)
- Routing-accuracy **Layer 3**: thumbs feedback corpus (`feedback` table, `POST /feedback`); embedding classifier (`app/embed_classifier.py`, kNN over fixtures, router fallback); online tuner (`app/tuner.py`, auto-apply policy.yaml w/ backup+revert, `/admin/tune*`). (commit caa45ad)
- **Zero-config setup**: built-in mock provider (`app/providers/mock.py`, $0 always-callable), graceful tier fallback (up→down→mock), on-failure mock fallback, setup doctor (`app/doctor.py` probe + tailored policy + `--ide` config generator) + `/admin/setup`. (commit 007fcb7)
- **IDE onboarding flow** verified live: dummy client key accepted, `clearview-auto` routes to real LLM (non-stream+stream); per-extension config generator; `x-clearview-tier|model` headers; optional `CLEARVIEW_CLIENT_KEYS` gateway lock. Wired Continue config at `~/.continue/config.yaml`.
- **Quality-learned provider selection** (P1-P3): `provider_score` table + `best_provider()` + `bucket_for()`; `_pick_model` prefers learned-winner provider, cold-start safe; provider-level auto-shadow + judge writes scores; thumbs feedback feeds scores; `/admin/provider_scores` + explorer PROVIDER LEARNING panel. Live closed-loop verified (cold→winner after judged wins).
- **Fixed IDE-context routing bug**: routing + short-output escalation now key off the latest user turn, not the IDE-injected context bulk. "how many days in a week" + 12k ctx → was Opus, now Haiku (cheap).
- **Fixed classifier + judge in subscription mode**: both called litellm directly → 401 in CLI-only mode. New `app/llm_dispatch.py` shares CLI-aware dispatch. Classifier now returns real scores via CLI; judge produces verdicts.
- Confirmed all 3 subscription CLIs work (claude/codex/gemini return PONG); enabled provider learning in `.env`; live traffic shows Codex A/B-tested every request.
- Test hygiene: hermetic `conftest` fixture clears ambient `.env` flags (litellm `load_dotenv` leak). Built showcase deck (`Docs/showcase.html`). Suite 230→**301 passing**.

## Next steps
- **#13 stock-market composite scoring** (tomorrow): blend quality+cost+latency+token-burn → weighted total → multiplier defines route. Data already in telemetry.
- Rotate `_provider_shadow_alt` through ALL alternates — today only first non-served (Gemini never sampled in 3-provider tier).
- #10 narrow `medium_prompt` catch-all so classifier drives more routing.
- Commit the large uncommitted batch (provider learning, IDE flow, mock/doctor, fixes) — many files dirty.
- Lower `CLEARVIEW_PROVIDER_SHADOW_RATE` from 1.0 — every request = 3 CLI calls.

## Files changed
- `app/router.py` — `_pick_model(bucket)` learned pick, `bucket_for`, embed-classifier fallback, classifier via `llm_dispatch`, last-user-turn routing
- `app/telemetry.py` — `shadow_verdict`, `feedback`, `tuner_log`, `provider_score` tables + helpers (`best_provider`, `record_provider_outcome`, `record_feedback`, `record_verdict`)
- `app/main.py` — auto-shadow + provider-shadow + judge wiring, `/feedback`, `/admin/{setup,tune,tune/revert,tune/history,provider_scores,feedback,shadow_verdicts,rule_hits}`, mock dispatch + on-failure fallback, client-key gate, last-user-turn routing/escalation fix
- `app/shadow_judge.py` (new), `app/embed_classifier.py` (new), `app/tuner.py` (new), `app/doctor.py` (new), `app/providers/mock.py` (new), `app/llm_dispatch.py` (new)
- `app/pricing.py` — mock/* = $0
- `app/templates/explorer.html` — ROUTING QUALITY + PROVIDER LEARNING panels
- `tests/` — `test_auto_shadow.py`, `test_layer3.py`, `test_setup.py`, `test_provider_learning.py` (new); hermetic-flags fixture in `conftest.py`
- `Docs/` — `Checklist.md`, `IDE_SETUP.md`, `showcase.html`, `Journal/JOURNAL.md`
- `.env`, `~/.continue/config.yaml` — enabled 3 CLIs + provider learning; Continue model entry

## Open questions / blockers
- Big uncommitted batch on `main` (committed: Layers 2/3 + setup; NOT committed: IDE flow, provider learning P1-P3, classifier/judge CLI fix, routing fix, showcase). Commit before tomorrow.
- Provider learning all-ties on trivia (judge can't separate equal answers) → routing stays anthropic; needs prompts where providers differ to demonstrate a flip.
- `CLEARVIEW_PROVIDER_SHADOW_RATE=1.0` burns 3× subscription calls per request — demo only.

---

# 2026-05-11 20:44

## Completed
- Wave 2 (backend agent): Claude CLI streaming via `--output-format stream-json --include-partial-messages`, NDJSON → SSE chunks
- Routed shadow + escalation paths through `_call_upstream`/`_acall_upstream` dispatcher (CLI in sub mode, litellm otherwise)
- Pre-allocated `request_id` at top of `chat_completions` → streamed primaries now properly link shadow rows via `shadow_of`
- Buffered streaming cache: accumulates SSE deltas, writes once; cache-hit on `stream:true` synthesizes one-chunk SSE replay
- Wave 2 frontend: TIER column w/ colored pills (cheap=green/mid=amber/frontier=pink/cache=grey), SHADOW badge on `consensus_flag=1` rows w/ dimmed opacity, A/B SHADOW COMPARE panel pairing primary↔shadow, tier dots on tape cells, colored MARKET DEPTH labels by tier
- Pushed initial commit to git@github.com:brezneyviegas/ClearView.git main (29 files)
- Wave 3 (3 parallel agents): per-team API keys + quotas, eval expansion 10→51 fixtures + gate, ticker drill-down panel
- Teams: `cv_team_<hex>` Bearer tokens, daily+monthly USD caps, allowed_tiers gate (403 on disallowed), cache key scoped by team_id, attribution on every CallRecord. Admin CRUD endpoints `POST/GET/PATCH/DELETE /admin/teams`. All admin endpoints accept `?team=` filter
- Eval: 26 cheap + 15 mid + 10 frontier fixtures, 100% dry routing accuracy, gate.json thresholds + `--gate` flag + json results dump + pytest test (3 passed)
- Ticker drill-down: 440px slide-out panel from ticker/depth/leaderboard click, mini candle w/ current-price dashed line, trade tape (last 20 calls for that model), THIS/CHEAPEST/BASELINE compare table, Esc + scrim close

## Next steps
- Embedding cache (semantic match) — 30-50% extra savings on agent loops, needs sentence-transformers or Haiku embedding dep
- Frontend per-team header w/ team selector dropdown + spend-vs-cap meter
- Backend: `calls_detail` SELECT add `synth_cost_usd` so trade tape "synth $" column populates
- Word-boundary regex in `_contains_any` — current substring match catches "prove" inside "improve"/"approve"
- Per-team timezone for monthly cap reset (currently UTC-1st-of-month)
- Soft-cap race: two concurrent requests can both pass quota check before either commits — document or tighten via DB-level constraint
- `_classify` swallows exceptions, returns 3 (mid) silently → log classifier failures for ops visibility
- Cost Ticker bonus: streaming price flash animation on tape pulse already there, add audio "bell" on tier price-tier-change for trader vibe

## Files changed
- `app/main.py` — `_acall_upstream`, pre-allocated request_id, `_resolve_team`, team quota gating (daily→monthly→global), tier gating after route, team CRUD endpoints, `?team=` filter on all admin views, request_id propagation through cache-hit/stream/shadow/escalation, budget-warn header scoping
- `app/providers/claude_cli.py` — `astream()` NDJSON event parser (`stream_event` envelopes, `content_block_delta.text_delta`, `result` event), `--verbose` flag added for stream-json mode
- `app/cache.py` — `hash_key()` folds team_id into SHA-256 namespace, `write_streamed()` for buffered SSE writes, `synthesize_stream_from_cache()` one-chunk SSE replay
- `app/telemetry.py` — `team_id` column ALTER, `stats(team_id)` filter, `metrics_snapshot` team_id label (NULL → "anon"), idempotent migration list
- `app/teams.py` (new) — Team dataclass, SQLite CRUD, today/month spend w/ 5s TTL, `python -m app.teams` CLI
- `app/templates/explorer.html` — TIER column, SHADOW badge, A/B SHADOW COMPARE panel, tier dots/colors on tape+depth, slide-out ticker drill-down panel (`#td-panel`)
- `eval/fixtures.json` — 10 → 51 fixtures
- `eval/run_eval.py` — refactored to `run()`/`gate()`/`main()`, rule-vs-classifier accuracy split, `--out`/`--gate` flags
- `eval/gate.json` (new) — regression thresholds
- `tests/test_teams.py` (new) — 27 tests passing
- `tests/test_eval_gate.py` (new) — 3 tests passing

## Open questions / blockers
- Test suite has pre-existing isolation bug: `tests/test_router.py` fails order-dependently after `tests/test_api.py` because `build_availability()` mutates module-global `_AVAILABLE` without restore — not introduced by this work, needs cleanup
- Live eval (`--live`) not run — no Anthropic credits in account, sub-mode skips litellm so meaningful drift % can't be measured this way for non-Anthropic providers
- Cache-hit attribution writes a telemetry row with team's `team_id` (so spend reads scope correctly) even though native_cost=0 — intentional, but explorer doesn't visually distinguish team-attributed cache hits

---

# 2026-05-09 21:01

## Completed
- Audited MVP — confirmed router, telemetry, pricing, explorer, eval harness all working per Idea.md
- Wave 1 (3 agents parallel): rule-order fix, daily budget enforcement (429 on breach, 5s cache), empty-response escalation w/ max_retries, header tier validation, ollama=$0 pricing fix, health-aware model pick (filter tiers by available API keys), admin bearer auth (`CLEARVIEW_ADMIN_TOKEN`), `/metrics` Prometheus endpoint, `stream_options.include_usage` fix, dead `_hash_prompt` removed
- Frontend rewrite: Chart.js sparklines (spend/calls/drift), per-row click → `<dialog>` modal, fetch-polling replaces meta-refresh, pause toggle, new-session button, curl empty-state. Added `/admin/timeseries`, `/admin/calls_detail`
- Pytest suite: tests/ w/ conftest, test_router (23 cases), test_pricing, test_telemetry, test_api (TestClient + monkeypatched litellm). Zero xfails
- Wave 2 (single agent): A/B shadow routing via `x-clearview-shadow` header → `asyncio.create_task`, `shadow_of` FK column, `consensus_flag=1`, new `/admin/shadow_compare`. Exact-match prompt cache (`app/cache.py`) — sha256 of (messages,model,temp), TTL 3600s, `picked_model="cache"` rows w/ native=0 + plan_equiv preserved as savings, `cache_hits`+`cache_savings_usd` in stats
- Live test: started uvicorn w/ `.env`, switched policy `ollama/qwen2.5` → `ollama/llama3.2` (model present locally), 6 demo calls routed cheap, drift_pct=100%, latency 600-850ms

## Next steps
- Add `picked_tier` column on `CallRecord` — fix Prometheus tier="unknown" for non-virtual-model rows
- Widen `/admin/calls_detail` + `/admin/stats` SELECTs to expose `shadow_of`/`consensus_flag` so explorer can badge shadow rows
- Streaming: allocate request_id up-front in `_stream_and_log` so shadow pairs link; add empty-response escalation post-first-chunk
- Buffered cache for streaming responses (currently skipped)
- Embedding cache (semantic match) for ~30-50% extra savings on agent loops — needs new dep, design call
- Per-team API keys + quotas — `Authorization: Bearer cv_team_xxx` → quota lookup, attribute spend
- Eval expansion: more fixtures, classifier accuracy tracked over time, `--live` cost regression gate
- Real provider keys in `.env` to demo meaningful drift % vs free-ollama baseline
- Fix escalation `on_error` in main.py to use `_AVAILABLE` not raw `policy.tiers` (currently retries unavailable frontier model)

## Files changed
- `policy.yaml` — rule reorder (complex_keywords before medium_prompt); ollama model qwen2.5 → llama3.2
- `app/router.py` — `build_availability`, `_provider_available`, escalating `_pick_model`, header tier validation
- `app/main.py` — budget gate, empty-response escalation, admin auth, `/metrics`, `stream_options`, cache lookup/write, shadow dispatch via `asyncio.create_task`, new `/admin/timeseries`, `/admin/calls_detail`, `/admin/shadow_compare`
- `app/pricing.py` — ollama short-circuit to $0
- `app/telemetry.py` — `today_spend()` w/ TTL cache, `metrics_snapshot()`, `shadow_of` column ALTER, `cache_hits`+`cache_savings_usd` in stats
- `app/cache.py` — new exact-match prompt cache module (sqlite, TTL)
- `app/templates/explorer.html` — full rewrite: Chart.js, polling, modal, pause, new-session
- `app/templates/_empty.html` — new empty-state partial w/ curl snippet
- `tests/conftest.py`, `tests/test_router.py`, `tests/test_pricing.py`, `tests/test_telemetry.py`, `tests/test_api.py` — new pytest suite
- `.claude/skills/journal/SKILL.md` — this skill
- `Docs/Journal/JOURNAL.md` — this file

## Open questions / blockers
- No real provider API keys in `.env` (all empty). Cost-saving demo trivially shows 100% (free ollama). Need user to populate at least `ANTHROPIC_API_KEY` for credible mixed-tier demo
- Escalation-on-error path in `app/main.py` references `pol.tiers["frontier"][0]` which can be a model w/ no available key — observed during live test. Documented as next-step, not fixed
- Shadow pairing for streaming requests known broken (request_id created in finally) — agent flagged, deferred
- pytest suite untested locally — `.venv` likely needs `pip install -e ".[dev]"` to run
