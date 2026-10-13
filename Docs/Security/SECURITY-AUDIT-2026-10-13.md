# Security Audit — 2026-10-13

**Repository:** ClearView (Python/FastAPI LLM gateway)  
**Auditor:** Automated daily audit (Claude)  
**Audit date:** 2026-10-13  
**Branch audited:** `main` @ e8ad2e3  
**Prior reports:** None (first audit)

---

## Severity Summary

| Severity | New | Fixed this run | Deferred |
|----------|-----|----------------|----------|
| High     | 1   | 1              | 0        |
| Medium   | 3   | 1              | 2        |
| Low      | 2   | 1              | 1        |
| Info     | 0   | —              | —        |

---

## High Findings

### H-1 — Non-constant-time admin token comparison · **FIXED**

- **File / location:** `app/main.py:301` (`_admin_auth`)
- **Risk:** Python's `!=` string comparison short-circuits on the first mismatched byte, leaking whether each byte of the admin token is correct via response-time differences. Over many requests an attacker can enumerate `CLEARVIEW_ADMIN_TOKEN` one byte at a time (timing oracle). All admin endpoints — telemetry, team management, tune/revert — are gated by this function.
- **Remediation applied:** Replaced `if token != expected:` with `if not secrets.compare_digest(token, expected):` and added `import secrets` (stdlib). Constant-time comparison closes the timing channel.
- **Status:** Fixed in this PR; all 359 tests pass post-fix.

---

## Medium Findings

### M-1 — Dockerfile runs as root — **FIXED**

- **File / location:** `Dockerfile`
- **Risk:** No `USER` directive means the process runs as UID 0 inside the container. A container-escape or path-traversal exploit would land an attacker with root privileges rather than an unprivileged service account. The database file at `/data/clearview.db` and the policy file at `/app/policy.yaml` are writable without restriction.
- **Remediation applied:**
  - Added `RUN adduser --system --no-create-home --group clearview` to mint a dedicated non-root service user.
  - Added `mkdir -p /data && chown clearview:clearview /data` so the non-root user can write the database.
  - Added `USER clearview` before `CMD`.
  - Also added `--upgrade pip setuptools` to the pip install step to resolve the build-toolchain CVEs below (Low-1).
- **Status:** Fixed in this PR.

### M-2 — `/metrics` endpoint has no auth gate · **DEFERRED**

- **File / location:** `app/main.py:2538` (`metrics`)
- **Risk:** The Prometheus metrics endpoint exposes request counts, USD cost totals, provider names, tier breakdowns, and `team_id` labels to any unauthenticated caller on the network. On a shared or cloud-hosted instance this leaks business-sensitive spend and routing data.
- **Proposed design:** Add `_admin_auth(request)` before `snap = telemetry.metrics_snapshot()`. This changes the function signature from `async def metrics()` to `async def metrics(request: Request)`. Operators running Prometheus scrapers must supply a `Bearer` header (prometheus `bearer_token` or `basic_auth` scrape config). Alternatively, offer a `CLEARVIEW_METRICS_OPEN=1` escape hatch to restore the open default for trusted-network deployments.
- **Reason deferred:** Adding mandatory Bearer auth would break existing Prometheus scraper setups silently; the design decision (open vs. token-gated) belongs to the operator. No behavior-changing fix per audit rules.

### M-3 — `docker-compose.yml` binds port 8000 on all interfaces · **DEFERRED**

- **File / location:** `docker-compose.yml:9`
- **Risk:** `"8000:8000"` without a host prefix binds on `0.0.0.0`, exposing the gateway to every network interface on the host — including external NICs on cloud VMs. An unprotected `CLEARVIEW_ADMIN_TOKEN`-less instance is then reachable by anyone.
- **Proposed design:** Change to `"127.0.0.1:8000:8000"` for local-dev default. Provide a `CLEARVIEW_BIND_HOST` variable or a separate compose override file for deployments that genuinely need LAN/WAN access.
- **Reason deferred:** Changing the bind address is a behavior-changing deployment decision. Operators with existing network setups might rely on the current binding. Documented here for awareness.

---

## Low Findings

### L-1 — Build-toolchain CVEs (pip, setuptools) · **PARTIALLY FIXED**

- **Tool:** `pip-audit` against the project venv
- **Packages affected:**
  - `pip 24.0` — CVEs: PYSEC-2026-196, PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-3721. Fix version: `pip >= 26.2`.
  - `setuptools 79.0.1` — CVE: PYSEC-2026-3447. Fix version: `setuptools >= 83.0.0`.
- **Risk:** pip and setuptools are not runtime dependencies; they are used only at image-build time. Exploitation would require a malicious package in the build environment, not a live request to the gateway. Impact is low for a production image that is built and sealed.
- **Remediation applied:** Added `RUN pip install --no-cache-dir --upgrade pip setuptools` before the project install in the Dockerfile; the build image will now pull current versions.
- **Note:** These packages are not in `pyproject.toml` and do not appear in the deployed runtime. The fix is in the Docker build layer only.

