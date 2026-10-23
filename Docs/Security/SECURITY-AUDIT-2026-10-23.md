# ClearView Security Audit — 2026-10-23

**Auditor:** Automated daily audit (Claude Code)  
**Branch audited:** `main` @ `e8ad2e3`  
**Previous report:** None (first audit)  
**Scope:** Full codebase sweep + dependency audit

---

## Severity Summary

| Severity | Count | Fixed this run | Deferred |
|----------|-------|---------------|---------|
| High     | 2     | 1             | 1       |
| Medium   | 1     | 0             | 1       |
| Low      | 3     | 1             | 2       |
| **Total**| **6** | **2**         | **4**   |

---

## High Findings

### H-1 · Non-constant-time admin token comparison · **FIXED**

**Location:** `app/main.py:301` — `_admin_auth()`  
**Risk:** The admin token comparison uses Python's `!=` operator, which short-circuits on the first differing byte. A timing oracle on a high-latency network allows an attacker to enumerate `CLEARVIEW_ADMIN_TOKEN` one byte at a time without triggering rate limits. Admin endpoints control team creation/deletion and routing policy.  
**Remediation:** Replace `if token != expected:` with `if not secrets.compare_digest(token, expected):` (and import `secrets` in `main.py`).  
**Status:** Fixed in this PR.

---

### H-2 · Dockerfile runs as root (no USER directive) · **FIXED**

**Location:** `Dockerfile` (no `USER` line before `CMD`)  
**Risk:** The container process runs as UID 0 (root). If the application is compromised via a code-execution vulnerability, the attacker gains full root access to the container filesystem and any mounted volumes (including `/data` with the SQLite database containing all team tokens and telemetry).  
**Remediation:** Add a non-root system user and switch to it before `CMD`.  
**Status:** Fixed in this PR.

---

## Medium Findings

### M-1 · `/metrics` endpoint has no authentication · **DEFERRED**

**Location:** `app/main.py:2538` — `GET /metrics`  
**Risk:** The Prometheus metrics endpoint exposes per-team request counts, cost totals, token throughput, routing tier breakdowns, and provider names without any authentication. An unauthenticated caller learns tenant usage patterns, active providers, and cost structure.  
**Proposed design:** Add an optional `CLEARVIEW_METRICS_TOKEN` environment variable. When set, `GET /metrics` requires `Authorization: Bearer <token>` (separate from `CLEARVIEW_ADMIN_TOKEN` to support Prometheus scraper auth models). When unset, behavior is unchanged for backwards compatibility.  
**Why deferred:** Adding unconditional auth would break existing Prometheus scrapers. Needs explicit operator sign-off on the token-name choice and whether to share the admin token.

---

## Low Findings

### L-1 · `pip` 24.0 has 6 known CVEs · **DEFERRED**

**Location:** `.venv` (dev tooling); affects Docker build-time pip  
**CVEs:** PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-196, PYSEC-2026-3721  
**Fix version:** pip >= 26.2.2  
**Risk:** These CVEs affect pip during package installation (build time), not the running application. Impact is limited to the `docker build` phase and local development installs.  
**Proposed fix:** Add `RUN pip install --upgrade "pip>=26.2.2"` to the Dockerfile before the application install step, and pin pip in `pyproject.toml` dev extras.  
**Why deferred:** Minor build-time risk; no runtime impact. No user sign-off needed but scheduled for next routine maintenance window.

---

### L-2 · `setuptools` 79.0.1 CVE · **DEFERRED**

**Location:** `.venv` (dev tooling); affects Docker build-time setuptools  
**CVE:** PYSEC-2026-3447  
**Fix version:** setuptools >= 83.0.0  
**Risk:** Build-time CVE in setuptools. No runtime impact after the package is installed.  
**Proposed fix:** Add `RUN pip install --upgrade "setuptools>=83.0.0"` to Dockerfile, and pin in pyproject.toml dev extras.  
**Why deferred:** Same as L-1.

---

### L-3 · `/v1/models` endpoint is unauthenticated · **ACCEPTED / DEFERRED**

**Location:** `app/main.py:364` — `GET /v1/models`  
**Risk:** The model listing is accessible without credentials, revealing the full tier/provider configuration (which models are configured in each tier). This is intentional by design — IDE clients (VS Code Continue, Cline, etc.) call this endpoint before authentication is configured.  
**Recommendation:** Document this explicitly in `policy.yaml` or a `SECURITY.md`. Consider adding `_client_key_allowed(request)` when `CLEARVIEW_CLIENT_KEYS` is set so that shared deployments don't expose the model list.  
**Why deferred:** Behavior-changing; IDE compatibility at stake.

---

## Carried-Over Deferred Findings

None (first audit).

---

## Positive Observations

- **Team tokens** are generated with `secrets.token_hex(16)` (128-bit entropy, 32-char hex, prefixed `cv_team_`). Cryptographically secure.
- **Chat conversation IDs** use `secrets.token_hex(8)`. Secure.
- **All SQL queries** use parameterized binds (`?` placeholders). Two queries use f-strings to build the `WHERE` clause, but they interpolate only hardcoded column name strings and `?` placeholders — never user values. No SQL injection risk.
- **No tracked secrets**: `git ls-files` found no `.env`, `.key`, or `.pem` files. No live-looking API keys (`sk-`, `AKIA`, `ghp_`) found in tracked files.
- **No shell injection**: `asyncio.create_subprocess_exec` is used throughout (not `shell=True`), with arguments as lists. No `os.system`, `eval(`, `exec(`, `pickle`, or `yaml.load(` found.
- **All `/admin/*` routes** call `_admin_auth(request)` — confirmed by grep. No gaps.
- **All `/chat/*` conversation routes** call `_require_chat_team(request)` — confirmed by grep. No gaps.
- **`/feedback`** documented as intentionally open (clients rate their own responses by `request_id`).
- **`/health`** open — standard pattern.
- **`/chat`**, **`/chat/login`**, **`/chat/logout`** open — intentional login surface.

---

## Remediation Status

| Finding | Status | Notes |
|---------|--------|-------|
| H-1 non-constant-time compare | ✅ Fixed | `secrets.compare_digest` in `app/main.py` |
| H-2 Dockerfile root user | ✅ Fixed | `appuser` added; `USER appuser` before CMD |
| M-1 `/metrics` no auth | ⏳ Deferred | Proposed `CLEARVIEW_METRICS_TOKEN` env var |
| L-1 pip CVEs | ⏳ Deferred | Upgrade pip ≥ 26.2.2 in Dockerfile/pyproject |
| L-2 setuptools CVE | ⏳ Deferred | Upgrade setuptools ≥ 83.0.0 |
| L-3 `/v1/models` open | ⏳ Deferred | Intentional; consider gating with client key |
