# Security Audit — 2026-11-27

**Branch audited:** `main` (HEAD `e8ad2e3`)  
**Auditor:** Automated daily audit (Claude Code)  
**Previous report:** None — first run.

---

## Severity Summary

| Severity | Count | Fixed this run | Deferred |
|----------|-------|---------------|----------|
| High     | 1     | 1             | 0        |
| Medium   | 3     | 1             | 2        |
| Low      | 2     | 0             | 2        |

---

## High Findings

### H-1 · Non-constant-time admin token comparison — **FIXED**

**File/location:** `app/main.py:301` (`_admin_auth`)  
**Risk:** The admin bearer token was compared with `token != expected`, a standard Python string equality operator. This is vulnerable to timing side-channel attacks: an attacker making many requests can observe microsecond latency differences to brute-force the token one character at a time.  
**Remediation applied:** Replaced with `secrets.compare_digest(token, expected)`, which runs in constant time regardless of match length.  
**Commit:** included in this branch.

---

## Medium Findings

### M-1 · Dockerfile runs container as root — **FIXED**

**File/location:** `Dockerfile`  
**Risk:** The container process runs as UID 0. If the application is compromised (e.g. via a dependency vulnerability or SSRF that reaches the local socket), the attacker has full root privileges inside the container, reducing the blast-radius containment that a non-root user provides.  
**Remediation applied:** Added `RUN useradd -r -u 1000 -m clearview` and `USER clearview` before `CMD`.  
**Commit:** included in this branch.

### M-2 · `/metrics` Prometheus endpoint unauthenticated — **DEFERRED**

**File/location:** `app/main.py` — `GET /metrics` route (line ~2538)  
**Risk:** The Prometheus scrape endpoint exposes per-tier request counts, total native/plan-equivalent costs, output tokens, drift percentage, and per-model/provider/status breakdowns with no authentication gate. An unauthenticated caller on the network can enumerate model usage patterns, infer deployed provider configuration, and observe cost trends. In a shared-network deployment this is an information disclosure risk.  
**Proposed design (deferred):** Gate `/metrics` behind `_admin_auth` (consistent with other admin surfaces), or introduce a separate `CLEARVIEW_METRICS_TOKEN` env var so a Prometheus scraper can authenticate without needing the full admin token. Either change requires operator monitoring config updates — not a one-line fix.

### M-3 · Session cookie missing `Secure` flag — **DEFERRED**

**File/location:** `app/main.py:2651` (`chat_login`)  
**Risk:** The `cv_session` cookie is set with `HttpOnly=True, SameSite=Lax` but without `Secure=True`. When ClearView is served over HTTP (the default for `localhost` dev), this is harmless. If ever deployed behind a TLS-terminating proxy where the upstream is HTTP (common), or directly over HTTPS, the cookie would be sent over both HTTPS and HTTP, making it stealable on a network that can inject a redirect to an HTTP resource.  
**Proposed design (deferred):** Add `secure=True` to `set_cookie` and document that ClearView should always be served behind TLS in production. A `CLEARVIEW_SECURE_COOKIE=1` env var would let operators opt in without breaking local dev workflows.

---

## Low Findings

### L-1 · `/v1/models` reveals tier/model configuration without auth — **DEFERRED**

**File/location:** `app/main.py:364` (`GET /v1/models`)  
**Risk:** Returns all configured tier names and underlying model IDs to any unauthenticated caller. An attacker can discover which frontier models are deployed, tier structure, and provider mix without credentials. For a purely internal gateway this is acceptable; for internet-facing deployments it leaks configuration.  
**Proposed design (deferred):** Most OpenAI-compatible IDE clients (Continue, Cline, Cursor) call `/v1/models` during setup and expect it open. Recommend adding an optional `CLEARVIEW_OPEN_MODEL_LIST=1` flag (default open for compatibility) so operators can gate it when desired, without breaking the common case.

### L-2 · pip/setuptools CVEs in build toolchain — **DEFERRED**

**Tool:** `pip-audit` on development venv  
**Risk:** The development venv uses `pip 24.0` (PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-196, PYSEC-2026-3721; fix: `pip>=26.1.2`) and `setuptools 79.0.1` (PYSEC-2026-3447, GHSA-xmj3-9gf7-h52w; fix: `setuptools>=84.1.0`). No production runtime dependency CVEs found. These vulnerabilities affect the pip toolchain used at install time, not the running application.  
**Proposed design (deferred):** Pin `pip>=26.1.2` and `setuptools>=84.1.0` in the Dockerfile (`RUN pip install --upgrade pip setuptools`) and in the dev setup instructions. This is low risk since production containers do not expose pip after build.

