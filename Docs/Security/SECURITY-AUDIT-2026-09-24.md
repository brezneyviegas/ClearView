# Security Audit — 2026-09-24

**Auditor:** Automated daily audit (Claude Code)
**Branch audited:** `main` (HEAD `e8ad2e3`)
**Previous audit:** None (first audit)
**Test suite result:** 359 passed, 0 failed

---

## Severity Summary

| Severity | Count | Fixed this run | Deferred |
|----------|-------|----------------|----------|
| High     | 1     | 1              | 0        |
| Medium   | 3     | 1              | 2        |
| Low      | 2     | 0              | 2        |
| **Total**| **6** | **2**          | **4**    |

---

## High Findings

### H1 — Dockerfile runs container as root (FIXED)
**Location:** `Dockerfile`
**Risk:** The image had no `USER` directive, so the application process ran as uid 0 inside the container. A container escape or path-traversal would grant root privileges on the host if Docker is misconfigured or run without user namespaces.
**Remediation applied:** Added `RUN useradd -r -u 1001 -g root clearview && mkdir -p /data && chown clearview /data` and `USER clearview` before `CMD`. Tests pass.

---

## Medium Findings

### M1 — Admin token compared with `!=` instead of `secrets.compare_digest` (FIXED)
**Location:** `app/main.py:301` (`_admin_auth`)
**Risk:** Python's `!=` operator on strings exits early on the first differing byte, leaking timing information. A remote attacker on a low-latency network could use a timing oracle to brute-force `CLEARVIEW_ADMIN_TOKEN` one character at a time.
**Remediation applied:** Replaced `if token != expected:` with `if not secrets.compare_digest(token, expected):` and added `import secrets` at the top of the file. Tests pass.

### M2 — `/metrics` Prometheus endpoint is unauthenticated (DEFERRED)
**Location:** `app/main.py:2538` (`metrics`)
**Risk:** The endpoint exposes request counts, cost aggregates, and per-provider/tier/team usage breakdowns without any authentication. On a network-accessible deployment this leaks operational intelligence to unauthenticated callers.
**Proposed design (requires sign-off):** Gate the endpoint behind `_admin_auth` and update any Prometheus scrape config to pass `Authorization: Bearer <CLEARVIEW_ADMIN_TOKEN>`. Alternatively add a dedicated `CLEARVIEW_METRICS_TOKEN` env var so metrics auth is separate from admin. Deferring because adding auth to an existing endpoint is a behavior-changing fix (would break existing scrape configs without migration guidance).

### M3 — Docker-compose publishes port 8000 on all network interfaces (DEFERRED)
**Location:** `docker-compose.yml` — `ports: - "8000:8000"`
**Risk:** The gateway (including the API, admin UI, and metrics endpoint) binds to `0.0.0.0:8000`, making it reachable from any network interface on the host — not just loopback. On a multi-tenant host this exposes the service to lateral movement.
**Proposed design (requires sign-off):** Change to `- "127.0.0.1:8000:8000"` for single-host deployments. Multi-node deployments should use a reverse proxy or explicit network binding. Deferring because restricting the bind address could break existing deployments behind a load balancer or in Kubernetes.

---

## Low Findings

### L1 — `pip` and `setuptools` CVEs in build tooling (DEFERRED)
**Detected by:** pip-audit
**Packages:**
- `pip 24.0` — PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-196, PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-3721; fix: upgrade to `pip >= 26.2.1`
- `setuptools 79.0.1` — PYSEC-2026-3447; fix: upgrade to `setuptools >= 83.0.0`

**Risk:** These CVEs affect the build environment only, not the runtime application. ClearView's published Docker image would carry these if built with the affected pip/setuptools, but the runtime Python process doesn't import them.
**Remediation:** Add `RUN pip install --upgrade pip setuptools` after the base image is set in `Dockerfile`, or pin a newer base image. Deferring as it requires Dockerfile layer adjustment and retesting.

### L2 — `/v1/models` endpoint is unauthenticated (DEFERRED — by design)
**Location:** `app/main.py:364`
**Risk:** Returns the list of all configured models and provider tiers. This is standard behaviour for OpenAI-compatible gateways (clients probe `/v1/models` before sending requests). The information disclosure is low-value in most deployments.
**Notes:** `_client_key_allowed` is intentionally not called here; the OpenAI spec expects this endpoint to be public. If model enumeration is a concern, gate it behind `_client_key_allowed`. No automated fix applied.

---

## Carried-Over Deferred Items

None — this is the first audit run.

---

## Positive Observations

- All admin endpoints (`/admin/*`) correctly call `_admin_auth` before touching data.
- All `/chat/*` endpoints (except the open `/chat/login`, `/chat/logout`, and the read-only landing page) call `_require_chat_team`.
- Token generation uses `secrets.token_hex` everywhere (see `app/teams.py:117`, `app/chat.py:62`).
- No shell injection risk: all subprocess calls in `app/providers/` use `asyncio.create_subprocess_exec` (no `shell=True`).
- No SQL injection: all parameterised values are passed via `?` placeholders; f-strings in SQL only interpolate hardcoded clause fragments, never user input.
- No tracked secrets: `git ls-files` reveals no `.env`, `.pem`, or `.key` files; grep found no live-looking keys (`sk-`, `AKIA`, `ghp_`).
- Team tokens use 40-character hex-encoded random ids (256 bits of entropy).
- HTTP-only, `SameSite=Lax` cookie set on `/chat/login` — appropriate protections.

---

## Remediation Status

| ID | Finding | Status |
|----|---------|--------|
| H1 | Dockerfile runs as root | **FIXED** (this PR) |
| M1 | Non-constant-time admin token comparison | **FIXED** (this PR) |
| M2 | `/metrics` unauthenticated | Deferred — behavior-changing |
| M3 | docker-compose binds to 0.0.0.0 | Deferred — behavior-changing |
| L1 | pip/setuptools CVEs in build tools | Deferred — Dockerfile upgrade needed |
| L2 | `/v1/models` unauthenticated | Deferred — by design |