### L-2 — Missing Secure flag on `cv_session` cookie · **DEFERRED**

- **File / location:** `app/main.py:2651` (`chat_login`, `resp.set_cookie`)
- **Risk:** The session cookie is set with `httponly=True, samesite="lax"` but lacks `secure=True`. Over a non-HTTPS connection the cookie is transmitted in plaintext, allowing interception of team session tokens.
- **Proposed design:** Add `secure=True` to `resp.set_cookie(...)`. Accept `CLEARVIEW_COOKIE_SECURE=0` as an escape hatch for HTTP-only local dev (same pattern as FastAPI's `SessionMiddleware`).
- **Reason deferred:** Setting `secure=True` breaks the `/chat` UI for operators running without TLS (common for local dev and Tailscale setups). The right fix requires a runtime flag rather than a constant change.

---

## Deferred Items (carried from prior audits)

None — this is the first audit.

---

## Route Auth Coverage

All `@app.get|post|delete|patch` routes were checked against the auth-gate requirement:

| Route pattern | Auth function | Notes |
|---|---|---|
| `GET /v1/models` | none | Intentionally open — IDE client model enumeration |
| `POST /v1/chat/completions` | `_client_key_allowed` + `_resolve_team` | Gated by `CLEARVIEW_CLIENT_KEYS` when set |
| `POST /v1/messages`, `/v1/responses`, `/v1[beta]/models/…` | via `_handle_chat_completions` | Same as above |
| `POST /feedback` | none | Intentional — client-side rating on own request IDs |
| `GET /health` | none | Standard health probe |
| `GET /metrics` | **none** | ⚠ M-2 above |
| `GET /chat` | none | HTML page (content differs for logged-in vs not) |
| `POST /chat/login`, `POST /chat/logout` | none | Login/logout flows |
| All `GET|POST|PATCH|DELETE /admin/*` | `_admin_auth` | ✓ |
| `GET /chat/model_options`, `/chat/me`, `/chat/conversations*` | `_require_chat_team` | ✓ |

---

## Minimum-Sweep Results

| Check | Result |
|---|---|
| All admin routes call `_admin_auth` / `_resolve_team` / `_require_chat_team` | ✓ (except `/metrics` — M-2) |
| `shell=True` / `os.system` | None found; subprocess calls use `create_subprocess_exec` (safe) |
| `eval(` / `exec(` | None found |
| `pickle` | None found |
| `yaml.load` (unsafe) | None found; config uses `yaml.safe_load` |
| SQL parameterization | ✓ All f-string WHERE clauses interpolate only hardcoded fragments; user values always bound via `?` parameters |
| Tracked secrets (`.env`, `.pem`, live keys) | None found |
| `secrets.compare_digest` for token comparison | ✓ after H-1 fix; `_client_key_allowed` uses set membership which is acceptable for client keys |
| `secrets` module for token generation | ✓ `teams.create` uses `secrets.token_hex(16)` |
| Dockerfile non-root USER | ✓ after M-1 fix |
| docker-compose unauthenticated service beyond loopback | ⚠ M-3 (deferred) |
| pip-audit CVEs | pip and setuptools build-toolchain CVEs — L-1, partially fixed in Dockerfile |

---

## Positive Observations

- All database queries use parameterized SQL (`?` placeholders). No f-string interpolation of user-supplied values was found.
- Team tokens are generated with `secrets.token_hex(16)` (cryptographically strong 128-bit entropy).
- No hardcoded secrets, API keys, or `.env` files found in the tracked tree.
- Provider CLI subprocess calls use `asyncio.create_subprocess_exec` (not shell) — no shell injection surface.
- YAML config loading uses `yaml.safe_load` — no arbitrary object deserialization.
- The cookie is `httponly=True, samesite="lax"` — baseline XSS and CSRF protections are present.
- Team token lookup is a DB query by exact primary key match rather than an in-process comparison, limiting timing oracle risk for client tokens.

---

## Remediation Status

| ID | Finding | Status |
|----|---------|--------|
| H-1 | Non-constant-time admin token compare | **Fixed** — `secrets.compare_digest` |
| M-1 | Dockerfile runs as root | **Fixed** — non-root `clearview` user |
| M-2 | `/metrics` no auth gate | **Deferred** — design decision needed |
| M-3 | docker-compose binds on 0.0.0.0 | **Deferred** — deployment config decision |
| L-1 | pip/setuptools build CVEs | **Fixed** in Dockerfile build step |
| L-2 | `cv_session` cookie missing Secure flag | **Deferred** — requires runtime flag |
