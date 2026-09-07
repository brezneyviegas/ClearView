# Security Audit — 2026-09-07

**Auditor:** Automated daily routine  
**Repo:** ClearView (Python/FastAPI LLM gateway)  
**Branch audited:** `main` (HEAD: `e8ad2e3`)  
**Previous report:** None (first audit)  
**Audit date:** 2026-09-07

---

## Severity Summary

| Severity | Total | Fixed in this PR | Deferred |
|----------|-------|-----------------|---------|
| High     | 0     | 0               | 0        |
| Medium   | 3     | 2               | 1        |
| Low      | 3     | 0               | 3        |
| **Total**| **6** | **2**           | **4**    |

---

## High Findings

_None._

---

## Medium Findings

### M-1 · Non-constant-time admin token comparison — FIXED

**Location:** `app/main.py:301`  
**Risk:** The `_admin_auth` guard compares the incoming bearer token against the expected `CLEARVIEW_ADMIN_TOKEN` using the Python `!=` operator:

```python
if token != expected:
    raise HTTPException(status_code=401, detail="invalid admin token")
```

Python string comparison short-circuits on the first differing byte, leaking timing information. An adversary making thousands of requests against a reachable admin endpoint can binary-search the correct token character by character in O(n) time rather than brute-forcing the full entropy.  
**Remediation:** Replace with `secrets.compare_digest(token, expected)` (constant-time). Applied in this PR.

---

### M-2 · Dockerfile runs app as root — FIXED

**Location:** `Dockerfile`  
**Risk:** The Dockerfile has no `USER` directive. The container process runs as UID 0 (root). If the application is compromised through any vulnerability (e.g., path traversal, SSRF to internal services, deserialization), the attacker has full root access inside the container and can more easily pivot via container escape vulnerabilities.  
**Remediation:** Add a non-privileged user and switch to it before the `CMD`. The `/data` volume is owned correctly via `chown`. Applied in this PR.

---

### M-3 · `/metrics` endpoint unauthenticated — DEFERRED

**Location:** `app/main.py:2538–2570`  
**Risk:** The Prometheus `/metrics` endpoint is publicly accessible with no authentication guard. It exposes call counts, cost totals per tier/provider, and team identifiers to any HTTP client that can reach the gateway. This leaks operational information that could help an adversary understand traffic patterns and team membership.  
**Proposed design:** Add an optional `CLEARVIEW_METRICS_TOKEN` environment variable; when set, the `/metrics` handler calls a dedicated auth helper that accepts either Bearer token or HTTP Basic auth (Prometheus convention). When the env var is unset, the endpoint remains open (backward-compatible for local dev). This is deferred because adding authentication to a Prometheus endpoint changes the scraper configuration for anyone already using it.

---

## Low Findings

### L-1 · `cv_session` cookie missing `secure` flag — DEFERRED

