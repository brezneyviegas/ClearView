# Security Audit — 2026-09-03

**Repository:** ClearView (Python/FastAPI LLM gateway)
**Auditor:** Automated daily routine
**Branch:** `security/audit-2026-09-03` off `main`
**Previous report:** None (first audit)

---

## Severity Summary

| Severity | New findings | Fixed in this PR | Deferred |
|----------|-------------|-------------------|----------|
| High     | 1           | 1                 | 0        |
| Medium   | 3           | 1                 | 2        |
| Low      | 1           | 0                 | 1        |
| Info     | 1           | 0                 | 1        |

---

## High Findings

### H-1 — Container runs as root (Dockerfile)

**Location:** `Dockerfile` (missing `USER` directive)
**Risk:** The container starts the FastAPI app as UID 0 (root). A remote code execution vulnerability in any dependency would give an attacker root inside the container, significantly easing privilege escalation or data exfiltration.
**Remediation:** Added a `clearview` system user, pre-created `/data` with correct ownership, and added `USER clearview` before `CMD`.
**Status: FIXED in this PR.**

---

## Medium Findings

### M-1 — Non-constant-time admin token comparison (`_admin_auth`)

**Location:** `app/main.py` — `_admin_auth()` (previously line ~301)
**Risk:** `if token != expected` uses Python's default string comparison, which short-circuits on the first differing byte. A remote attacker making many requests can mount a timing attack to recover `CLEARVIEW_ADMIN_TOKEN` one character at a time. All admin endpoints (`/admin/stats`, `/admin/teams`, `/admin/tune`, etc.) are protected only by this check.
**Remediation:** Replaced `token != expected` with `not secrets.compare_digest(token, expected)`. Added `import secrets` to imports.
**Status: FIXED in this PR.**

### M-2 — `/metrics` endpoint unauthenticated (Prometheus scrape)

**Location:** `app/main.py` — `GET /metrics` (line ~2539)
**Risk:** The Prometheus metrics endpoint is publicly accessible with no authentication. It exposes aggregate request counts by tier/provider/status/team_id, total USD spend, total output tokens, and drift percentage. An attacker can use this to profile traffic patterns, infer which providers are in use, and track budget consumption. In a multi-tenant deployment this leaks cross-tenant aggregate shapes.
**Remediation (deferred):** Adding `_admin_auth` here would break existing Prometheus scrape configurations. Proposed design: support an optional `CLEARVIEW_METRICS_TOKEN` env var; when set, require a `Bearer <token>` or basic-auth header for `/metrics`; when unset, behavior stays open (backward-compatible). Alternatively, bind the metrics endpoint to a separate port accessible only within the cluster.
**Status: DEFERRED — behavior-changing; needs operator sign-off.**

### M-3 — Session cookie missing `Secure` flag

**Location:** `app/main.py` — `chat_login()` (line ~2651)
**Risk:** The `cv_session` cookie (which carries a team bearer token) is set with `httponly=True, samesite="lax"` but without `secure=True`. In a deployment where the application is accessible over plain HTTP (not just HTTPS), the browser will transmit the session cookie unencrypted, exposing it to network eavesdroppers.
**Remediation (deferred):** Adding `secure=True` would break ClearView in standard local-dev HTTP mode. Proposed design: read `CLEARVIEW_SECURE_COOKIES=1` (default off) and set `secure=True` only when the env var is set; document in README that production deployments behind TLS should set this flag.
**Status: DEFERRED — behavior-changing in local-dev scenarios; needs operator sign-off.**

---

## Low Findings

### L-1 — pip and setuptools CVEs in build environment

**Location:** Build venv (`pip==24.0`, `setuptools==79.0.1`)
**Risk:** Nine CVEs detected by pip-audit, all in `pip` and `setuptools` (build-time tools, not runtime app dependencies). The most notable are:
- `pip==24.0`: PYSEC-2026-196, PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-3721. Fix version: `pip>=26.2.1`.
- `setuptools==79.0.1`: PYSEC-2026-3447 (sdist source-distribution build path traversal). Fix version: `setuptools>=83.0.0`.

