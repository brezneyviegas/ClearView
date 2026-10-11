# ClearView Security Audit — 2026-10-11

**Audit date:** 2026-10-11  
**Branch at audit time:** `main` (HEAD: `e8ad2e3`)  
**Previous report:** None (first audit)  
**Auditor:** Automated daily audit routine

---

## Severity Summary

| Severity | New | Fixed this run | Deferred |
|----------|-----|----------------|----------|
| High     | 1   | 1              | 0        |
| Medium   | 2   | 2              | 0        |
| Low      | 2   | 0              | 2        |

---

## High Findings

### H-1 — Dockerfile runs container as root [FIXED]

**File/location:** `Dockerfile`  
**Risk:** The image had no `USER` directive, so the application process runs as root inside the container. A successful exploitation of any vulnerability in the app or its dependencies grants full root access to the container filesystem, secrets mounted via volumes, and potentially the host (in misconfigured runtimes).  
**Remediation applied:** Added a dedicated `clearview` user (UID 1000) with `adduser`, `mkdir -p /data && chown clearview /data`, and `USER clearview` before the `VOLUME` and `CMD` directives.  
**Status:** ✅ Fixed in this run — `Dockerfile` updated on branch `security/audit-2026-10-11`.

---

## Medium Findings

### M-1 — Non-constant-time admin token comparison [FIXED]

**File/location:** `app/main.py:302` — `_admin_auth()`  
**Risk:** `if token != expected:` uses Python's built-in string `!=`, which short-circuits on the first differing byte. An adversary making repeated requests to any admin endpoint could perform a timing attack to enumerate `CLEARVIEW_ADMIN_TOKEN` character by character when the gateway is reachable over a low-jitter network link.  
**Remediation applied:** Replaced `token != expected` with `not secrets.compare_digest(token, expected)`. Added `import secrets` to module imports.  
**Status:** ✅ Fixed in this run.

### M-2 — Non-constant-time client key comparison [FIXED]

**File/location:** `app/main.py:325` — `_client_key_allowed()`  
**Risk:** `if cand in allowed:` performs a set membership check, whose underlying per-element `==` comparison short-circuits on the first differing character. An adversary with knowledge of the key prefix could distinguish a near-match from a miss by latency.  
**Remediation applied:** Replaced `if cand in allowed:` with `if cand and any([secrets.compare_digest(cand, k) for k in allowed]):`. The list comprehension (not generator) ensures all keys are always compared before `any()` evaluates, preventing early exit on the first true element.  
**Status:** ✅ Fixed in this run.

---

## Low Findings

### L-1 — `/metrics` endpoint unauthenticated [DEFERRED]

**File/location:** `app/main.py:2539` — `metrics()`  
**Risk:** The Prometheus `/metrics` endpoint returns request counts, token totals, cost sums, and drift percentages labelled by tier, provider, and team_id — even when `CLEARVIEW_ADMIN_TOKEN` is set and all other admin routes are locked down. Any network-reachable host can read this telemetry without credentials.  
**Remediation:** Would require adding `_admin_auth(request)` (or a separate `CLEARVIEW_METRICS_TOKEN`) to the handler. Deferred because Prometheus scrapers typically cannot authenticate with a Bearer token without additional configuration, so blindly gating the endpoint could break existing monitoring setups. **Proposed design:** Add an opt-in `CLEARVIEW_METRICS_AUTH=1` env var that enables auth on `/metrics`; when unset, behavior is unchanged.

### L-2 — pip/setuptools CVEs in image build toolchain [DEFERRED]

**Tool:** `pip 24.0`, `setuptools 79.0.1` (found by `pip-audit`)  
**CVEs:** PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-196, PYSEC-2026-3721 (fix: pip ≥26.2.0); PYSEC-2026-3447 (fix: setuptools ≥83.0.0)  
**Risk:** These vulnerabilities are in the build toolchain (`pip`/`setuptools`), not in any runtime dependency. They are relevant if the Docker image is rebuilt on an adversary-influenced network or with adversary-controlled package indexes. Not exploitable via the running application.  
**Remediation:** Add `RUN pip install --upgrade pip setuptools` to the Dockerfile after the base image pull. Deferred to avoid unverified dependency changes; recommend upgrading as part of a normal dependency maintenance cycle.

---

## Carried-Over Deferred Items

None — this is the first audit.

---

## Positive Observations

- **Token generation is correct:** `teams.create()` uses `secrets.token_hex(16)` — 32 hex characters of CSPRNG entropy.
- **SQL is fully parameterized:** All SQLite queries in `app/main.py`, `app/teams.py`, and `app/telemetry.py` use `?` placeholders; no f-string interpolation of user-controlled values into query strings.
- **No tracked secrets:** `git ls-files` shows only `.env.example` and `clearview-client.env.example`; no `.env`, `.key`, or `.pem` files are tracked. No live-looking API keys (`sk-`, `AKIA`, `ghp_`) found in tracked files.
- **No shell injection vectors:** `app/providers/*.py` use `asyncio.create_subprocess_exec` (argument list, not shell string). No `os.system`, `shell=True`, `eval(`, `exec(`, `pickle`, or `yaml.load(` calls found.
- **Admin endpoints consistently protected:** All `/admin/*` routes call `_admin_auth(request)`. All `/chat/*` routes (except login/logout/root) call `_require_chat_team(request)`.
- **Health endpoint intentionally open:** `GET /health` has no auth — correct, as it is a liveness probe.
- **Feedback endpoint intentionally open:** `POST /feedback` is documented as caller-rated and request_id is the access handle — correct for its use case.
- **docker-compose publishes no unauthenticated internal services:** `ollama` is profile-gated (`local`); the main service publishes port 8000 which is the app's normal public port.

---

## Remediation Status

| ID  | Finding                          | Fixed | Branch                     |
|-----|----------------------------------|-------|----------------------------|
| H-1 | Dockerfile runs as root          | ✅    | security/audit-2026-10-11  |
| M-1 | Non-constant-time admin token    | ✅    | security/audit-2026-10-11  |
| M-2 | Non-constant-time client key     | ✅    | security/audit-2026-10-11  |
| L-1 | `/metrics` unauthenticated       | ⏳    | Deferred — design needed   |
| L-2 | pip/setuptools CVEs              | ⏳    | Deferred — maintenance     |