**Location:** `app/main.py:2651–2656`  
**Risk:** The `/chat/login` handler sets the `cv_session` cookie (which contains the team's raw bearer token) without `secure=True`. If the gateway is reachable over plain HTTP, the token will be transmitted in cleartext on every subsequent browser request.  
**Proposed design:** Accept a `CLEARVIEW_COOKIE_SECURE` env var (default `false`). When `true`, pass `secure=True` to `set_cookie`. Deployment docs should recommend enabling this for any HTTPS-fronted deployment. Deferred because forcing it on by default would silently break all non-TLS deployments.

---

### L-2 · docker-compose publishes on all interfaces — DEFERRED

**Location:** `docker-compose.yml:8–9`  
**Risk:** `ports: - "8000:8000"` binds to all host network interfaces (0.0.0.0). On a multi-tenant server or developer laptop on a shared network, the gateway (including admin endpoints when `CLEARVIEW_ADMIN_TOKEN` is unset) is reachable from the local network without explicitly intending to expose it.  
**Proposed design:** Change to `"127.0.0.1:8000:8000"` in the compose file and document the bind in the README. Deferred as behavior-changing for deployments that rely on the current binding.

---

### L-3 · CVEs in pip and setuptools (build tooling) — DEFERRED

**Tool:** `pip-audit` against installed packages  
**Risk:** Several CVEs in pip (24.0) and setuptools (79.0.1) are present in the build environment and in the Docker image (pip is not removed after `pip install .`):

| Package    | Version | CVE IDs                                                      | Fix Version |
|------------|---------|--------------------------------------------------------------|-------------|
| pip        | 24.0    | PYSEC-2026-196, PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-3721 | 26.1.2 / 26.2 |
| setuptools | 79.0.1  | PYSEC-2026-3447                                              | 83.0.0      |

These are build tools; they are not imported by the app at runtime. However, pip remains installed in the Docker image and could be invoked by an attacker who achieves code execution. Deferred pending CVE-detail review and dependency-update strategy; upgrading pip/setuptools in the Docker build is the recommended fix.

---

## Carried-Over Deferred Items

None (first audit).

---

## Audit Checklist Status

| Check | Status | Notes |
|-------|--------|-------|
| Auth coverage — every route gated | ✅ PASS | All admin routes call `_admin_auth`. All `/chat/*` routes call `_require_chat_team`. `/health`, `/v1/models`, `/feedback`, `/chat` (page), `/chat/login`, `/chat/logout` are intentionally open per design. `/metrics` flagged as M-3. |
| Injection — shell=True, os.system, eval, exec, pickle, yaml.load | ✅ PASS | Subprocess usage is `asyncio.create_subprocess_exec` (no shell). No `eval`, `exec`, `pickle`, or unsafe `yaml.load` found. |
| SQL parameterization | ✅ PASS | All raw SQLite queries in `app/main.py` use `?` placeholders; `WHERE` clauses interpolate only static template strings, not caller-supplied values. |
| Tracked secrets (env/key/pem files, live key patterns) | ✅ PASS | No `.env`, `.pem`, or secret files tracked. Patterns `sk-`, `AKIA`, `ghp_` appear only in test fixtures (`sk-x`, `sk-test`) — not live keys. |
| Token generation uses `secrets` module | ✅ PASS | `teams.py:117` uses `secrets.token_hex(16)`. `random` in `main.py` is used only for probabilistic sampling, not for cryptographic purposes. |
| Token comparisons use `secrets.compare_digest` | ⚠️ FIXED (M-1) | `_admin_auth` used `!=` — fixed in this PR. |
| Dockerfile non-root USER | ⚠️ FIXED (M-2) | No USER directive — fixed in this PR. |
| docker-compose loopback binding | ⚠️ M-3 | Publishes on all interfaces — deferred (L-2). |
| Dependency CVEs | ⚠️ LOW | pip + setuptools CVEs — deferred (L-3). |

---

## Positive Observations

- **Token generation is correct:** Team bearer tokens (`cv_team_<32 hex chars>`) are minted with `secrets.token_hex(16)` — cryptographically strong and correctly prefixed.
- **SQL parameterization is consistent:** All SQLite queries in the codebase use `?` placeholders. No f-string interpolation of user-controlled values into SQL was found.
- **No injection vectors found:** No `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, or unsafe `yaml.load(` in any application code. Subprocesses use `asyncio.create_subprocess_exec` (argument list, no shell interpretation).
- **No live secrets tracked:** Git repository contains no `.env`, `.pem`, or secret files. Test fixtures use clearly fake placeholder keys (`sk-x`, `sk-test`).
- **Cookie httponly flag set correctly:** The `cv_session` cookie has `httponly=True` and `samesite="lax"`, preventing JavaScript access and most CSRF vectors.
- **Team token redaction in listing:** Admin list endpoints (`/admin/teams`) return only `id_short` (18-char prefix + `...`), preventing full bearer token exposure through the explorer UI.

---

## Remediation Status

| ID  | Finding                              | Status      |
|-----|--------------------------------------|-------------|
| M-1 | Non-constant-time token comparison   | **FIXED** — `secrets.compare_digest` in this PR |
| M-2 | Dockerfile runs as root              | **FIXED** — non-root USER added in this PR |
| M-3 | `/metrics` unauthenticated           | Deferred — design proposed above |
| L-1 | `cv_session` missing `secure` flag   | Deferred — design proposed above |
| L-2 | docker-compose all-interface binding | Deferred — behavior-changing |
| L-3 | pip / setuptools CVEs                | Deferred — upgrade pip in Dockerfile |
