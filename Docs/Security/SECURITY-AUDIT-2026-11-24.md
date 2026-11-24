# Security Audit — 2026-11-24

**Scope:** Full repository scan (first audit; no prior report to compare against)
**Branch:** `security/audit-2026-11-24` off `main`
**Auditor:** Automated daily audit (Claude Sonnet 4.6)
**Last commit audited:** `e8ad2e3`

---

## Severity Summary

| Severity | New | Fixed in this run | Deferred |
|----------|-----|-------------------|----------|
| High     | 0   | 0                 | 0        |
| Medium   | 4   | 2                 | 2        |
| Low      | 4   | 0                 | 4        |
| **Total**| **8** | **2**           | **6**    |

---

## High Findings

_None._

---

## Medium Findings

### M1 — Timing attack on admin token comparison ✅ FIXED
**File:** `app/main.py:302` (`_admin_auth`)
**Risk:** `token != expected` uses Python's default string `__eq__`, which short-circuits on the first differing byte. An attacker who can make rapid requests to any `_admin_auth`-gated endpoint and measure response time can recover `CLEARVIEW_ADMIN_TOKEN` one byte at a time — classic timing oracle.
**Remediation applied:** Replaced `if token != expected` with `if not secrets.compare_digest(token, expected)`.

---

### M2 — Timing attack on client key set membership ⚠️ DEFERRED
**File:** `app/main.py:324` (`_client_key_allowed`)
**Risk:** `cand in allowed` checks membership in a Python `set[str]`, which uses a hash lookup. While hashing provides some implicit timing resistance (hash is computed before comparison), this is not guaranteed constant-time and leaks key length via `__hash__`/`__eq__` path. A remote attacker measuring precise latencies across many requests could potentially recover allowed key values.
**Proposed design:** Replace with `any(secrets.compare_digest(cand, k) for k in allowed)`. Note: `secrets.compare_digest` raises `TypeError` if strings differ in length, so callers must pad or handle that case. Requires a short helper and test coverage update.
**Deferred reason:** Behavior-changing — requires careful length-handling logic.

---

### M3 — Dockerfile runs container as root ✅ FIXED
**File:** `Dockerfile`
**Risk:** The container process runs as `root` (UID 0). A container escape or application-level RCE would immediately grant root access to the host (in privileged/misconfigured environments) or allow full container filesystem writes. CIS Docker Benchmark, NSA/CISA guidelines, and all major container security frameworks require a non-root UID.
**Remediation applied:** Added `RUN useradd -r -u 1001 -s /bin/false appuser && mkdir -p /data && chown appuser /data` and `USER appuser` before the `VOLUME` directive.

---

### M4 — docker-compose exposes port 8000 on all host interfaces ⚠️ DEFERRED
**File:** `docker-compose.yml:8`
**Risk:** `"8000:8000"` binds the host port to `0.0.0.0`, making the gateway reachable from any network interface (and thus any peer on the same LAN/WiFi or directly from the internet when the host is internet-facing). Unless `CLEARVIEW_ADMIN_TOKEN` and `CLEARVIEW_CLIENT_KEYS` are set, the gateway and all admin endpoints are unauthenticated.
**Remediation applied:** Changed to `"127.0.0.1:8000:8000"`.
**Deferred reason:** Teams running ClearView on a shared server or exposing it deliberately to a team network need `0.0.0.0`. The fix is applied but operators should consciously revert when needed.

> **Note:** M4 fix was applied (loopback binding is safer default). It is listed as "deferred" in the sense that operators must actively choose to revert for multi-host deployments.

---

## Low Findings

### L1 — `/metrics` endpoint has no authentication ⚠️ DEFERRED
**File:** `app/main.py:2538`
**Risk:** The Prometheus `/metrics` endpoint is entirely unauthenticated. It exposes provider names, tier labels, aggregate cost figures, and token volume. An unauthenticated user can enumerate which LLM providers are configured, estimate traffic volume, and infer team-level activity.
**Proposed design:** Add `_admin_auth(request)` call, or introduce a separate `CLEARVIEW_METRICS_TOKEN` env var so Prometheus scrapers can authenticate without sharing the admin token. For networks where the scraper is on-host, a middleware IP allowlist is an alternative.
**Deferred reason:** Adding auth breaks standard Prometheus scraping setups without coordinated configuration change.

---

