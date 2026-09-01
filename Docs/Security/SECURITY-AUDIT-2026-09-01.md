# Security Audit — 2026-09-01

Scope: full codebase (`app/`, `app/providers/`, `eval/`, Dockerfile, docker-compose.yml, env handling).
Method: auth-path review of all routes, injection grep, secrets hygiene, container config, dependency vulnerability scan (`pip-audit`).
Baseline: previous report dated 2026-06-10 (branch `security/audit-2026-06-10`).

## Changes since last audit

`git log --since=2026-06-10` — **zero new commits on main.** The last commit is `e8ad2e3` (2026-06-09). All findings carry from the 2026-06-10 report. Importantly, the security PR opened against that report (`security/audit-2026-06-10`) was **never merged into main**; all remediations it contained are therefore still absent from the live codebase and are re-confirmed below.

---

## Summary

| Severity | Count (new) | Carried from 2026-06-10 PR (unmerged) |
|----------|-------------|---------------------------------------|
| High     | 0           | 2 (H1, H2) re-confirmed open          |
| Medium   | 0           | 4 (M1–M3, M6) re-confirmed open; M4/M5 still deferred |
| Low      | 1 (L3-dep)  | 3 (L1, L2 re-confirmed; L4 deferred)  |

No new injection, secret leakage, or SQL construction findings.

---

## High — Re-confirmed open (fixes exist on unmerged branch)

### H1 — `/metrics` endpoint is unauthenticated
**Location:** `app/main.py` — `metrics()` function, `@app.get("/metrics")`
**Risk:** Exposes per-team request counts, team IDs, native/baseline costs, and token totals to anyone who can reach port 8000. The Docker image binds `0.0.0.0`.
**Status:** Fix authored on `security/audit-2026-06-10` but not merged. **Re-applying on this branch.**
**Remediation:** Add `_admin_auth(request)` call to `metrics()`.

### H2 — Unauthenticated `/feedback` with unsafe rating validation
**Location:** `app/main.py` — `post_feedback()`, line ~1720 (pre-fix)
**Risk (a):** No auth gate — anyone can call `/feedback` regardless of `CLEARVIEW_CLIENT_KEYS`.
**Risk (b):** Validation logic `rating not in (1,-1,...) and not isinstance(...)` uses AND, not OR. Any integer/float passes (e.g. `rating=1000`). Feedback drives `record_provider_outcome` → routing poisoning.
**Risk (c):** Any `request_id` string accepted; caller need not reference a real call.
**Status:** Fix authored on `security/audit-2026-06-10` but not merged. **Re-applying on this branch.**
**Remediation:** `_client_key_allowed(request)` gate; `rating not in (1,-1,1.0,-1.0)` strict check; require `request_id` to resolve a real telemetry row.

---

## Medium — Re-confirmed open (fixes exist on unmerged branch)

### M1 — Admin token compared with `!=` (timing side-channel)
**Location:** `app/main.py` — `_admin_auth()`, `if token != expected`
**Risk:** Non-constant-time comparison leaks whether an attacker's guess matches token length/prefix under timing.
**Status:** Fix authored on unmerged branch. **Re-applying: `secrets.compare_digest`.**

### M2 — Open-by-default posture with no startup warning
**Location:** `app/main.py` — `lifespan()` startup handler; Dockerfile sets `CLEARVIEW_HOST=0.0.0.0`
**Risk:** `docker compose up` with default env exposes admin endpoints and provider keys to the LAN without any visible warning.
**Status:** Fix authored on unmerged branch. **Re-applying: startup warning when bound host is non-loopback and no auth env vars are set.**

### M3 — Container runs as root
**Location:** `Dockerfile` — no `USER` directive
**Risk:** A container breakout or path-traversal exploit runs as uid 0.
**Status:** Fix authored on unmerged branch. **Re-applying: `useradd clearview; USER clearview`.**

### M4 — Team tokens stored in plaintext as primary keys (deferred)
**Location:** `app/teams.py`
**Risk:** Any read of the SQLite file (backup, volume snapshot) yields live credentials.
**Design needed:** Store SHA-256 hash of token; look up by hash; show plaintext only at creation time. Requires migration.
**Status:** Carried over — no change.

### M5 — No rate limiting on `/chat/login` (deferred)
**Location:** `app/main.py` — `chat_login()`
**Risk:** Unthrottled login attempts (128-bit tokens make brute-force impractical but guessing is unlogged).
**Status:** Carried over — no change. Proposed fix: per-IP failed-attempt counter with exponential back-off or fail2ban integration.

