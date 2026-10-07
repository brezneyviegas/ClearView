# ClearView Security Audit — 2026-10-07

**Auditor:** Automated daily security audit (Claude Code)
**Branch audited:** `main` @ e8ad2e3  
**Audit scope:** Full initial audit (no prior reports exist)

---

## Severity Summary

| Severity | Count | Fixed | Deferred |
|----------|-------|-------|----------|
| High     | 1     | 1     | 0        |
| Medium   | 2     | 1     | 1        |
| Low      | 2     | 0     | 1        |
| Info     | 1     | 0     | —        |

---

## High Findings

### H1 — Non-constant-time admin token comparison

**File/Location:** `app/main.py:301` (`_admin_auth`)  
**Risk:** The comparison `token != expected` uses Python's default `!=` operator, which short-circuits on the first differing byte. An attacker on the same network can mount a timing attack to recover `CLEARVIEW_ADMIN_TOKEN` one character at a time by measuring response latency differences. All `/admin/*` endpoints are gated on this check, so a successful attack grants full admin access.  
**Remediation:** Replace with `secrets.compare_digest(token, expected)` (constant-time).  
**Status:** **FIXED** in this PR — `import secrets` added; comparison changed to `not secrets.compare_digest(token, expected)`.

---

## Medium Findings

### M1 — Dockerfile runs as root

**File/Location:** `Dockerfile`  
**Risk:** No `USER` directive; the container process runs as UID 0 (root). If the application is compromised via RCE, the attacker has root inside the container, making container escape and privilege escalation significantly easier.  
**Remediation:** Create a dedicated low-privilege system user and drop to it before `CMD`.  
**Status:** **FIXED** in this PR — a `clearview` system user (UID/GID 1001) is created; `/data` is pre-chowned to that user; `USER clearview` is set before `CMD`. Operators upgrading existing deployments with pre-existing named volumes should `docker exec` to fix the volume ownership: `chown -R 1001:1001 /data`.

### M2 — `/metrics` endpoint unauthenticated

**File/Location:** `app/main.py:2538` (`metrics()`)  
**Risk:** The Prometheus `/metrics` endpoint is served without calling `_admin_auth`. It exposes per-tier call counts, token totals, spend totals, and provider names to any unauthenticated caller who can reach the port. In any deployment where the port is exposed beyond localhost, this leaks operational data.  
**Deferred — proposed design:**  
  - Add `_admin_auth(request)` as the first line of `metrics()`.  
  - When `CLEARVIEW_ADMIN_TOKEN` is unset, `_admin_auth` is a no-op (dev mode stays open).  
  - When the token is set, Prometheus scrapers must present `Authorization: Bearer <CLEARVIEW_ADMIN_TOKEN>` — configure via Prometheus `bearer_token` or `authorization.credentials`.  
  - Alternatively, introduce a separate `CLEARVIEW_METRICS_TOKEN` to decouple metrics and admin auth scopes.  
  **Not fixed now** to avoid breaking existing scrapers without an operator-controlled migration path.

---

## Low Findings

### L1 — Session cookie missing `secure` flag

**File/Location:** `app/main.py:2651` (`chat_login()`)  
**Risk:** The `cv_session` cookie is set with `httponly=True, samesite="lax"` but without `secure=True`. Over a plain HTTP connection the token is transmitted in cleartext and can be captured by a network observer.  
**Deferred — proposed design:**  
  - Set `secure=True` on the cookie whenever `CLEARVIEW_COOKIE_SECURE` env var is set (default false for local dev, true for production).  
  - This avoids breaking the common local-dev case (HTTP on 127.0.0.1) while hardening production.

### L2 — `/v1/models` endpoint unauthenticated

