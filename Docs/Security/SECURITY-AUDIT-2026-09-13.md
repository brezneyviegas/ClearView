# Security Audit — 2026-09-13

**Auditor:** Automated daily audit (Claude Code)
**Branch audited:** `main` (HEAD `e8ad2e3`)
**Audit branch:** `security/audit-2026-09-13`
**Previous report:** None (first audit)

---

## Severity Summary

| Severity | New findings | Fixed this run | Deferred |
|----------|-------------|----------------|----------|
| High     | 0           | 0              | 0        |
| Medium   | 3           | 2              | 1        |
| Low      | 2           | 0              | 2        |
| **Total**| **5**       | **2**          | **3**    |

---

## Findings

### Medium — FIXED

#### M-1 · Non-constant-time admin token comparison

- **File:** `app/main.py:302` (`_admin_auth`)
- **Risk:** `token != expected` is a string equality check subject to timing side-channels. An attacker who can send many requests and measure response latency could enumerate a valid `CLEARVIEW_ADMIN_TOKEN` character by character. Admin endpoints control teams, policy tuning, and telemetry data.
- **Remediation:** Replaced with `secrets.compare_digest(token, expected)`. Added `import secrets` at the top of the module (line 12). Fix committed in this PR.

---

#### M-2 · Dockerfile runs as root

- **File:** `Dockerfile`
- **Risk:** No `USER` directive means the `python -m app` process runs as UID 0 inside the container. Container breakout or path-traversal vulnerabilities give an attacker full root access to the container filesystem, including the SQLite database at `/data/clearview.db`.
- **Remediation:** Added `RUN useradd -r -u 1001 -s /sbin/nologin clearview && mkdir -p /data && chown clearview:clearview /data` and `USER clearview` before the `CMD`. Fix committed in this PR.

---

### Medium — DEFERRED

#### M-3 · `/metrics` endpoint is unauthenticated

- **File:** `app/main.py:2538` (`metrics()`)
- **Risk:** The Prometheus `/metrics` endpoint is open with no authentication. It exposes `team_id` label values in `clearview_requests_total` counters, plus aggregate cost totals, token counts, and tier usage. On a network-accessible deployment (docker-compose binds 0.0.0.0:8000) any host on the network can enumerate team IDs and infer usage patterns.
- **Proposed design:** Add an opt-in `CLEARVIEW_METRICS_TOKEN` env var. When set, `metrics()` calls a new `_metrics_auth(request)` guard (mirrors `_admin_auth` with `secrets.compare_digest`). When unset the endpoint remains open for dev/local deployments. This avoids breaking existing Prometheus scrape configs that don't supply credentials.
- **Deferred reason:** Behavior-changing (adds auth to a currently-open endpoint). Requires coordinated Prometheus scrape config update.

---

### Low — DEFERRED

#### L-1 · docker-compose publishes port 8000 to all interfaces

- **File:** `docker-compose.yml` (service `clearview`, `ports: "8000:8000"`)
- **Risk:** `0.0.0.0:8000:8000` makes the gateway reachable from any network interface on the host, not just loopback. On a multi-tenant or cloud host this exposes `/v1/chat/completions`, the admin panel, and `/metrics` to other tenants or the public internet unless a firewall is in place.
- **Proposed design:** Change to `127.0.0.1:8000:8000` so the default is loopback-only. Operators who need network access can override in a `docker-compose.override.yml`. The `/health` endpoint is sufficient for container-internal health checks.
- **Deferred reason:** Likely intentional for a local gateway use-case. A change here affects documented quick-start instructions.

#### L-2 · `/v1/models` is open without authentication