### M6 — Session cookie lacks `Secure` flag
**Location:** `app/main.py` — `chat_login()`, `resp.set_cookie(...)`
**Risk:** `cv_session` (the raw team token) transits cleartext HTTP when deployed behind TLS-terminating proxies.
**Status:** Fix authored on unmerged branch. **Re-applying: opt-in `CLEARVIEW_COOKIE_SECURE=1` env var.**

---

## Low

### L1 — Unbounded request body
**Location:** `app/main.py` — all `await request.json()` call sites; no body-size middleware
**Risk:** Memory-exhaustion via a giant POST body.
**Status:** Fix authored on unmerged branch. **Re-applying: `_limit_body_size` ASGI middleware, default 10 MB cap, env-tunable.**

### L2 — Compose publishes ollama to the host network
**Location:** `docker-compose.yml` — `ports: "11434:11434"`
**Risk:** Unauthenticated ollama API reachable from the LAN.
**Status:** Fix authored on unmerged branch. **Re-applying: `127.0.0.1:11434:11434`.**

### L3 — pip / setuptools CVEs (build-tool surface)
**Location:** Build environment (pip ≤ 26.1, setuptools ≤ 82.x)
**Risk:** `pip-audit` reports 6 pip CVEs (path traversal on malicious archives, entry-point path injection, tar/zip confusion) and 1 setuptools CVE (NFD/NFC bypass for MANIFEST.in exclusions). These affect the build pipeline, not the running application; exploitation requires fetching from a malicious index during `pip install`. Fix versions: pip ≥ 26.2.1, setuptools ≥ 83.0.0.
**Remediation:** Pin build tools in CI (`pip install --upgrade pip setuptools`); add `pip-audit` as a CI step. No code change needed in the app itself.

### L4 — Refusal-marker escalation is spoofable (deferred)
**Location:** `app/main.py` — `_looks_refusal_or_too_short()`
**Risk:** A prompt containing "I can't" forces tier escalation (cost amplification). Budget caps bound the damage.
**Status:** Carried over — no change.

---

## Positive observations

- All SQL uses parameter binding; f-string queries in `telemetry` and `teams` interpolate only whitelisted column fragments, never user input.
- CLI providers (`claude_cli`, `codex_cli`, `gemini_cli`) use `asyncio.create_subprocess_exec` (exec form); no shell injection surface.
- `.env` is gitignored and untracked. `.env.example` and `clearview-client.env.example` contain no real secrets. Test files use monkeypatch mock keys (`sk-x`, `sk-test`) — not real API keys.
- `secrets.token_hex` used for team token generation (CSPRNG); `random` used only for shadow sampling (non-security path).
- Prompt cache keys include `team_id` → no cross-tenant cache poisoning.
- Jinja2Templates autoescaping on by default for all HTML surfaces.
- No `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, or unsafe `yaml.load` in any tracked file.

---

## Carried-over deferred items

| ID | Description | Status |
|----|-------------|--------|
| M4 | Team tokens stored as plaintext PKs | Deferred — needs migration design |
| M5 | No rate limiting on `/chat/login` | Deferred — needs per-IP throttle design |
| L3 | pip-audit in CI | Partially addressed (daily audit runs pip-audit); pip/setuptools CVEs noted above |
| L4 | Refusal-marker escalation spoofable | Deferred — minor, cost-bounded |

---

## Remediation status — this PR

The following fixes are applied in `security/audit-2026-09-01` (identical to the 2026-06-10 unmerged PR):

| Finding | Fix | Status |
|---------|-----|--------|
| H1 `/metrics` unauthenticated | `_admin_auth(request)` added | **Fixed** |
| H2 `/feedback` unsafe | `_client_key_allowed`, strict rating, request_id validation | **Fixed** |
| M1 Timing comparison | `secrets.compare_digest` | **Fixed** |
| M2 No startup warning | Warn on non-loopback host without auth env vars | **Fixed** |
| M3 Root container | `useradd clearview; USER clearview` in Dockerfile | **Fixed** |
| M6 Cookie no Secure flag | `CLEARVIEW_COOKIE_SECURE` env opt-in | **Fixed** |
| L1 Unbounded body | 10 MB cap ASGI middleware | **Fixed** |
| L2 Ollama LAN exposure | `127.0.0.1:11434:11434` in compose | **Fixed** |
| M4 Token storage | Design required — deferred | **Deferred** |
| M5 Rate limiting | Design required — deferred | **Deferred** |
| L3 pip/setuptools CVEs | CI step needed — deferred | **Deferred** |
| L4 Refusal escalation | Minor/cost-bounded — deferred | **Deferred** |

All 359 tests pass with these changes applied.
