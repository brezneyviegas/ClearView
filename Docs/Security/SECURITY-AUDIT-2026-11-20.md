# Security Audit — 2026-11-20

**Repository:** ClearView (Python/FastAPI LLM gateway)
**Auditor:** Automated daily audit (Claude)
**Date:** 2026-11-20
**Branch audited:** main (HEAD e8ad2e3)
**Audit branch:** security/audit-2026-11-20

---

## Severity Summary

| Severity | New | Fixed | Deferred | Carried Over |
|----------|-----|-------|----------|--------------|
| High     | 0   | 0     | 0        | 0            |
| Medium   | 2   | 1     | 1        | 0            |
| Low      | 2   | 1     | 1        | 0            |
| Info     | 1   | 0     | 1        | 0            |

---

## High Findings

None.

---

## Medium Findings

### M-1 — Non-constant-time admin token comparison [FIXED]

**File/Location:** `app/main.py:301` (`_admin_auth`)
**Risk:** Python's `!=` operator short-circuits on the first differing byte, allowing a timing side-channel attack. An attacker who can send many requests and measure response latency can recover the CLEARVIEW_ADMIN_TOKEN one character at a time, gaining full admin access (team management, policy tuning, telemetry).
**Remediation:** Replaced `token != expected` with `secrets.compare_digest(token, expected)` (constant-time comparison).
**Status:** Fixed in this PR.

---

### M-2 — Unauthenticated `/metrics` endpoint leaks team and usage telemetry [DEFERRED]

**File/Location:** `app/main.py:2538` (`metrics` route)
**Risk:** The Prometheus `/metrics` endpoint requires no authentication. It exposes `team_id` as a label in the `clearview_requests_total` counter, which reveals what teams exist and their call volumes. Combined with provider/tier labels it discloses the system's routing strategy to any network-adjacent party.
**Remediation:** Add `_admin_auth(request)` gating to the `/metrics` route, or publish metrics on a separate internal-only port (standard Prometheus practice). Note: existing Prometheus scrapers must supply the CLEARVIEW_ADMIN_TOKEN bearer header after this change.
**Deferred because:** This is a behavior-changing fix — existing scrapers will break without a coordinated config change. Needs operator decision on scraper reconfiguration before applying.

---

## Low Findings

### L-1 — Dockerfile runs application as root [FIXED]

**File/Location:** `Dockerfile` (no `USER` directive)
**Risk:** The container process runs as `root` (UID 0). If a path-traversal, SSRF, or code-execution vulnerability is exploited, the attacker has full container root privileges, making container escapes easier and maximising blast radius.
**Remediation:** Added `useradd -r -u 1001 clearview` and `USER clearview` before the entrypoint.
**Status:** Fixed in this PR.

---

### L-2 — pip / setuptools CVEs in build toolchain [DEFERRED]

**Tool:** `pip-audit` (run against installed venv)
**Packages:** `pip` 24.0 (fix: 26.1.2+), `setuptools` 79.0.1 (fix: 83.0.0+)
**CVEs:** PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-196, PYSEC-2026-3721 (pip); PYSEC-2026-3447, GHSA-xmj3-9gf7-h52w (setuptools)
**Risk:** These are build/packaging tools, not application runtime dependencies. They affect `pip install` and `setup.py`-based builds. The Docker image uses `pip install` at build time, so the Docker base image's pip version should also be updated.
**Remediation:** Pin `pip>=26.1.2` and `setuptools>=84.1.0` in the Dockerfile (`RUN pip install --upgrade pip setuptools`), and update the local dev environment.
**Deferred because:** No application runtime risk. Base image update requires a separate Docker build validation pass.

---

## Carried-Over Deferred Items

None — this is the first audit run for this repository.

---

## Positive Observations

1. **Token generation secure:** Team tokens use `secrets.token_hex(16)` (32 hex chars), which is cryptographically secure and correct for bearer tokens.
2. **SQL queries fully parameterised:** All SQLite queries use `?` placeholders. The f-string WHERE clause building in several admin routes is safe because only hardcoded column-name strings are interpolated (never user-controlled values).
3. **No tracked secrets:** `git ls-files` shows no `.env`, `.pem`, or `.key` files tracked in the repository.
4. **No hardcoded API keys:** Grep for `sk-`, `AKIA`, `ghp_` patterns found nothing in tracked source files.
5. **No dangerous exec primitives:** No `shell=True`, `os.system()`, `eval()`, `exec()`, `pickle.loads()`, or unsafe `yaml.load()` usage. Subprocess calls all use `create_subprocess_exec` (arg list, not shell string).
6. **Admin auth consistently applied:** All 15 `/admin/*` routes call `_admin_auth(request)` before proceeding.
7. **Chat routes gated by team auth:** All `/chat/*` routes (except the HTML page and logout) call `_require_chat_team(request)`, which validates the `cv_session` cookie or Bearer token.
8. **Session cookie hardened:** `cv_session` cookie set with `httponly=True, samesite="lax"`, mitigating XSS-based session theft and CSRF.
9. **No application dependency CVEs:** `pip-audit` found zero CVEs in the app's runtime dependencies (fastapi, uvicorn, litellm, pydantic, pyyaml, jinja2, httpx).
10. **Budget enforcement present:** Per-team daily/monthly caps with hard-reject enforced before upstream calls.

---

## Remediation Status

| ID  | Finding                              | Status  | PR          |
|-----|--------------------------------------|---------|-------------|
| M-1 | Non-constant-time admin token compare | Fixed   | This PR     |
| M-2 | Unauthenticated /metrics endpoint    | Deferred | See above  |
| L-1 | Dockerfile runs as root              | Fixed   | This PR     |
| L-2 | pip/setuptools CVEs in build chain   | Deferred | See above  |

---

## Test Results

`python -m pytest -q` — **359 passed, 16 warnings** in 174s. All tests pass after fixes.