These are exploitable only when building a wheel or sdist from a maliciously crafted package — not during normal app operation. Risk is limited to the CI/build environment.
**Remediation:** Upgrade pip and setuptools in the CI pipeline and Docker build (`pip install --upgrade pip setuptools` before `pip install .`), or pin them in a `requirements-build.txt`. Not fixed here to avoid touching the build pipeline without operator review.
**Status: DEFERRED — CI/build tooling change; limited runtime risk.**

---

## Informational

### I-1 — docker-compose does not set `CLEARVIEW_ADMIN_TOKEN`

**Location:** `docker-compose.yml`
**Risk:** The compose file passes no `CLEARVIEW_ADMIN_TOKEN`. By design this means all `/admin/*` endpoints are open (dev mode). Any operator who starts the stack with `docker compose up` without additionally setting the env var has unprotected admin endpoints.
**Note:** This is documented behavior (dev mode), not a code bug. The README should prominently note that production deployments must set `CLEARVIEW_ADMIN_TOKEN`.
**Status: INFORMATIONAL — no code change warranted; recommend docs update.**

---

## Audit Checklist Results

| Check | Result |
|-------|--------|
| Auth coverage: every admin route calls `_admin_auth` | PASS — all `/admin/*` routes verified |
| Auth coverage: chat routes call `_require_chat_team` | PASS — all `/chat/*` routes verified |
| Public routes documented open | PASS — `/v1/models`, `/health`, `/feedback`, `/chat/login`, `/chat/logout`, `/chat` (unauthenticated view) are intentionally open; `/metrics` flagged as M-2 |
| Injection: `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, `yaml.load` | PASS — none found |
| SQL parameterization | PASS — all queries use `?` placeholders with separate `params` tuples; f-strings only interpolate column-name literal strings, not user values |
| Tracked secrets: env/key/pem files in git | PASS — none tracked |
| Tracked secrets: live-looking keys in source | PASS — none found |
| Token generation uses `secrets` module | PASS — `secrets.token_hex()` used in `teams.py` and `chat.py` |
| `secrets.compare_digest` for token comparisons | FIXED — M-1 remediated in this PR |
| Dockerfile non-root `USER` | FIXED — H-1 remediated in this PR |
| docker-compose unauthenticated services beyond loopback | PASS — `clearview` is bound to `0.0.0.0:8000` (expected for compose); `CLEARVIEW_ADMIN_TOKEN` guidance logged as I-1 |
| pip-audit CVEs | LOW — pip/setuptools build-tool CVEs; no runtime app dependency CVEs found |

---

## Positive Observations

- All 30+ `/admin/*` endpoints consistently call `_admin_auth`, with no gaps.
- All `/chat/*` endpoints consistently call `_require_chat_team`.
- Token generation (`cv_team_*`, `cv_chat_*`) correctly uses `secrets.token_hex()`.
- No `eval`, `exec`, `pickle`, `os.system`, or `shell=True` usage anywhere in the codebase.
- SQL queries throughout use parameterized queries; no string-interpolation of user values into SQL.
- No real credentials or API keys are tracked in git.
- `cv_session` cookie is set with `httponly=True` and `samesite="lax"` (only the `secure` flag is missing).

---

## Test Suite

All 359 tests passed before and after fixes (`python -m pytest -q`). Neither H-1 nor M-1 remediations cause any regressions.

---

## Remediation Status

| ID  | Description                            | Status           | Branch                      |
|-----|----------------------------------------|------------------|-----------------------------|
| H-1 | Dockerfile runs as root                | Fixed            | `security/audit-2026-09-03` |
| M-1 | Non-constant-time admin token compare  | Fixed            | `security/audit-2026-09-03` |
| M-2 | `/metrics` unauthenticated             | Deferred         | Proposed design above       |
| M-3 | Session cookie missing `Secure` flag   | Deferred         | Proposed design above       |
| L-1 | pip/setuptools build-tool CVEs         | Deferred         | CI upgrade needed           |
| I-1 | docker-compose no `CLEARVIEW_ADMIN_TOKEN` | Informational | README update recommended   |