**File/Location:** `app/main.py:364` (`list_models()`)  
**Risk:** Lists all configured virtual and underlying model IDs without authentication, revealing provider selection and tier structure to any caller. This matches the OpenAI models API convention (intentionally public), but in a gateway with sensitive routing logic it may leak more than desired.  
**Not fixed** — behaviour is intentional per OpenAI spec. Operator can gate the port at the network level. Noted for awareness.

---

## Deferred Items (carried forward each run)

None from previous audits (this is the first run).

---

## Minimum-sweep Checklist

| Check | Result |
|-------|--------|
| All `@app.get/post/delete` routes call `_admin_auth`, `_client_key_allowed`, `_resolve_team`, or `_require_chat_team`, or have a documented reason to be open | `/v1/models` open (OpenAI-spec); `/health` open (ops); `/feedback` open (client-facing); `/metrics` open — flagged as M2; `/chat` open (serves login page); `/chat/login` and `/chat/logout` open (auth flow) |
| `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, `yaml.load` | None found in `app/`. CLI providers use `asyncio.create_subprocess_exec` (no shell injection) |
| SQL uses parameterized queries only | All f-string SQL in `app/teams.py` and `app/telemetry.py` interpolates only hardcoded column/clause strings; all user values go through `?` parameters. Safe. |
| No tracked secrets (`.env`, key/pem files, live-looking key strings) | No `*.env`, `*.pem`, or `*.key` files tracked. No `sk-`, `AKIA`, or `ghp_` patterns found in tracked files. |
| Secret comparisons use `secrets.compare_digest` | Fixed in H1 above. |
| Token generation uses `secrets` module | `teams.py:117` uses `secrets.token_hex(16)`. |
| Dockerfile has non-root `USER` | Fixed in M1 above. |
| `docker-compose` does not publish unauthenticated services beyond loopback | Port `8000:8000` publishes on all interfaces. Documented — operator should use `127.0.0.1:8000:8000` or add `CLEARVIEW_ADMIN_TOKEN`. |

---

## pip-audit Results

| Package    | Version | CVE ID           | Fix Version |
|------------|---------|------------------|-------------|
| pip        | 24.0    | PYSEC-2026-1795  | 25.3        |
| pip        | 24.0    | PYSEC-2026-1796  | 26.0        |
| pip        | 24.0    | PYSEC-2026-196   | 26.1.2      |
| pip        | 24.0    | PYSEC-2026-2875  | 26.1        |
| pip        | 24.0    | PYSEC-2026-2876  | 26.1        |
| pip        | 24.0    | PYSEC-2026-3721  | 26.2        |
| setuptools | 79.0.1  | PYSEC-2026-3447  | 83.0.0      |

All CVEs are in build tooling (`pip`, `setuptools`), not application runtime dependencies. No application dependency CVEs found. Remediation: update the base image or pin pip/setuptools in the Dockerfile via `RUN pip install --upgrade pip setuptools`.

---

## Positive Observations

- Team token generation uses `secrets.token_hex(16)` (cryptographically secure, 128-bit entropy).
- All `/admin/*` endpoints consistently call `_admin_auth`.
- All `/chat/*` data endpoints consistently call `_require_chat_team`.
- SQL in `app/teams.py` and `app/telemetry.py` correctly parameterizes user-supplied values.
- No tracked secrets or key files found.
- Prompt hashes use SHA-256 (app/main.py:1626) — PII-safe telemetry.
- Cookie is `HttpOnly` and `SameSite=Lax`, limiting XSS and CSRF surface.

---

## Remediation Status Summary

| ID | Finding | Status |
|----|---------|--------|
| H1 | Non-constant-time admin token comparison | Fixed in `security/audit-2026-10-07` |
| M1 | Dockerfile runs as root | Fixed in `security/audit-2026-10-07` |
| M2 | `/metrics` endpoint unauthenticated | Deferred — design proposed above |
| L1 | Session cookie missing `secure` flag | Deferred — design proposed above |
| L2 | `/v1/models` unauthenticated | Accepted / intentional (OpenAI spec) |