- **File:** `app/main.py:364` (`list_models()`)
- **Risk:** Returns the full list of configured tiers and underlying model IDs without any credentials. Exposes the operator's model configuration (e.g. which Anthropic/OpenAI models are loaded) to unauthenticated callers.
- **Note:** This is required for OpenAI-compatible clients (they call `/v1/models` to populate model dropdowns). The exposure is limited to model names, not keys or usage data.
- **Proposed design:** Gate behind `_client_key_allowed` when `CLEARVIEW_CLIENT_KEYS` is set, so at least clients must supply a known key to enumerate models.
- **Deferred reason:** Behavior-changing and potentially breaks IDE plugins that call `/v1/models` before auth is configured.

---

## Minimum Sweep Results

| Check | Result |
|-------|--------|
| All `@app.get/post/delete` admin routes call `_admin_auth` | ✅ Pass — all `/admin/*` routes verified |
| All `/chat/*` routes call `_require_chat_team` (except `/chat`, `/chat/login`, `/chat/logout` which are intentionally open) | ✅ Pass |
| `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, unsafe `yaml.load` | ✅ None found |
| SQL parameterization — f-string/concat queries | ✅ `teams.py:218` uses an f-string to build a `SET` clause but only from hardcoded column names; all values use `?` placeholders. No injection risk. |
| Tracked credential files (`.env`, `.key`, `.pem`) | ✅ None tracked |
| Live-looking keys in tracked files (`sk-`, `AKIA`, `ghp_`) | ✅ None found |
| `secrets.compare_digest` for token comparisons | ✅ Fixed in M-1 (was non-constant-time, now uses `secrets.compare_digest`) |
| Token generation uses `secrets` module | ✅ `teams.py:117` uses `secrets.token_hex(16)`, `chat.py:62` uses `secrets.token_hex(8)` |
| Dockerfile non-root `USER` | ✅ Fixed in M-2 (added `USER clearview`) |
| docker-compose doesn't publish unauthenticated beyond loopback | ⚠️ Deferred — L-1 |
| `pip-audit` CVEs in application dependencies | ✅ No known vulnerabilities in application packages. `pip 24.0` and `setuptools 79.0.1` (dev tools in the container) have CVEs (PYSEC-2026-1795/1796/196/2875/2876/3721, PYSEC-2026-3447); fix by upgrading pip ≥ 26.2.1 and setuptools ≥ 83.0.0 in the Dockerfile. These are container tooling CVEs, not runtime application CVEs. |

---

## Positive Observations

- All 14 admin endpoints are consistently gated behind `_admin_auth`, with no exceptions.
- All chat endpoints enforce `_require_chat_team`; team token lookup uses constant-time-safe DB lookup, not string comparison.
- Team tokens are generated with `secrets.token_hex(16)` (128-bit entropy).
- Chat session tokens generated with `secrets.token_hex(8)` (64-bit, adequate for non-privileged session IDs).
- Login cookie is `HttpOnly`, `SameSite=Lax`, 30-day TTL — correct defaults.
- No shell injection surface: no `subprocess` with user-controlled arguments, no `os.system`, no `eval`.
- All SQL queries use parameterized `?` placeholders for user-supplied values; the one dynamic `SET` clause uses hardcoded column names only.
- No API keys or secrets found in tracked files.

---

## Deferred Items Carried Forward

| ID | Description | First seen |
|----|-------------|-----------|
| M-3 | `/metrics` unauthenticated — exposes team_ids and cost totals | 2026-09-13 |
| L-1 | docker-compose binds 0.0.0.0:8000 | 2026-09-13 |
| L-2 | `/v1/models` open without auth | 2026-09-13 |

---

## Remediation Status

| Finding | Status |
|---------|--------|
| M-1 · Non-constant-time token comparison | **Fixed** — `secrets.compare_digest` in `app/main.py` |
| M-2 · Dockerfile runs as root | **Fixed** — `USER clearview` in `Dockerfile` |
| M-3 · `/metrics` unauthenticated | **Deferred** — see proposed design above |
| L-1 · docker-compose all-interfaces bind | **Deferred** |
| L-2 · `/v1/models` open | **Deferred** |
