# Security Audit — 2026-11-08

**Auditor:** Automated daily audit (Claude Sonnet 4.6)
**Branch audited:** `main` (HEAD `e8ad2e3`)
**Audit branch:** `security/audit-2026-11-08`
**Prior reports:** None (first audit)

---

## Severity Summary

| Severity | New | Fixed in this run | Deferred |
|----------|-----|-------------------|----------|
| High     | 0   | 0                 | 0        |
| Medium   | 2   | 1                 | 1        |
| Low      | 3   | 1                 | 2        |

---

## High Findings

None.

---

## Medium Findings

### M-1 — Non-constant-time admin token comparison [FIXED]

**File/Location:** `app/main.py:301` (`_admin_auth`)
**Risk:** Python's `!=` operator short-circuits on the first differing byte, allowing a timing-oracle attack against `CLEARVIEW_ADMIN_TOKEN`. An attacker who can send many authenticated requests and measure response latencies could iteratively recover the token byte-by-byte.
**Remediation:** Replaced `if token != expected:` with `if not secrets.compare_digest(token, expected):` (constant-time comparison). Added `import secrets` to the module imports.
**Status:** Fixed in this audit run. All 359 tests pass.

---

### M-2 — `/metrics` endpoint unauthenticated [DEFERRED]

**File/Location:** `app/main.py:2538` (`metrics` route)
**Risk:** The Prometheus `/metrics` endpoint is open to unauthenticated requests by default. It exposes `team_id` label values on `clearview_requests_total` counters, which leaks the opaque team token prefix pattern and usage volume per team. It also exposes provider names and cost totals.
**Remediation:** Add `_admin_auth(request)` to the metrics handler. Requires passing `Request` as a parameter (currently absent). Operators with Prometheus scrapers must add a `Bearer` header to their scrape config.
**Deferred reason:** Adding `_admin_auth` to `/metrics` is a behavior-changing fix — it would break existing Prometheus scrape jobs. Proposed design: add an opt-in `CLEARVIEW_METRICS_TOKEN` env var checked independently of `CLEARVIEW_ADMIN_TOKEN`, so scrape configs can use a separate read-only credential.

---

## Low Findings

### L-1 — Dockerfile runs container as root [FIXED]

**File/Location:** `Dockerfile` (no `USER` directive)
**Risk:** The container runs as `root` (UID 0). If the app is compromised via an exploitable vulnerability, the attacker has root within the container, which widens the blast radius (e.g., easier container escapes, unrestricted file writes).
**Remediation:** Added `adduser clearview` and `USER clearview` before the entrypoint. Created `/data` with correct ownership so the volume mount still works.
**Status:** Fixed in this audit run. All 359 tests pass.

---

### L-2 — Session cookie missing `Secure` flag [DEFERRED]

**File/Location:** `app/main.py:2651` (`chat_login`)
**Risk:** The `cv_session` cookie is set with `httponly=True, samesite="lax"` but no `secure=True`. Without `Secure`, browsers will transmit the cookie over plain HTTP, allowing network interception of team tokens.
**Remediation:** Add `secure=True` to the `set_cookie` call. The app should also enforce HTTPS or be deployed behind a TLS-terminating reverse proxy.
**Deferred reason:** Setting `secure=True` breaks the `/chat` UI over HTTP, which is the documented local-dev mode. Proposed design: read a `CLEARVIEW_SECURE_COOKIES=1` env var and conditionally set `secure=True`; default off for dev, documented as required for production.

---

### L-3 — Build-tool CVEs (pip / setuptools) [DEFERRED]

**Tool:** `pip-audit` on installed environment
**CVEs:**
- `pip 24.0`: PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-196, PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-3721 → fix: upgrade to `pip>=26.1.2`
- `setuptools 79.0.1`: PYSEC-2026-3447, GHSA-xmj3-9gf7-h52w → fix: upgrade to `setuptools>=84.1.0`
**Risk:** These are build-time tools, not runtime application dependencies. No runtime attack surface. Low practical impact, but should be updated in CI/developer environments.
**Deferred reason:** Upgrading pip/setuptools is a dev-environment change, not a production code change. Track in `pyproject.toml` as a `build-system.requires` minimum version and in CI baseline image.

---

## Carried-Over Deferred Items

None (first audit).

---

## Minimum Sweep Results

| Check | Result |
|-------|--------|
| All `@app.*` routes call `_admin_auth`, `_client_key_allowed`, `_resolve_team`, or `_require_chat_team`, or have a documented reason to be open | Pass — `/v1/models`, `/health`, `/feedback` are intentionally open (OpenAI-compat model listing, health probe, client self-rating). `/metrics` is open — flagged as M-2. |
| `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, `yaml.load` | Pass — none found. `create_subprocess_exec` used in CLI adapters (safe; args are static, prompt goes via stdin). |
| SQL queries parameterized | Pass — all sqlite3 calls use `?` placeholders. The f-string in `teams.py:218` interpolates only hardcoded column-name strings (`"daily_usd_cap = ?"` etc.), not user input. |
| Tracked secrets (`.env`, `.key`, `.pem`, live API keys) | Pass — `git ls-files` returns no `.env`/`.key`/`.pem` files; no `sk-`, `AKIA`, or `ghp_` patterns in tracked files. |
| Secret comparisons use `secrets.compare_digest` | Fixed — M-1 above. |
| Token generation uses `secrets` module | Pass — `app/teams.py:117` and `app/chat.py:62` both use `secrets.token_hex`. |
| Dockerfile non-root `USER` | Fixed — L-1 above. |
| `docker-compose.yml` doesn't publish unauthenticated services beyond loopback | Note — port `8000:8000` binds to all interfaces. This is by design for a gateway product but means `CLEARVIEW_ADMIN_TOKEN` must be set when exposed on a shared network. |
| `pip-audit` CVEs | Flagged as L-3 (build tools only, no runtime deps affected). |

---

## Positive Observations

- All admin and chat routes consistently apply the appropriate auth guard.
- Token generation (`cv_team_`, `cv_chat_`) uses `secrets.token_hex` throughout.
- Subprocess calls in CLI adapters use `create_subprocess_exec` (not `shell=True`); prompt text goes via stdin, eliminating shell injection risk.
- No secrets or credentials committed to the repository.
- Budget enforcement (per-team daily/monthly + global daily) is in place with documented soft-cap caveat.
- Cookie is HttpOnly, preventing JS theft of the session token.
- SQL is fully parameterized; no user-controlled data reaches query structure.

---

## Remediation Status

| ID  | Finding                                  | Status        |
|-----|------------------------------------------|---------------|
| M-1 | Non-constant-time admin token comparison | Fixed         |
| M-2 | `/metrics` unauthenticated               | Deferred      |
| L-1 | Dockerfile runs as root                  | Fixed         |
| L-2 | Session cookie missing `Secure` flag     | Deferred      |
| L-3 | Build-tool CVEs (pip/setuptools)         | Deferred      |
