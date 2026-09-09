# ClearView Security Audit — 2026-09-09

**Auditor:** Automated daily routine  
**Branch audited:** `main` (HEAD: `e8ad2e3`)  
**Remediation branch:** `security/audit-2026-09-09`  
**Previous report:** none (first audit)

---

## Severity Summary

| Severity | New | Fixed this run | Deferred |
|----------|-----|---------------|----------|
| High     | 0   | 0             | 0        |
| Medium   | 3   | 2             | 1        |
| Low      | 3   | 0             | 3        |
| **Total**| **6**| **2**        | **4**    |

---

## High Findings

_None._

---

## Medium Findings

### M-1 · Non-constant-time admin token comparison — **FIXED**

**File:** `app/main.py:301`  
**Risk:** `_admin_auth` compared the bearer token with `token != expected`, a Python string equality check that is not constant-time. An attacker with high-frequency access to the endpoint could use a timing oracle to recover the admin token one character at a time.  
**Remediation:** Replaced with `secrets.compare_digest(token, expected)`. The `secrets` module was added to the imports at line 11.  
**Status:** Fixed — tests pass (359/359).

---

### M-2 · Dockerfile runs as root — missing `USER` directive — **FIXED**

**File:** `Dockerfile`  
**Risk:** No `USER` instruction meant the application process ran as UID 0 (root) inside the container. A container escape or arbitrary-code execution vulnerability would grant an attacker root privileges on the host.  
**Remediation:** Added `RUN useradd -r -m -s /sbin/nologin appuser && mkdir -p /data && chown appuser /data` followed by `USER appuser` before the `VOLUME` and `CMD` directives.  
**Status:** Fixed — tests pass (359/359).

---

### M-3 · `/metrics` endpoint unauthenticated — **DEFERRED**

**File:** `app/main.py:2538`  
**Risk:** The Prometheus metrics endpoint (`GET /metrics`) exposes operational data — request counts broken down by tier, provider, status, and team ID; total native cost in USD; total plan-equivalent cost; total output tokens; and the savings drift percentage — without any authentication gate. An external observer can use this to enumerate team IDs, infer tier preferences and provider mix, and track cost trajectories.  
**Proposed design:** Add `request: Request` parameter to `metrics()` and call `_admin_auth(request)`. Since `_admin_auth` is a no-op when `CLEARVIEW_ADMIN_TOKEN` is unset, this only activates in deployments that already have an admin token configured. Prometheus scrape configs would need a `bearer_token` stanza added. This is a two-line code change but requires operator action to update scrapers; therefore deferred pending explicit sign-off.

---

## Low Findings

### L-1 · `/v1/models` endpoint unauthenticated — **DEFERRED (by design)**

**File:** `app/main.py:364`  
**Risk:** Exposes the list of virtual model IDs (`clearview-auto`, `clearview-frontier`, etc.) and the names of underlying provider models to any caller without authentication. Useful for model enumeration.  
**Note:** This is the OpenAI-compatible `/v1/models` discovery endpoint. IDE plugins (Continue, Cline, VS Code extension) query it unauthenticated during setup. Requiring auth here would break client auto-discovery. Accepted as intentional open endpoint.

---

### L-2 · `/feedback` endpoint accepts unauthenticated ratings — **DEFERRED**

**File:** `app/main.py:1693`  
**Risk:** `POST /feedback` accepts thumbs-up/down ratings keyed by `request_id` without any auth. A caller who observes or guesses a valid `request_id` (32 hex chars from `uuid4().hex`) can submit arbitrary feedback, biasing the provider-scoring corpus and potentially causing the router to prefer or avoid specific providers.  
**Note:** The code comment acknowledges this: "No admin auth — callers rate their own responses (request_id is the handle)." Adding auth would break anonymous clients (IDE plugins that don't present a team token). Recommended design: require `_client_key_allowed(request)` at minimum (already called on the main completions path), so only clients with a configured key can submit feedback. Defer pending sign-off.

---

### L-3 · Vulnerable pip and setuptools versions in dev environment — **DEFERRED**

**Tool:** `pip-audit`  
**Packages affected:**

| Package    | Installed | CVE IDs                                                                                      | Fix version |
|------------|-----------|----------------------------------------------------------------------------------------------|-------------|
| pip        | 24.0      | PYSEC-2026-196, PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-3721 | ≥ 26.1.2   |
| setuptools | 79.0.1    | PYSEC-2026-3447                                                                              | ≥ 83.0.0   |

**Risk:** These packages are the Python bootstrap tools in the `.venv` development environment and in the Docker base image (`python:3.12-slim`). They are not imported by the application at runtime, but an attacker able to run `pip install` inside the container (e.g., via a dependency confusion attack during a build) could exploit them.  
**Remediation:** Upgrade pip and setuptools in `pyproject.toml` optional dependencies (or the base Docker image's RUN step). Production impact is low since neither is imported by the FastAPI application.

---

## Carried-Over Deferred Findings

_None — this is the first audit._

---

## Positive Observations

- **Auth coverage complete:** Every `/admin/*` route calls `_admin_auth`. Every `/chat/*` route calls `_require_chat_team` (except `/chat` HTML page and `/chat/login`, both intentionally open). The main completions routes (`/v1/chat/completions`, `/v1/messages`, `/v1/responses`, Gemini shims) all call `_client_key_allowed` and `_resolve_team`.
- **No injection patterns found:** `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, and `yaml.load(` were absent across the entire `app/` tree.
- **No tracked secrets:** `git ls-files` shows only `.env.example` and `clearview-client.env.example` — both example files with no real credentials. No `sk-`, `AKIA`, or `ghp_` patterns found in tracked files.
- **Token generation uses `secrets` module:** `teams.py:117` uses `secrets.token_hex(16)` to generate team bearer tokens; `chat.py:62` uses `secrets.token_hex(8)` for conversation IDs.
- **SQL uses parameterized queries:** All `c.execute()` calls in `telemetry.py` and `teams.py` pass user-controlled values as bound parameters (`?` placeholders), not via f-string interpolation.
- **`/health` endpoint intentionally open:** Appropriate for liveness probes.

---

## Remediation Status

| Finding | Status |
|---------|--------|
| M-1: Non-constant-time token comparison | ✅ Fixed in `security/audit-2026-09-09` |
| M-2: Dockerfile runs as root | ✅ Fixed in `security/audit-2026-09-09` |
| M-3: `/metrics` unauthenticated | ⏳ Deferred — operator sign-off required |
| L-1: `/v1/models` unauthenticated | ⏳ Deferred — intentional by design |
| L-2: `/feedback` unauthenticated | ⏳ Deferred — requires API surface decision |
| L-3: pip/setuptools CVEs | ⏳ Deferred — dev tooling only, low runtime risk |
