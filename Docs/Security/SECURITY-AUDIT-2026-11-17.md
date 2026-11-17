# ClearView Security Audit — 2026-11-17

**Auditor:** Automated daily audit (Claude Code)
**Branch audited:** `main` (HEAD `e8ad2e3`)
**Scope:** Full codebase sweep (first audit — no prior reports to carry forward)
**Remediations branch:** `security/audit-2026-11-17`

---

## Severity Summary

| Severity | New findings | Fixed this run | Deferred |
|----------|-------------|----------------|---------|
| High     | 1           | 1              | 0       |
| Medium   | 2           | 0              | 2       |
| Low      | 1           | 1              | 0       |
| Info     | 1           | —              | —       |

---

## High Findings

### H-1 — Timing side-channel in admin token comparison ✅ FIXED

**File/location:** `app/main.py:301` (`_admin_auth`)
**Risk:** Python's `!=` operator short-circuits string comparison on the first differing byte. An attacker who can make rapid authenticated requests to any `_admin_auth`-gated endpoint (e.g. `/admin/stats`) and measure response time could byte-by-byte recover `CLEARVIEW_ADMIN_TOKEN` without valid credentials. At typical LAN or loopback latencies this is impractical but becomes realistic over a low-jitter network path.
**Remediation applied:** Replaced `token != expected` with `secrets.compare_digest(token, expected)` (constant-time comparison). Added `import secrets` to the module imports (the module already imported `secrets` via `app.teams`; the explicit import makes the dependency explicit and future-proof).

---

## Medium Findings

### M-1 — `/metrics` endpoint unauthenticated — DEFERRED

**File/location:** `app/main.py:2538` (`metrics`)
**Risk:** The Prometheus metrics endpoint is publicly reachable with no authentication. It exposes: request counts broken down by `tier`, `provider`, `status`, and `team_id` label; cumulative cost counters (native USD and plan-equivalent); and total output token counts. In a production deployment these reveal business-sensitive operational data (which AI providers are in use, rough cost volumes, team identifiers) to any unauthenticated client.
**Remediation deferred — proposed design:** Add a `CLEARVIEW_METRICS_TOKEN` env var (optional). When set, `metrics()` calls `_admin_auth(request)` or checks a dedicated bearer. When unset, the endpoint remains open (preserves the current zero-config scraper behaviour). This is a one-line change in the route body but touches the documented dev-mode contract, so it belongs in a deliberate feature commit rather than the daily audit patch.

### M-2 — Session cookie missing `Secure` flag — DEFERRED

**File/location:** `app/main.py:2651` (`chat_login`)
**Risk:** The `cv_session` cookie is set with `httponly=True` and `samesite="lax"` but without `secure=True`. On deployments served over plain HTTP (the default without a TLS proxy), `secure=True` would be silently ignored anyway; however, adding it without detecting the transport protocol would break the cookie on legitimate HTTP deployments. The fix requires either detecting the transport (via `X-Forwarded-Proto` or a `CLEARVIEW_HTTPS=1` env var) or leaving the responsibility to the operator's reverse proxy. This is a behaviour-changing fix deferred to a planned TLS-aware cookie hardening pass.

---

## Low Findings

### L-1 — Dockerfile runs as root ✅ FIXED

**File/location:** `Dockerfile`
**Risk:** No `USER` directive was present, so the application process ran as UID 0 inside the container. If an attacker achieved RCE through the application, they would have unrestricted root access within the container, simplifying container-escape attacks and lateral movement.
**Remediation applied:** Added `useradd -r -u 1001 -s /bin/false clearview`, created `/data` with correct ownership, and added `USER clearview` before the `VOLUME`/`EXPOSE`/`CMD` directives.

---

## Informational

### I-1 — `CLEARVIEW_ADMIN_TOKEN` defaults to open in dev mode

**File/location:** `app/main.py:293` (`_admin_auth`)
**Risk/note:** When `CLEARVIEW_ADMIN_TOKEN` is not set, `_admin_auth` returns immediately, leaving all `/admin/*` endpoints open. This is intentional and documented ("dev mode: open"), but operators who deploy to a shared network without setting the token inadvertently expose the admin surface. No code change is recommended (the current behaviour is correct for local dev); a one-time operator checklist reminder in the README is sufficient.

---

## Minimum Sweep Results

| Check | Result |
|-------|--------|
| All `@app.*` routes call `_admin_auth`, `_client_key_allowed`, `_resolve_team`, or `_require_chat_team`, or have documented reason | PASS — `/v1/models`, `/health`, `/metrics`, `/feedback`, `/chat`, `/chat/login`, `/chat/logout` are intentionally open; all `/admin/*` and `/chat/*` data endpoints are gated. |
| `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, unsafe `yaml.load` | PASS — none found. `create_subprocess_exec` (not `shell`) is used in CLI provider adapters. |
| SQL parameterization — no f-string/concat queries interpolating non-whitelisted values | PASS — all f-string WHERE clauses are assembled from hardcoded column-name strings; every user-supplied value is bound via `?` placeholder. |
| No tracked secrets (`.env`, key, PEM files; live-looking key patterns `sk-`, `AKIA`, `ghp_`) | PASS — only `.env.example` tracked (no live values). No live key strings found. |
| Secret comparisons use `secrets.compare_digest` | PASS (after H-1 fix) |
| Token generation uses `secrets` module | PASS — `secrets.token_hex(16)` in `app/teams.py:117` |
| Dockerfile has non-root `USER` | PASS (after L-1 fix) |
| `docker-compose.yml` does not publish unauthenticated services beyond loopback | NOTE — port 8000 is published on `0.0.0.0`. Acceptable for a single-user local gateway; in a shared/cloud deployment the operator should set `CLEARVIEW_ADMIN_TOKEN` and `CLEARVIEW_CLIENT_KEYS`. |
| `pip-audit` CVE scan | PASS — no known vulnerabilities found |

---

## Positive Observations

- Team token generation is cryptographically strong (`secrets.token_hex(16)`, 128-bit entropy).
- No shell injection vectors: all subprocess invocations use `create_subprocess_exec` / `subprocess.run` with argument lists, not shell strings.
- SQL injection risk is minimal: the codebase uses SQLite with `?` placeholders throughout; f-string WHERE assembly uses only hardcoded column-name strings.
- `cv_session` cookie is marked `HttpOnly` (XSS-resistant) and `SameSite=Lax` (CSRF-resistant for most scenarios).
- Team tokens and admin tokens are never logged in full; listing endpoints redact tokens to a 18-char prefix.
- No CVEs found in any dependency by `pip-audit`.
- Telemetry and cache endpoints validate that `team_id` and `session_id` are always passed as parameterized SQL values.

---

## Deferred Items (carry-forward)

| ID | Description | Proposed design | Priority |
|----|-------------|-----------------|----------|
| M-1 | `/metrics` unauthenticated | Add `CLEARVIEW_METRICS_TOKEN` opt-in env var; when set, gate the endpoint with `_admin_auth` | Medium |
| M-2 | Session cookie missing `Secure` flag | Add `CLEARVIEW_HTTPS=1` env var; when set, pass `secure=True` to `set_cookie` | Medium |