---

## Minimum-sweep Results

| Check | Result |
|-------|--------|
| All `@app.get/post/delete` routes → auth guard or documented open | Pass — see table below |
| `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, unsafe `yaml.load` | Pass — no occurrences in app/ |
| SQL parameterization (f-string interpolation of non-whitelisted values) | Pass — all f-string WHERE clauses use hardcoded column names; values via `?` placeholders |
| Tracked secrets (`.env`, `.pem`, key files in git ls-files) | Pass — only `.env.example` tracked (no live values) |
| Live-looking secrets in tracked files (`sk-`, `AKIA`, `ghp_`) | Pass — none found |
| Secret comparisons via `secrets.compare_digest` | Fixed (H-1) |
| Token generation via `secrets` module | Pass — `secrets.token_hex` used for all team/chat IDs |
| Dockerfile non-root USER | Fixed (M-1) |
| docker-compose publishes no unauthenticated loopback services | Pass — port 8000 is the intended gateway port; Ollama (profile=local) on 11434 is opt-in |
| pip-audit: production deps | Pass — no CVEs in fastapi, litellm, uvicorn, pydantic, pyyaml, jinja2, httpx |

### Route auth coverage

| Route | Guard | Note |
|-------|-------|------|
| `GET /v1/models` | None | Open by design for IDE compatibility (L-1) |
| `POST /v1/chat/completions` | `_client_key_allowed` + `_resolve_team` | ✓ |
| `POST /v1/messages` | via `_handle_chat_completions` | ✓ |
| `POST /v1/responses` | via `_handle_chat_completions` | ✓ |
| `POST /v1*/models/{m}:generateContent` (×4) | via `_handle_chat_completions` | ✓ |
| `POST /feedback` | None | Intentional — callers rate own responses |
| `GET /admin/*` (11 routes) | `_admin_auth` | ✓ |
| `POST /admin/teams`, `GET /admin/teams`, `PATCH/DELETE /admin/teams/{id}` | `_admin_auth` | ✓ |
| `GET /metrics` | None | Information disclosure risk — M-2 |
| `GET /health` | None | Standard health check — intentional |
| `GET /chat` | `_resolve_team` (open page) | ✓ — serves both states |
| `GET /chat/model_options` | `_require_chat_team` | ✓ |
| `POST /chat/login`, `POST /chat/logout` | None | Login/logout endpoints — intentional |
| `GET /chat/me`, `GET|POST|DELETE /chat/conversations/*` | `_require_chat_team` | ✓ |
| `POST /chat/conversations/{cid}/send[_stream]` | `_require_chat_team` | ✓ |

---

## Positive Observations

- Team token generation uses `secrets.token_hex(16)` — cryptographically strong.
- Chat conversation IDs use `secrets.token_hex(8)` — no guessable sequential IDs.
- The `cv_session` cookie is `HttpOnly` and `SameSite=Lax`, preventing XSS-based theft and most CSRF.
- All SQLite queries use parameterized placeholders; dynamic WHERE clauses are built from hardcoded column name fragments, not user input.
- CLI provider adapters use `asyncio.create_subprocess_exec` (not `shell=True`), preventing shell injection regardless of model name input.
- No production runtime dependency CVEs found by pip-audit.
- Telemetry strips `_clearview_*` internal fields before returning responses to clients.

---

## Deferred Items Carried Forward

| ID  | Summary | Proposed action |
|-----|---------|-----------------|
| M-2 | `/metrics` unauthenticated | Add `CLEARVIEW_METRICS_TOKEN` env var or gate behind `_admin_auth` |
| M-3 | Session cookie missing `Secure` flag | Add `CLEARVIEW_SECURE_COOKIE=1` opt-in env var |
| L-1 | `/v1/models` open | Add `CLEARVIEW_OPEN_MODEL_LIST=1` toggle (default open) |
| L-2 | pip/setuptools CVEs | Pin `pip>=26.1.2`, `setuptools>=84.1.0` in Dockerfile |
