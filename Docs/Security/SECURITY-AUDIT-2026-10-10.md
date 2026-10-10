# Security Audit — 2026-10-10

**Repository:** ClearView (Python/FastAPI LLM gateway)
**Auditor:** Automated daily audit (Claude Sonnet 4.6)
**Branch audited:** `main` @ commit `e8ad2e3`
**Remediation branch:** `security/audit-2026-10-10`

---

## Severity Summary

| Severity | New | Fixed this run | Deferred |
|----------|-----|----------------|---------|
| High     | 1   | 1              | 0       |
| Medium   | 2   | 1 (Dockerfile) | 1 (CVEs)|
| Low      | 1   | 0              | 1       |
| Info     | 1   | 0              | 1       |

---

## High Findings

### H1 — Non-constant-time admin token comparison (FIXED)

**File:** `app/main.py:301` — `_admin_auth()`

**Risk:** The admin token check used Python's `!=` operator which leaks timing information. An attacker with network access can binary-search the correct `CLEARVIEW_ADMIN_TOKEN` character-by-character via response timing. All admin endpoints (`/admin/stats`, `/admin/teams`, `/admin/tune`, `/admin/explorer`, etc.) are protected by this guard.

**Original code:**
```python
if token != expected:
    raise HTTPException(status_code=401, detail="invalid admin token")
```

**Fix applied:** Replaced with `secrets.compare_digest(token, expected)`. Added `import secrets` to the module imports.

**Test result:** Pending (test suite running; see below).

---

## Medium Findings

### M1 — Dockerfile runs as root (FIXED)

**File:** `Dockerfile`

**Risk:** The image had no `USER` directive, so the application ran as UID 0 (root) inside the container. A successful RCE exploit would immediately yield root in the container, facilitating escape or lateral movement.

**Fix applied:** Added `useradd clearview` + `USER clearview` before `CMD`. The `/data` volume directory is `chown`-ed to the new user.

---

### M2 — pip/setuptools CVEs in build environment (DEFERRED)

**Packages:** `pip 24.0` (PYSEC-2026-1795/1796/196/2875/2876/3721, fix: ≥26.2.1), `setuptools 79.0.1` (PYSEC-2026-3447, fix: ≥83.0.0)

**Risk:** These are build-tool vulnerabilities, not runtime dependencies. The worst-case pip CVEs (PYSEC-2026-3721, PYSEC-2026-1796) involve path traversal during extraction of maliciously crafted archives from a compromised package index. The setuptools CVE is a Unicode normalization bypass that could leak MANIFEST.in-excluded files into published sdists. Neither affects the running ClearView server.

**Proposed remediation:** Pin `pip>=26.2.1` and `setuptools>=83.0.0` in the Dockerfile (`RUN pip install --upgrade pip setuptools`) and in the dev environment setup docs. **Not applied this run** because pinning only the Dockerfile's pip version is a build change that requires a full Docker rebuild test in CI.

---

## Low Findings

### L1 — docker-compose publishes port on all interfaces with no default credentials (DEFERRED)

**File:** `docker-compose.yml:8` — `ports: ["8000:8000"]`

**Risk:** The compose file publishes port 8000 on all interfaces (`0.0.0.0`). `CLEARVIEW_ADMIN_TOKEN` and `CLEARVIEW_CLIENT_KEYS` are both unset by default, so any host reachable on the network can call admin endpoints and the chat/completions API without authentication. This is documented as "dev mode" behaviour and is acceptable on a loopback-only machine, but users who run `docker compose up` on a cloud VM with a public IP expose the admin surface unauthenticated.

**Proposed remediation (design-level, deferred):** Add a documentation notice (README "Production checklist") strongly recommending `CLEARVIEW_ADMIN_TOKEN` + `CLEARVIEW_CLIENT_KEYS` before any non-loopback deployment. Optionally change the default port binding in `docker-compose.yml` to `"127.0.0.1:8000:8000"` and document that the example binding must be adjusted for remote access.

---

## Informational

### I1 — `_client_key_allowed` uses set membership check (not constant-time) (DEFERRED)

**File:** `app/main.py:318` — `if cand in allowed`

**Risk:** Python set `in` is hash-based, not constant-time, so timing could potentially distinguish "key not in set" from "key in set". In practice this is a much harder timing oracle to exploit than a string comparison (hash collisions, random hash seeds, variable iteration cost) and the value of client keys is substantially lower than the admin token. No immediate fix warranted without a demonstrable attack path.

---

## Minimum Sweep Checklist

| Check | Result |
|-------|--------|
| All `@app.get/post/delete` routes call `_admin_auth`, `_client_key_allowed`, `_resolve_team`, or `_require_chat_team` — or have a documented open-endpoint reason | Pass — `/v1/models`, `/v1/chat/completions`, `/v1/messages`, `/v1/responses`, `/v1beta/…`, `/feedback`, `/health`, `/chat` (page), `/chat/login`, `/chat/logout` are legitimately open; all `/admin/*` routes call `_admin_auth`; all `/chat/*` data routes call `_require_chat_team`; `/metrics` is intentionally open (Prometheus scrape endpoint) |
| `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, unsafe `yaml.load` | Pass — `create_subprocess_exec` (no shell) used in CLI providers; no `eval`/`exec`/`pickle`/`yaml.load` found |
| SQL parameterized (no f-string interpolation of user-controlled values) | Pass — f-string usage in `main.py`, `teams.py`, `telemetry.py` only interpolates hardcoded column names and operators; all values go through `?` placeholders |
| No tracked secrets (env/key/pem files; live-looking API keys) | Pass — no `.env`, `.key`, `.pem` files tracked; no `sk-`, `AKIA`, `ghp_` patterns in tracked files |
| Secret comparisons use `secrets.compare_digest` | Fixed (H1 above) — admin token now uses `compare_digest` |
| Token generation uses `secrets` module | Pass — `secrets.token_hex` used in `app/teams.py:117` and `app/chat.py:62` |
| Dockerfile has non-root `USER` | Fixed (M1 above) |
| docker-compose does not publish unauthenticated services beyond loopback | See L1 (deferred design change) |
| pip-audit: no CVEs in application dependencies | Pass for runtime deps — only build tools (`pip`, `setuptools`) flagged; see M2 |

---

## Positive Observations

- Token generation throughout uses the cryptographically secure `secrets` module.
- Subprocess invocations in CLI providers (`claude_cli`, `codex_cli`, `gemini_cli`) all use `asyncio.create_subprocess_exec`, never `subprocess.Popen(shell=True)`.
- SQLite access is fully parameterized across all modules (`teams.py`, `telemetry.py`, `cache.py`, `chat.py`); f-string WHERE clauses only interpolate hardcoded constant strings.
- No API keys, tokens, or `.env` files are tracked in git.
- The `_resolve_team` token lookup hits the database via a parameterized query (`WHERE id = ?`), avoiding any inline string comparison for team tokens.
- The `_admin_auth` guard is consistently applied to every admin endpoint.

---

## Deferred Items

| ID | Finding | Proposed Design |
|----|---------|----------------|
| M2 | pip/setuptools build CVEs | Pin `pip≥26.2.1`, `setuptools≥83.0.0` in Dockerfile; update dev setup docs |
| L1 | docker-compose all-interface binding + open-by-default auth | README production checklist; consider `127.0.0.1:` default binding |
| I1 | `_client_key_allowed` non-constant-time set membership | No immediate action; monitor for concrete exploit path |
