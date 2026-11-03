# ClearView Security Audit — 2026-11-03

**Auditor:** Claude Code (automated daily audit)
**Branch:** security/audit-2026-11-03
**Scope:** Full codebase sweep (first audit — no prior report to diff against)
**Audit date:** 2026-11-03

---

## Severity Summary

| Severity | New | Fixed This Run | Deferred |
|----------|-----|----------------|----------|
| High     | 1   | 1              | 0        |
| Medium   | 2   | 1              | 1        |
| Low      | 2   | 0              | 2        |
| Info     | 1   | 0              | 1        |

---

## Findings

### HIGH — Non-constant-time admin token comparison

**File:** `app/main.py:301`
**Risk:** Python's `!=` operator short-circuits on the first differing byte, enabling timing-based side-channel attacks to recover `CLEARVIEW_ADMIN_TOKEN` character-by-character. An attacker with local network access and ability to make repeated requests could eventually brute-force the admin token without the rate-limiting overhead of a full hash compare.
**Remediation:** Replace `token != expected` with `secrets.compare_digest(token, expected)`.
**Status: FIXED** — `secrets` imported and `compare_digest` used at `app/main.py:301`.

---

### MEDIUM — Dockerfile runs as root

**File:** `Dockerfile`
**Risk:** No `USER` directive means the container process runs as UID 0. A container escape or RCE vulnerability in the app or a dependency grants full root access to the host filesystem if the container is privileged or shares volumes.
**Remediation:** Create a non-privileged system user and switch to it before the `CMD`.
**Status: FIXED** — Added `addgroup/adduser clearview` and `USER clearview` before `CMD` in `Dockerfile`.

---

### MEDIUM — Unauthenticated `/metrics` endpoint exposes operational data (DEFERRED)

**File:** `app/main.py:2538`
**Risk:** The `/metrics` Prometheus endpoint returns detailed per-tier, per-provider call counts, cumulative USD costs, and output-token totals without any authentication. An unauthenticated observer on the same network can enumerate which providers are in use, estimate traffic volumes, and calculate spend.
**Proposed fix:** Gate `/metrics` behind `_admin_auth` or an independent `CLEARVIEW_METRICS_TOKEN` env var, consistent with the existing admin auth pattern. This is a config-layer change and will not break the test suite, but it changes the default-open behaviour; deferring for operator review before applying.
**Status: DEFERRED** — Needs operator decision on scrape auth strategy.

---

### LOW — `/v1/models` leaks provider configuration without auth

**File:** `app/main.py:364`
**Risk:** The `GET /v1/models` endpoint returns all configured virtual and underlying model IDs (including provider prefixes such as `openai/gpt-4o`, `anthropic/claude-sonnet-4-6`, `ollama/llama3.2`) to any unauthenticated caller. This reveals the gateway's full provider configuration.
**Proposed fix:** Call `_client_key_allowed(request)` (already exists) at the top of `list_models`. The cost is minimal; the benefit is consistent auth across all v1 endpoints when `CLEARVIEW_CLIENT_KEYS` is set.
**Status: DEFERRED** — Intentionally open in dev mode; worth gating when `CLEARVIEW_CLIENT_KEYS` is set. Low priority given local-first deployment model.

---

### LOW — Session cookie missing `Secure` flag

**File:** `app/main.py:2651` (`chat_login`)
**Risk:** The `cv_session` cookie carrying the team Bearer token is set with `httponly=True, samesite="lax"` but no `secure=True`. If the gateway is deployed behind an HTTP-only reverse proxy, the cookie is transmitted in plaintext, exposing the team token to network eavesdropping.
**Proposed fix:** Add `secure=True` conditionally when `CLEARVIEW_COOKIE_SECURE=1` is set, or unconditionally when not running on loopback.
**Status: DEFERRED** — Acceptable for local dev; operators deploying over HTTPS should set this. Add to deployment checklist.

---

### INFO — `docker-compose.yml` publishes port 8000 on all interfaces

