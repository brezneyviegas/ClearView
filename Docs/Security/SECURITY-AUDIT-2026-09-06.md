# Security Audit — 2026-09-06

**Repository:** ClearView (Python/FastAPI LLM gateway)
**Auditor:** Automated daily audit
**Date:** 2026-09-06
**Branch audited:** main (`e8ad2e3`)
**Previous audit:** none (first run)

---

## Severity Summary

| Severity | Count | Fixed this run | Deferred |
|----------|-------|---------------|----------|
| High     | 1     | 1             | 0        |
| Medium   | 2     | 1             | 1        |
| Low      | 3     | 0             | 3        |
| Info     | 1     | —             | —        |

---

## High Findings

### H-1 — Timing-attack on admin token comparison [FIXED]

**File/Location:** `app/main.py:300` — `_admin_auth()`

**Risk:** The admin bearer token is compared with `token != expected`, a non-constant-time Python string equality. An attacker making many requests against a deployed instance can measure response-time differences to recover the admin token one character at a time (classic timing oracle).

**Remediation applied:** Replaced `if token != expected:` with `if not secrets.compare_digest(token, expected):` and added `import secrets` to the module imports.

---

## Medium Findings

### M-1 — Dockerfile runs as root (no non-root USER) [FIXED]

**File/Location:** `Dockerfile` (entire file)

**Risk:** The image is built from `python:3.12-slim` with no `USER` directive, so the process runs as UID 0 inside the container. If the application is compromised, an attacker gains root within the container, potentially escaping via kernel or volume misconfigurations.

**Remediation applied:** Added a dedicated `clearview` system user and `USER clearview` directive before the `CMD`.

---

### M-2 — `/metrics` endpoint has no authentication gate [DEFERRED]

**File/Location:** `app/main.py:2538` — `GET /metrics`

**Risk:** The Prometheus-format metrics endpoint exposes call counts, cumulative cost totals, and team-level routing data (tier, provider, status, team_id labels) without any authentication. On a network-accessible deployment this leaks operational and financial intelligence to any client.

**Proposed design:** Gate `/metrics` behind `_admin_auth` and document the token-free exemption pattern for Prometheus scrapers (e.g. a separate scrape-only token, or network-layer restriction via the reverse proxy). This is a non-trivial auth design change (Prometheus scraper needs credentials configured) and is not a mechanical fix.

**Deferred — no code change this run.**

---

## Low Findings

### L-1 — docker-compose binds on all host interfaces [DEFERRED]

**File/Location:** `docker-compose.yml:7` — `ports: "8000:8000"`

**Risk:** The default bind of `"8000:8000"` listens on `0.0.0.0`, making ClearView reachable from any network interface on the Docker host. If the host is on a shared or public network and `CLEARVIEW_ADMIN_TOKEN` / `CLEARVIEW_CLIENT_KEYS` are not configured, admin and client endpoints are unauthenticated.

**Proposed design:** Change to `"127.0.0.1:8000:8000"` for developer/local use; keep the current binding when deploying behind a load balancer with explicit documentation. This is an operator/deployment change outside the code scope of a mechanical fix.

**Deferred — no code change this run.**

---

### L-2 — pip CVEs in build environment [DEFERRED]

**Tool:** `pip-audit` on installed environment

**Packages / CVEs:**

| Package    | Version | CVE ID           | Fix Version | Summary |
|-----------|---------|-----------------|-------------|---------|
| pip       | 24.0    | PYSEC-2026-196   | 26.1.2      | console_scripts path traversal during install |
| pip       | 24.0    | PYSEC-2026-1795  | 25.3        | tar symlink extraction outside target dir |
| pip       | 24.0    | PYSEC-2026-1796  | 26.0        | wheel archive path traversal |
| pip       | 24.0    | PYSEC-2026-2875  | 26.1        | ambiguous tar+ZIP handling |
| pip       | 24.0    | PYSEC-2026-2876  | 26.1        | module import after wheel install race |
| pip       | 24.0    | PYSEC-2026-3721  | 26.2        | doubly-encoded URL arbitrary file install |
| setuptools| 79.0.1  | PYSEC-2026-3447  | 83.0.0      | Unicode normalization bypass in MANIFEST.in |

