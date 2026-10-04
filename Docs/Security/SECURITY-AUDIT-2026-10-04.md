# Security Audit — 2026-10-04

**Auditor:** Automated daily audit (Claude Code)  
**Baseline:** First audit — no prior report to compare against  
**Branch audited:** `main` (commit `e8ad2e3`)  
**Audit branch:** `security/audit-2026-10-04`

---

## Severity Summary

| Severity | New | Fixed This Run | Deferred |
|----------|-----|----------------|----------|
| High     | 0   | 0              | 0        |
| Medium   | 2   | 2              | 0        |
| Low      | 3   | 0              | 3        |

---

## High Findings

_None._

---

## Medium Findings

### M-1 — Non-constant-time admin token comparison  **[FIXED]**

**File/location:** `app/main.py:301` (`_admin_auth`)  
**Risk:** Python's string `!=` operator short-circuits on the first differing byte, making the comparison timing-dependent. An attacker with repeated low-latency access to `/admin/*` routes can determine the value of `CLEARVIEW_ADMIN_TOKEN` byte-by-byte via timing oracle, bypassing the gateway's admin protection.  
**Remediation applied:** Replaced `token != expected` with `secrets.compare_digest(token, expected)` and added `import secrets` to `app/main.py`. Tests confirm no regressions.

---

### M-2 — Dockerfile runs process as root  **[FIXED]**

**File/location:** `Dockerfile` (missing `USER` directive)  
**Risk:** The container runs as UID 0. Any RCE or path-traversal exploit against the FastAPI process inherits full root access inside the container, increasing the blast radius of a compromise (read arbitrary files, bind to privileged ports, escape some container configurations).  
**Remediation applied:** Added `useradd -m -u 1001 appuser`, `chown appuser /data`, and `USER appuser` before `CMD` in `Dockerfile`. The `/data` volume ownership is set during build so the non-root process can write the SQLite database.

---

## Low Findings

### L-1 — `/metrics` endpoint unauthenticated  **[DEFERRED]**

**File/location:** `app/main.py:2538` (`GET /metrics`)  
**Risk:** The Prometheus-format endpoint exposes per-model, per-tier, per-team request counts and cumulative cost totals with no authentication. Any party that can reach port 8000 can enumerate team IDs and observe billing data. The `docker-compose.yml` publishes port 8000 on all interfaces (`0.0.0.0`).  
**Proposed design:** Gate `/metrics` behind `_admin_auth` (add the same call that all other `/admin/*` routes use), and document the scrape bearer token in the operator setup guide. Alternatively, split metrics onto a separate, non-public port via a `CLEARVIEW_METRICS_PORT` env var. Deferred because changing the endpoint contract may break existing Prometheus scrapers operators have configured; requires a minor docs/migration notice.

---

### L-2 — Session cookie missing `Secure` flag  **[DEFERRED]**

**File/location:** `app/main.py:2651–2655` (`/chat/login`)  
**Risk:** The `cv_session` cookie is set with `httponly=True, samesite="lax"` but without `secure=True`. On deployments served over plain HTTP the cookie is transmitted in clear text, allowing an on-path attacker to steal the session token.  
**Proposed design:** Add `secure=True` to `set_cookie` guarded by a `CLEARVIEW_COOKIE_SECURE=1` env var (default off to preserve the localhost dev experience). Deferred because activating it without a matching HTTPS reverse-proxy breaks the chat UI entirely; requires coordinated infrastructure change.

---

### L-3 — Development tooling CVEs (pip / setuptools)  **[DEFERRED]**

**Tool:** `pip-audit` on installed dev venv  
**Findings:**

| Package    | Version | CVE IDs                                                                   | Fix Version |
|------------|---------|---------------------------------------------------------------------------|-------------|
| pip        | 24.0    | PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-196, PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-3721 | ≥26.2.1 |
| setuptools | 79.0.1  | PYSEC-2026-3447                                                           | ≥83.0.0     |

**Risk:** These CVEs are in build/packaging tools only. The production runtime dependencies (fastapi, litellm, uvicorn, pydantic, pyyaml, jinja2, httpx) have no known CVEs at this version set. The pip/setuptools versions bundled in the developer venv and in the `python:3.12-slim` Docker base image should be upgraded in CI and the Dockerfile.  
**Proposed design:** Add `RUN pip install --upgrade pip setuptools` after the `FROM` line in the Dockerfile. Update the CI setup step likewise. Deferred as a non-blocking hygiene item with no direct runtime exploit path.

---

## Carried-Over Deferred Items

_None (first audit)._

---

## Positive Observations

- **Auth coverage complete:** Every `@app.get/post/delete/patch` route in `app/main.py` is covered — admin routes call `_admin_auth`, client-facing routes call `_client_key_allowed`+`_resolve_team`, and chat UI routes call `_require_chat_team`. Intentionally open routes (`/health`, `/v1/models`, `/feedback`, `/chat`, `/chat/login`, `/chat/logout`) are either standard convention (health/models) or documented as open by design.
- **No injection vectors:** All subprocess calls use `asyncio.create_subprocess_exec` (arg-list form, never `shell=True`). No `eval()`, `exec()`, `pickle`, or unsafe `yaml.load()` usage found.
- **SQL fully parameterized:** Dynamic WHERE clauses are built from constant SQL fragments; all user-supplied values flow through `?` placeholders. No f-string interpolation of user input into SQL queries.
- **No tracked secrets:** `git ls-files` shows only `.env.example` and `clearview-client.env.example` — no real keys or PEM files. Test monkeypatches use clearly-fake `"sk-x"` / `"sk-test"` values.
- **Team token generation is secure:** `teams.create()` uses `secrets.token_hex(16)` (32 hex chars, 128 bits entropy).
- **Cookie is HttpOnly + SameSite:** Protects against XSS theft and CSRF; `Secure` flag is the only gap (see L-2).

---

## Remediation Status

| ID  | Description                               | Status   | Commit |
|-----|-------------------------------------------|----------|--------|
| M-1 | Constant-time admin token compare         | Fixed    | this PR |
| M-2 | Dockerfile non-root USER                  | Fixed    | this PR |
| L-1 | `/metrics` auth gate                      | Deferred | — |
| L-2 | Session cookie `Secure` flag              | Deferred | — |
| L-3 | pip/setuptools CVE hygiene                | Deferred | — |

---

## Test Suite

```
359 passed, 2 warnings in 162.32s
```

All tests pass on the remediation branch. Fixes were validated against `tests/test_api.py`, `tests/test_chat.py`, and `tests/test_teams.py` (81 tests) with no regressions.
