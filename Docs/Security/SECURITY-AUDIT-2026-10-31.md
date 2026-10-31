# Security Audit — 2026-10-31

**Auditor:** Automated daily audit (Claude Code)
**Branch audited:** `main` (HEAD `e8ad2e3`)
**Audit branch:** `security/audit-2026-10-31`
**Previous report:** _(none — first audit)_

---

## Severity Summary

| Severity | Total | Fixed | Deferred |
|----------|-------|-------|---------|
| High     | 1     | 1     | 0       |
| Medium   | 2     | 1     | 1       |
| Low      | 3     | 0     | 3       |

---

## High Findings

### H-1 — Timing side-channel in admin token comparison (FIXED)

**Location:** `app/main.py:301` (`_admin_auth`)
**Risk:** The admin bearer token was compared with `token != expected`, a Python string equality check that short-circuits on the first differing byte. An attacker able to make many rapid requests to any `/admin/*` endpoint could measure response-time variance and progressively reconstruct the token character by character (timing oracle). In a local or LAN deployment this is low-probability but not impossible; on internet-facing deployments it is a meaningful risk.
**Remediation:** Replaced with `secrets.compare_digest(token, expected)` (constant-time HMAC-style comparison). Also added `import secrets` to `app/main.py`.
**Status:** ✅ Fixed in this PR. All 359 tests pass.

---

## Medium Findings

### M-1 — Dockerfile runs container process as root (FIXED)

**Location:** `Dockerfile`
**Risk:** No `USER` directive was present, so the Python interpreter and the ClearView process ran as UID 0 inside the container. If the application was ever compromised (e.g. path traversal in template rendering, dependency RCE), the attacker would have root inside the container, making container-escape exploits and file-system damage significantly easier.
**Remediation:** Added a `useradd -r clearview` step and `USER clearview` directive. The `/data` volume directory is `chown`-ed to that user before the user switch so the DB write path still works.
**Status:** ✅ Fixed in this PR. All 359 tests pass.

### M-2 — Vulnerable transitive dependencies (filelock, pip, setuptools) — DEFERRED

**Location:** Python virtual environment (transitive deps of litellm)
**Details:**

| Package    | Installed | CVE(s)                          | Fix Version |
|------------|-----------|----------------------------------|-------------|
| filelock   | 3.32.7    | CVE-2026-112907 through -112940 (13 CVEs) | 4.2.1      |
| pip        | 24.0      | PYSEC-2026-1795/1796/196/2875/2876/3721 (6 CVEs) | 26.2.2    |
| setuptools | 79.0.1    | PYSEC-2026-3447, GHSA-xmj3-9gf7-h52w | 84.1.0    |

`pip` and `setuptools` are build-time tools; their CVEs do not affect the running application. `filelock` is a transitive dependency of `litellm`; upgrading it independently risks breaking litellm's pinned resolution. **Proposed remediation:** Pin `filelock>=4.2.1` as a direct dependency in `pyproject.toml` after confirming litellm compatibility in a dedicated test run. Defer until next dependency update cycle.
**Status:** ⏳ Deferred — no safe mechanical fix without broader dependency audit.

---

## Low Findings

### L-1 — `/metrics` Prometheus endpoint unauthenticated — DEFERRED

**Location:** `app/main.py:2538` (`@app.get("/metrics")`)
**Risk:** The Prometheus scrape endpoint is always open (no `_admin_auth` call). It exposes aggregate operational data: per-tier/provider/status/team_id call counts, total cost in USD, total tokens, and the current drift percentage. In a multi-tenant deployment with named team IDs, this leaks usage-pattern intelligence to any unauthenticated caller.
**Proposed remediation:** Call `_admin_auth(request)` in `metrics()`. Operators running open Prometheus scrapers (no bearer token configured) are unaffected because `_admin_auth` is a no-op when `CLEARVIEW_ADMIN_TOKEN` is unset.
**Status:** ⏳ Deferred — low-risk in local/dev deployments; propose gating behind `_admin_auth` in next sprint.

### L-2 — docker-compose exposes port on all network interfaces without auth warning — DEFERRED

**Location:** `docker-compose.yml:8` (`ports: - "8000:8000"`)
**Risk:** The compose file binds `0.0.0.0:8000`, not `127.0.0.1:8000`, making the service reachable on every network interface. When `CLEARVIEW_ADMIN_TOKEN` is not set (the default), all `/admin/*` endpoints are open to anyone on the same network. The `.env.example` documents `CLEARVIEW_ADMIN_TOKEN` but it is commented out, so new deployments default to open admin.
**Proposed remediation:** Change the compose port binding to `"127.0.0.1:8000:8000"` for a safer default, or add a comment in `docker-compose.yml` stating that `CLEARVIEW_ADMIN_TOKEN` must be set in `.env` before deploying on a shared network. Do not change without user sign-off (affects LAN deployments that rely on the current binding).
**Status:** ⏳ Deferred — design decision; needs operator input.

### L-3 — `random` module used for shadow sampling — DEFERRED (ACCEPTED)

**Location:** `app/main.py:1651,1672,1685` (`_auto_shadow_tier`, `_provider_shadow_alt`)
**Risk:** `random.random()` is used for statistical sampling of shadow routing. This is not a security-sensitive context (no security decisions depend on the sampling outcome), but using a non-CSPRNG for any sampling in a security product may raise audit flags.
**Proposed remediation:** Replace `random.random()` and `random.choice()` in these functions with `secrets.SystemRandom()` equivalents. Low priority.
**Status:** ⏳ Deferred — functionally acceptable; pure cosmetic improvement.

---

## Carried-Over Deferred Findings

_None — this is the first audit._

---

## Positive Observations

- **Team token generation** uses `secrets.token_hex(16)` (`app/teams.py:117`) — cryptographically secure.
- **Chat conversation ID generation** uses `secrets.token_hex(8)` (`app/chat.py:62`) — cryptographically secure.
- **No shell=True, os.system, eval, exec, pickle, or unsafe yaml.load** found in any tracked Python file.
- **All SQL queries** use parameterized `?` bindings; f-string SQL fragments contain only hardcoded column/table names and `?` placeholders — no user input is interpolated directly into SQL.
- **No live credentials tracked in git.** `.env.example` and `clearview-client.env.example` contain only empty/placeholder values. `git ls-files` shows no `.env`, key, or PEM files.
- **Subprocess calls** in CLI providers (`claude_cli`, `codex_cli`, `gemini_cli`) use `create_subprocess_exec` (not `shell=True`), preventing shell injection.
- **Team token Bearer comparison** in `_resolve_team` queries the DB by token; the DB lookup is parameterized. No string comparison of secrets outside `_admin_auth`.
- **Cookie auth** for `/chat` uses `httponly=True, samesite="lax"` — JS cannot read the session cookie and CSRF surface is reduced.
- **Admin endpoints** (`/admin/*`) are consistently gated with `_admin_auth`.
- **Test coverage:** 359 tests; all pass.

---

## Remediation Status

| Finding | Action           | Commit |
|---------|-----------------|--------|
| H-1     | Fixed           | This PR |
| M-1     | Fixed           | This PR |
| M-2     | Deferred        | — |
| L-1     | Deferred        | — |
| L-2     | Deferred        | — |
| L-3     | Deferred (accepted) | — |