**Risk:** All are build-time / installer vulnerabilities, not runtime. The running ClearView application is not directly exposed by them. Risk materialises when installing packages from an untrusted index during CI or image builds.

**Proposed design:** Pin `pip>=26.2` and `setuptools>=83.0.0` in the Dockerfile `RUN pip install --upgrade pip setuptools` step before installing application deps. Mechanical but touches the build pipeline rather than application code — deferring for a dedicated dependency-update PR.

**Deferred — no code change this run.**

---

### L-3 — Admin endpoints open when CLEARVIEW_ADMIN_TOKEN unset [DEFERRED]

**File/Location:** `app/main.py:294-297` — `_admin_auth()`

**Risk:** When `CLEARVIEW_ADMIN_TOKEN` is not set, all `/admin/*` endpoints are fully unauthenticated ("dev mode: open"). If ClearView is deployed to a shared or production environment without setting this variable, any user on the network can read telemetry, modify policy, create/delete teams, and trigger a policy revert.

**Proposed design:** Emit a startup warning at WARN/ERROR level when the var is unset and the server is listening on a non-loopback address. The behavioral change (fail-closed by default) would break existing single-user local deployments, so it is a behaviour change requiring a design decision, not a mechanical fix.

**Deferred — no code change this run.**

---

## Carried-Over Deferred Items

None (first audit).

---

## Minimum-Sweep Attestation

| Check | Result |
|-------|--------|
| All `@app.get/post/delete` routes gated by `_admin_auth`, `_client_key_allowed`, `_resolve_team`, or `_require_chat_team`, or documented as intentionally open | **Pass** — `/v1/models`, `/v1/chat/completions` (and Anthropic/Gemini shims), `/feedback`, `/health`, `/chat` (page only), `/chat/login`, `/chat/logout` are intentionally open; `/metrics` flagged as M-2 |
| `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, unsafe `yaml.load` | **Pass** — none found; subprocess calls use `create_subprocess_exec` |
| SQL parameterisation (no f-string interpolation of user values) | **Pass** — f-strings interpolate static WHERE clause skeletons only; all user values passed as bound params |
| No tracked secrets (`.env`, `.key`, `.pem`, live API keys `sk-`, `AKIA`, `ghp_`) | **Pass** — only `.env.example` tracked; no live keys found |
| Secret comparisons use `secrets.compare_digest` | **Fixed** — H-1 above; token generation already uses `secrets.token_hex` |
| Dockerfile has non-root USER | **Fixed** — M-1 above |
| docker-compose does not publish unauthenticated services beyond loopback | **Flagged** — L-1 above (deferred) |
| `pip-audit` CVEs | **Flagged** — L-2 above (deferred; build-time only) |

---

## Positive Observations

- Token generation (`cv_team_` and `cv_chat_` IDs) correctly uses `secrets.token_hex()`.
- All `/admin/*` and `/chat/*` (post-login) endpoints properly call their auth helpers.
- No use of dangerous subprocess patterns (`shell=True`) — all CLI provider adapters use `create_subprocess_exec`.
- SQL queries consistently use parameterised `?` placeholders; no raw string interpolation of user data into SQL.
- No secrets committed to the repository; `.env` is gitignored and only `.env.example` is tracked.
- Team token list redaction in `_team_to_redacted_dict` prevents full Bearer token exposure via the list API.
- Per-team spend cache invalidation after each call prevents budget cap races from being silently unbounded.

---

## Remediation Status

| ID  | Title | Status |
|-----|-------|--------|
| H-1 | Timing attack on admin token comparison | **Fixed** (this PR) |
| M-1 | Dockerfile non-root USER missing | **Fixed** (this PR) |
| M-2 | `/metrics` unauthenticated | Deferred — design decision required |
| L-1 | docker-compose binds on all interfaces | Deferred — operator config |
| L-2 | pip / setuptools CVEs | Deferred — dependency-update PR |
| L-3 | Admin auth open by default | Deferred — design decision required |
