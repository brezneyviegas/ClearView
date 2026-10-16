# Security Audit — 2026-10-16

**Repo:** ClearView (Python/FastAPI LLM gateway)  
**Auditor:** Automated daily audit (Claude Code)  
**Branch audited:** `main` @ `e8ad2e3`  
**Previous report:** None (first audit)

---

## Severity Summary

| Severity | New | Fixed this run | Deferred |
|----------|-----|----------------|----------|
| High     | 0   | 0              | 0        |
| Medium   | 3   | 2              | 1        |
| Low      | 2   | 1              | 1        |
| **Total**| **5**| **3**         | **2**    |

---

## Findings

### MEDIUM — Non-constant-time admin token comparison ✅ FIXED

**File/location:** `app/main.py:301` (`_admin_auth`)  
**Risk:** Python's `!=` string comparison short-circuits on the first mismatching byte, leaking timing information about the shared prefix length with the real token. A local-network attacker with many requests could recover `CLEARVIEW_ADMIN_TOKEN` character by character.  
**Remediation applied:** Replaced `token != expected` with `secrets.compare_digest(token, expected)` and added `import secrets` at the top of the file.  
**Tests:** 359/359 pass.

---

### MEDIUM — `/metrics` Prometheus endpoint unauthenticated ✅ FIXED

**File/location:** `app/main.py:2538` (`metrics`)  
**Risk:** The `/metrics` endpoint exposed Prometheus counters including `team_id`, provider names, tier labels, native USD costs, and total token counts without requiring any authentication — even when `CLEARVIEW_ADMIN_TOKEN` was set to lock all other admin routes.  
**Remediation applied:** Added `request: Request` parameter and called `_admin_auth(request)` at the start of the handler, matching the pattern of all other `/admin/*` endpoints. When `CLEARVIEW_ADMIN_TOKEN` is unset (dev mode), the endpoint remains open as before.  
**Tests:** 359/359 pass.

---

### MEDIUM — Dockerfile runs container as root (deferred)

**File/location:** `Dockerfile` — no `USER` directive  
**Risk:** If any dependency or the application itself contains a path-traversal or RCE vulnerability, the attacker gains root inside the container, making container escape and host compromise significantly easier.  
**Status:** ✅ FIXED — Added non-root `clearview` user and `USER clearview` directive. The `/data` volume directory is pre-created and chowned to `clearview` so the database is writable at runtime.  
**Tests:** 359/359 pass (Dockerfile not exercised by the test suite; change is mechanical).

---

### LOW — Session cookie missing `Secure` flag (deferred)

**File/location:** `app/main.py:2651-2655` (`chat_login`)  
**Risk:** The `/chat/login` response sets `cv_session` with `httponly=True, samesite="lax"` but no `secure=True`. If the gateway is served over plain HTTP in a non-localhost deployment, the cookie (which is a valid team bearer token) is transmitted in cleartext.  
**Proposed design:** Gate the `secure` flag on a new env var `CLEARVIEW_SECURE_COOKIES=1`, or infer it from `CLEARVIEW_HOST` not being loopback. Default off to preserve local dev UX. This is a one-line change but requires operator documentation.  
**Reason for deferral:** Behavior-changing for any HTTP deployment; low risk in the intended local-dev default topology. Needs operator docs update.

---

### LOW — pip and setuptools CVEs in build environment (deferred)

**Tool:** `pip-audit` against the installed venv  
**Affected packages:**
- `pip 24.0` — PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-196, PYSEC-2026-3721 — fix: ≥ 26.2
- `setuptools 79.0.1` — PYSEC-2026-3447 — fix: ≥ 83.0.0

**Risk:** These are build-tool CVEs, not runtime application CVEs. They affect the `pip` and `setuptools` binaries used to install packages, not the deployed application itself. Risk is primarily during CI/CD or `docker build`.  
**Proposed remediation:** Add `RUN pip install --upgrade pip setuptools` to `Dockerfile` after the `FROM` line, and pin versions in CI. No code change needed in application logic.  
**Reason for deferral:** No application runtime CVEs found; build-tool upgrades carry no functional risk but were deferred to keep the Dockerfile change minimal for this audit cycle.

---

## Carried-over Deferred Items

None (first audit).

---

## Positive Observations

- **Parameterized SQL throughout:** All queries in `telemetry.py`, `teams.py`, and `chat.py` use `?` placeholders with value tuples. No f-string or concatenation SQL injection found.
- **Cryptographically secure token generation:** Team tokens use `secrets.token_hex(16)` (40-char tokens); conversation IDs use `secrets.token_hex(8)`. No `random` module used for secrets.
- **No dangerous code patterns:** No `shell=True`, `os.system()`, `eval()`, `exec()`, `pickle`, or unsafe `yaml.load()` found in application code.
- **No tracked secrets:** `git ls-files` shows no `.env`, `.key`, `.pem` files. No live-looking keys (`sk-`, `AKIA`, `ghp_`) found in tracked files.
- **Comprehensive admin auth:** All 18 `/admin/*` routes consistently gate with `_admin_auth`; all `/chat/*` routes gate with `_require_chat_team`. Intentionally open routes (`/health`, `/feedback`, `/v1/models`) have documented reasons.
- **HTTPOnly + SameSite cookie:** Session cookie uses `httponly=True, samesite="lax"` preventing JS access and most CSRF vectors.
- **No runtime application CVEs:** `pip-audit` found no CVEs in any runtime dependency (fastapi, uvicorn, litellm, pydantic, pyyaml, jinja2, etc.).

---

## Remediation Status Summary

| ID | Finding | Severity | Status |
|----|---------|----------|--------|
| AUD-2026-10-16-01 | Non-constant-time admin token compare | Medium | Fixed (`secrets.compare_digest`) |
| AUD-2026-10-16-02 | `/metrics` unauthenticated | Medium | Fixed (`_admin_auth` added) |
| AUD-2026-10-16-03 | Docker runs as root | Medium | Fixed (non-root USER added) |
| AUD-2026-10-16-04 | Session cookie missing Secure flag | Low | Deferred (needs CLEARVIEW_SECURE_COOKIES env var design) |
| AUD-2026-10-16-05 | pip/setuptools build-tool CVEs | Low | Deferred (no runtime risk; upgrade in next Docker build cycle) |
