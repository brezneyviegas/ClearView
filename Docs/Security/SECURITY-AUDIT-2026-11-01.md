# ClearView Security Audit — 2026-11-01

**Auditor:** Automated daily security routine  
**Branch audited:** `main` (HEAD `e8ad2e3`)  
**Audit date:** 2026-11-01  
**Prior report:** None (first audit)

---

## Severity Summary

| Severity | Total | Fixed in this PR | Deferred |
|----------|-------|-----------------|---------|
| High     | 1     | 1               | 0       |
| Medium   | 3     | 1               | 2       |
| Low      | 2     | 1               | 1       |

---

## High Findings

### H-1 — Timing Oracle in Admin Token Comparison (FIXED)

**File/Location:** `app/main.py:300` (`_admin_auth`)  
**Risk:** `_admin_auth` compared the incoming bearer token to `CLEARVIEW_ADMIN_TOKEN` using Python's `!=` operator, which short-circuits on the first differing byte. An attacker who can send many requests and measure response-time variance can recover the admin token one character at a time — a classical timing side-channel. The admin token gates all `/admin/*` endpoints (stats, telemetry, team management, policy tuning).  
**Remediation:** Replaced `token != expected` with `hmac.compare_digest(token, expected)`. `hmac` was already in the stdlib; `import hmac` added to the module imports.  
**Status:** Fixed in this PR.

---

## Medium Findings

### M-1 — Dockerfile Runs as Root (FIXED)

**File/Location:** `Dockerfile`  
**Risk:** No `USER` directive was present; the application ran as root inside the container. If any dependency or request-handling code is exploited, the attacker gains root within the container, maximising the blast radius of any container-escape path or volume-mounted data exfiltration.  
**Remediation:** Added a system user/group `clearview` created at image-build time and a `USER clearview` directive before `CMD`. The `/data` volume directory is `chown`ed to `clearview` at build time.  
**Status:** Fixed in this PR.

### M-2 — `/metrics` Prometheus Endpoint Unauthenticated (DEFERRED)

**File/Location:** `app/main.py:2538` (`metrics`)  
**Risk:** The Prometheus-format endpoint exposes per-provider request counts, cumulative native cost in USD, plan-equivalent baseline cost, total output tokens, and overall drift percentage — all operational and financial data. No authentication is required. On a shared network any host can scrape this data.  
**Proposed design:** Gate `/metrics` behind the existing `_admin_auth` guard, or provide a separate `CLEARVIEW_METRICS_TOKEN` env var so the Prometheus scraper can authenticate independently without sharing the admin token. Operators who already have scraper configurations will need to update them, making this a minor breaking change.  
**Status:** Deferred — behavior-changing; needs operator coordination.

### M-3 — Transitive `filelock` CVEs (FIXED in pyproject.toml)

**File/Location:** `pyproject.toml` (transitive via `litellm`, `huggingface_hub`)  
**Risk:** `filelock 3.32.7` carries 13 CVEs (CVE-2026-112907 through CVE-2026-112940). The most impactful ones involve `ReadWriteLock`/`AsyncReadWriteLock` (SQLite-backed) incorrectly resolving lock paths (directory traversal) and `SoftFileLock` failing to clean up stale locks. Most Windows-specific variants have limited impact on Linux containers. The path-traversal issue (CVE-2026-112936, fix 4.0.9) is relevant on Linux.  
**Fix versions:** `>=4.2.1` resolves all 13 CVEs.  
**Remediation:** Added `"filelock>=4.2.1"` as a direct constraint in `pyproject.toml` so pip resolves the safe version even if `litellm`'s `filelock` lower-bound is older.  
**Status:** Fixed in this PR (constraint added; existing `.venv` keeps older version until reinstall).

---

## Low Findings

### L-1 — `/v1/models` Endpoint Unauthenticated (DEFERRED)

**File/Location:** `app/main.py:364` (`list_models`)  
**Risk:** Returns the list of configured virtual and underlying models (provider + model name) without any authentication. Leaks the operator's provider configuration (e.g. which Anthropic/OpenAI/Gemini model IDs are in use).  
**Proposed design:** Gate behind `_client_key_allowed` check (same as `/v1/chat/completions`) when `CLEARVIEW_CLIENT_KEYS` is set. OpenAI-compatible clients probe `/v1/models` before sending requests; adding auth here requires clients to be pre-configured with a key. Alternatively, return only the virtual model names (clearview-auto, etc.) to unauthenticated callers.  
**Status:** Deferred — could break anonymous IDE integrations.

### L-2 — `pip` and `setuptools` Build Tool CVEs (DEFERRED)

**Packages:** `pip 24.0` (7 CVEs, fix `>=26.2.2`), `setuptools 79.0.1` (2 CVEs, fix `>=84.1.0`)  
**Risk:** These are build-time tools. In a running container `pip` and `setuptools` are not used unless the container re-installs packages at runtime. The most severe pip CVEs involve tar/ZIP path traversal during `pip install` (PYSEC-2026-1795, -1796), which would only be triggered if the container runs `pip install` on untrusted packages.  
**Remediation:** Update the base image or add `RUN pip install --upgrade pip setuptools` to the Dockerfile. Deferred because this is a transitive concern not exposed in normal operation.  
**Status:** Deferred.

---

## Carried-over Deferred Items

None (first audit).

---

## Positive Observations

- Token generation (`teams.create`, `chat_store`) correctly uses `secrets.token_hex()` — no `random` or `uuid`-based tokens.
- All `/admin/*` routes call `_admin_auth` consistently.
- Chat endpoints uniformly require `_require_chat_team` (cookie + team token).
- SQL queries in `main.py` use parameterised `?` placeholders throughout; no f-string interpolation of user input into SQL was found.
- No tracked secrets (`.env`, `.pem`, live API key patterns `sk-`, `AKIA`, `ghp_`) found in the repository.
- No `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, or unsafe `yaml.load` found in `app/`.
- Subprocess usage (`create_subprocess_exec` in CLI provider adapters) passes arguments as lists, not shell strings.
- `docker-compose.yml` passes API keys via environment variables, not hardcoded in the file.

---

## Test Suite

359 tests, 0 failures, 3 deprecation warnings (unrelated to this audit). All tests pass after applying fixes.