**File:** `docker-compose.yml`
**Risk:** `ports: - "8000:8000"` binds the gateway to `0.0.0.0:8000` on the Docker host, making it reachable from the local network (and beyond, depending on firewall rules) even when no API keys or admin tokens are configured.
**Proposed fix:** Use `"127.0.0.1:8000:8000"` to restrict to loopback by default; expose more broadly only when explicitly overriding.
**Status: DEFERRED** — Design trade-off (LAN sharing is a stated use case). Document the exposure in README deployment section.

---

## Minimum Sweep Results

| Check | Result |
|-------|--------|
| All `@app.get/post/delete` routes call an auth helper or are documented-open | **Pass** — `/health`, `/v1/models`, `/feedback`, `/metrics`, Gemini shims are intentionally open; all `/admin/*` and `/chat/*` routes call `_admin_auth` or `_require_chat_team` respectively. See MEDIUM finding for `/metrics`. |
| `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, unsafe `yaml.load` | **Pass** — None found. CLI providers use `asyncio.create_subprocess_exec` (safe, no shell interpolation). |
| SQL parameterization | **Pass** — All user-supplied values use `?` placeholders; f-strings only interpolate code-controlled structural fragments (column names, clause lists). |
| Tracked secret files (`.env`, `.key`, `.pem`) | **Pass** — None tracked. `.gitignore` covers `.env`. |
| Live-looking keys in tracked files (`sk-`, `AKIA`, `ghp_`, `cv_team_*`) | **Pass** — None found in tracked files. |
| Secret comparisons use `secrets.compare_digest` | **Fixed** — Admin token comparison now uses `secrets.compare_digest`. |
| Token generation uses `secrets` module | **Pass** — `teams.py` uses `secrets.token_hex(16)`; `chat.py` uses `secrets.token_hex(8)`. |
| Dockerfile has non-root `USER` | **Fixed** — Added `USER clearview`. |
| `docker-compose` does not publish unauthenticated services beyond loopback | **See INFO finding** — Port 8000 published on all interfaces; deferred. |
| pip-audit (CVE scan) | **Pass** — No known vulnerabilities found in declared dependencies. |

---

## Deferred Items

| ID | Severity | File | Summary |
|----|----------|------|---------|
| D-001 | Medium | `app/main.py:2538` | `/metrics` unauthenticated — add scrape auth |
| D-002 | Low | `app/main.py:364` | `/v1/models` leaks provider config — gate when client keys set |
| D-003 | Low | `app/main.py:2651` | Session cookie missing `Secure` flag |
| D-004 | Info | `docker-compose.yml` | Port 8000 bound to all interfaces |

---

## Positive Observations

- Team token generation uses `secrets.token_hex` (cryptographically strong).
- Conversation IDs use `secrets.token_hex` (no predictable sequential IDs).
- SQL throughout uses parameterized queries; no string-concatenated user values found.
- `httponly=True, samesite="lax"` on the session cookie prevents XSS token theft and CSRF.
- No `shell=True` subprocess calls; CLI providers use `create_subprocess_exec` with arguments passed as a list.
- No tracked secrets, API keys, or `.env` files found in the repository.
- No dangerous deserializers (`pickle`, unsafe `yaml.load`) found.
- `pip-audit` reports zero CVEs against declared dependency versions.
- Escalation and shadow paths run as fire-and-forget tasks; they cannot influence the auth state of the primary request.

---

## Remediation Status (this run)

| Finding | Action |
|---------|--------|
| HIGH: non-constant-time admin token compare | Fixed — `secrets.compare_digest` |
| MEDIUM: Docker root user | Fixed — `USER clearview` added to Dockerfile |
| MEDIUM: unauthenticated `/metrics` | Deferred (D-001) |
| LOW: `/v1/models` leaks config | Deferred (D-002) |
| LOW: cookie missing `Secure` | Deferred (D-003) |
| INFO: docker-compose all-interface bind | Deferred (D-004) |
