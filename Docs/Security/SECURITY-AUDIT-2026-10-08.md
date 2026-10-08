# ClearView Security Audit — 2026-10-08

**Auditor:** Automated daily audit (Claude Code)
**Branch audited:** `main` @ `e8ad2e3`
**Previous report:** None (first run)

---

## Severity Summary

| Severity | New findings | Fixed this run | Deferred |
|----------|-------------|----------------|---------|
| High     | 1           | 1              | 0       |
| Medium   | 2           | 1              | 1       |
| Low      | 2           | 0              | 2       |

---

## High Findings

### H-1 — Non-constant-time admin token comparison (FIXED)

**File/location:** `app/main.py:301` (`_admin_auth`)
**Risk:** Timing-attack against `CLEARVIEW_ADMIN_TOKEN`. A string `!=` comparison
short-circuits on the first differing byte, allowing a remote attacker on a
low-latency network to brute-force the token character-by-character via
response-time differences.
**Remediation:** Replaced `if token != expected:` with
`if not secrets.compare_digest(token, expected):`. The `secrets` module is now
imported at the top of `app/main.py`.
**Status: FIXED** (commit on this branch)

---

## Medium Findings

### M-1 — Dockerfile runs as root (FIXED)

**File/location:** `Dockerfile`
**Risk:** Container process runs as `root` (UID 0). A path-traversal or
deserialization bug in FastAPI/litellm could allow full host-level escape via
Docker socket mounts or other container-escape techniques. Defense-in-depth
requires a non-root process UID.
**Remediation:** Added `RUN useradd -r -u 1001 -s /bin/false clearview` and
`mkdir -p /data && chown clearview /data`, then `USER clearview` before `CMD`.
The `/data` volume directory is pre-created and owned by the service user so
SQLite writes succeed at runtime.
**Status: FIXED** (commit on this branch)

### M-2 — Unauthenticated `/metrics` endpoint exposes operational data (DEFERRED)

**File/location:** `app/main.py:2538` (`metrics`)
**Risk:** The Prometheus metrics endpoint (`/metrics`) is open to any caller
without bearer-token or network authentication. It reveals: total requests by
tier/provider/status/team_id, cumulative spend in USD, output-token counts, and
per-team identifiers in label values. An external observer scraping this endpoint
learns the gateway's usage patterns, team identifiers, and cost information.
**Proposed design:** Gate the endpoint with `_admin_auth(request)` — add
`request: Request` parameter and call it. Alternatively, mount the metrics on a
separate internal-only port (a common Prometheus pattern). Because adding the
`request` parameter changes the endpoint signature and may affect existing
Prometheus scrape configurations, this is deferred for operator review.
**Status: DEFERRED** — low operational risk when ClearView is deployed on a
private network, but should be addressed before any public-facing deployment.

---

## Low Findings

### L-1 — pip and setuptools CVEs in build toolchain (DEFERRED)

**Tool:** `pip-audit`
**Affected packages:** `pip 24.0` (PYSEC-2026-1795/1796/196/2875/2876/3721, fix: upgrade to ≥26.2),
`setuptools 79.0.1` (PYSEC-2026-3447, fix: upgrade to ≥83.0.0)
**Risk:** These CVEs affect the pip package manager and setuptools, which are
build/install-time tools, not runtime application dependencies. They are not
present in the deployed container (which only has the installed application
packages). The risk is confined to the CI build environment.
**Status: DEFERRED** — upgrade `pip` and `setuptools` in the CI/build
environment. No application code change required.

### L-2 — SQL f-string interpolation of dynamically-built WHERE clauses (DEFERRED)

**File/location:** `app/main.py` (multiple admin endpoints), `app/telemetry.py:268`,
`app/teams.py:218`
**Risk:** Several query-building functions use f-strings to interpolate the
`where` variable into SQL. However, in all observed cases, the `where` string
is assembled exclusively from **hardcoded column names and SQL operators** (e.g.
`"WHERE ts >= ?"`, `"AND session_id = ?"`), and all user-controlled values are
passed through SQLite's parameterized `?` placeholders. No direct SQL injection
path was found. The pattern is nevertheless fragile: a future change that
accidentally interpolates a user-supplied string into the `where` fragment would
introduce injection without an obvious code smell.
**Proposed design:** Refactor to use a query-builder helper that never accepts
raw strings in the WHERE fragment, or add a pre-commit lint rule. Deferred as
a code-quality improvement, not an active vulnerability.
**Status: DEFERRED** — no active injection path found; carry as a hardening item.

---

## Carried-Over Deferred Items

None (first audit run).

---

## Minimum Sweep Results

| Check | Result |
|-------|--------|
| Every `@app.get/post/delete` route calls auth guard OR is documented as open | **PASS** — open routes: `/v1/models` (model listing), `/health`, `/feedback` (user-side rating), `/chat` (login page), `/chat/login`, `/chat/logout`, `/metrics` (flagged M-2 above) |
| `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, unsafe `yaml.load` | **PASS** — none found. `create_subprocess_exec` (not `shell=True`) used in provider CLIs. |
| SQL parameterized — no f-string/concat with non-whitelisted values | **PASS** (with L-2 caveat above — no active injection path) |
| No tracked secrets (`.env`, `.key`, `.pem`; `sk-`, `AKIA`, `ghp_` patterns) | **PASS** — no secrets in tracked files; `.env.example` and `clearview-client.env.example` contain only placeholder strings |
| Secret comparisons use `secrets.compare_digest` | **PASS after fix** — H-1 fixed this run |
| Token generation uses `secrets` module | **PASS** — `teams.py:117` uses `secrets.token_hex(16)`, `chat.py:62` uses `secrets.token_hex(8)` |
| Dockerfile has non-root `USER` | **PASS after fix** — M-1 fixed this run |
| docker-compose does not publish unauthenticated services beyond loopback | **CONDITIONAL PASS** — `clearview` service publishes `8000:8000` (all interfaces by default); acceptable for a local gateway, but production deployments should bind to `127.0.0.1:8000:8000`. `ollama` service publishes `11434:11434` only under `--profile local`. |
| pip-audit CVEs | **FAIL (L-1)** — build-tool CVEs in pip/setuptools; no application-code CVEs |

---

## Positive Observations

- Team tokens are generated with `secrets.token_hex(16)` (cryptographically random, 128 bits).
- The team token list endpoint (`GET /admin/teams`) redacts tokens to a short prefix, preventing accidental exposure in operator logs.
- Budget enforcement guards exist at both per-team and global levels.
- The cookie set by `/chat/login` is `httponly=True, samesite="lax"`, correctly preventing JS-based theft and CSRF for state-mutating requests.
- The shadow-judge and provider-learning paths are entirely async and fire-and-forget — no user-facing latency impact from observability features.
- No hardcoded credentials or API keys found in tracked files.

---

## Remediation Status

| ID  | Severity | Description                              | Status  |
|-----|----------|------------------------------------------|---------|
| H-1 | High     | Non-constant-time admin token compare    | FIXED   |
| M-1 | Medium   | Dockerfile runs as root                  | FIXED   |
| M-2 | Medium   | Unauthenticated `/metrics` endpoint      | DEFERRED |
| L-1 | Low      | pip/setuptools CVEs in build toolchain   | DEFERRED |
| L-2 | Low      | SQL f-string WHERE assembly pattern      | DEFERRED |