### L2 — Session cookie missing `Secure` flag ⚠️ DEFERRED
**File:** `app/main.py:2651` (`chat_login`)
**Risk:** The `cv_session` cookie is set with `httponly=True, samesite="lax"` but no `secure=True`. Without the `Secure` attribute, browsers will transmit the session token over plain HTTP, making it vulnerable to network interception. This matters when ClearView is deployed behind an HTTPS terminator (nginx, Caddy) with an HTTP backend.
**Proposed design:** Set `secure=True` unconditionally, or gate it on a `CLEARVIEW_COOKIE_SECURE=1` env var that operators set when deploying with TLS. The `chat_login` function already accepts a `request` parameter so a helper like `_is_secure_scheme(request)` could auto-detect.
**Deferred reason:** Setting `secure=True` breaks the `/chat` UI over plain HTTP (the default local dev mode). Requires a config flag.

---

### L3 — pip CVEs in Docker image (6 CVEs) ⚠️ DEFERRED
**Tool:** `pip-audit`
**Package:** `pip 24.0`
**CVEs:** PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-196, PYSEC-2026-3721
**Fix version:** `pip >= 26.3`
**Risk:** These are vulnerabilities in the `pip` installer itself, not in the application. `pip` is present in the production Docker image (as a base Python image artifact) but is not invoked at runtime. Exploitation would require an attacker who can already execute code inside the container to run `pip install` — a secondary-stage risk.
**Deferred reason:** No runtime impact; fix requires updating the base image (`python:3.12-slim` ships pip 24.0) or pinning a newer pip in the Dockerfile. Low urgency given no runtime exposure.

---

### L4 — setuptools CVEs in Docker image (2 CVEs) ⚠️ DEFERRED
**Tool:** `pip-audit`
**Package:** `setuptools 79.0.1`
**CVEs:** PYSEC-2026-3447, GHSA-xmj3-9gf7-h52w
**Fix version:** `setuptools >= 84.1.0`
**Risk:** Same class as L3 — installer/build tooling CVEs, not exploitable in the production request path.
**Deferred reason:** Same as L3; upgrade setuptools pin in `pyproject.toml` or Dockerfile.

---

## Carried-Over Deferred Items

_None (first audit)._

---

## Minimum Sweep Results

| Check | Result |
|-------|--------|
| All `@app.get/post/delete` admin routes call `_admin_auth` | ✅ Pass |
| All `/chat/*` routes call `_require_chat_team` or are intentionally open | ✅ Pass |
| `GET /v1/models`, `GET /health`, `POST /feedback` are intentionally open | ✅ Documented open |
| `shell=True` / `os.system` / `eval(` / `exec(` / `pickle` | ✅ None found |
| `yaml.load` without Loader | ✅ None found |
| SQL parameterization (f-string WHERE + parameterized values) | ✅ Pass |
| Tracked secret/cert files | ✅ None found |
| Live keys in tracked files (`sk-`, `AKIA`, `ghp_`) | ✅ None found |
| Secret comparison uses `secrets.compare_digest` | ✅ Fixed (M1) |
| Token generation uses `secrets` module | ✅ Pass (`secrets.token_hex`) |
| Dockerfile non-root USER | ✅ Fixed (M3) |
| docker-compose loopback binding | ✅ Fixed (M4) |
| pip-audit CVEs in app dependencies | ✅ No app-dep CVEs; build-tool CVEs deferred (L3, L4) |

---

## Positive Observations

- **Parameterized SQL throughout.** All SQLite queries use `?` placeholders; the f-string pattern for `WHERE` clauses builds only from predefined constant strings, never from user-supplied values directly in the query string.
- **`secrets.token_hex` used for token minting.** Team tokens (`cv_team_`) and conversation IDs use the `secrets` module, producing cryptographically strong random tokens.
- **Admin route coverage is complete.** Every `/admin/*` route calls `_admin_auth`; every `/chat/*` route that accesses team data calls `_require_chat_team`.
- **No `shell=True` subprocess usage.** Provider CLI adapters use `asyncio.create_subprocess_exec` (argument list, no shell expansion).
- **Team tokens redacted in list API.** `_team_to_redacted_dict` truncates token IDs to a short prefix in the listing response, preventing accidental token exposure in logs.
- **Budget enforcement is layered.** Per-team daily, per-team monthly, and global daily caps are checked in order before dispatching any upstream call.

---

## Remediation Status

| ID | Description | Status |
|----|-------------|--------|
| M1 | `_admin_auth` timing-safe compare | **Fixed** in this PR |
| M2 | `_client_key_allowed` timing-safe compare | Deferred |
| M3 | Dockerfile non-root USER | **Fixed** in this PR |
| M4 | docker-compose loopback bind | **Fixed** in this PR |
| L1 | `/metrics` authentication | Deferred |
| L2 | Session cookie `Secure` flag | Deferred |
| L3 | pip CVEs | Deferred |
| L4 | setuptools CVEs | Deferred |
