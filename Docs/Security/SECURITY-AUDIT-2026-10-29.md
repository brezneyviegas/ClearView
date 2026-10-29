# ClearView Security Audit — 2026-10-29

**Auditor:** Automated daily audit (Claude Code)
**Branch audited:** `main` (HEAD e8ad2e3)
**Audit branch:** `security/audit-2026-10-29`
**Previous report:** None (first run)

---

## Severity Summary

| Severity | New | Fixed this run | Deferred | Carried over |
|----------|-----|---------------|----------|-------------|
| High     | 0   | 0             | 0        | 0           |
| Medium   | 2   | 1             | 1        | 0           |
| Low      | 3   | 1             | 2        | 0           |
| CVE      | 28  | 0             | 28       | 0           |

---

## High Findings

*None.*

---

## Medium Findings

### M-1 — Non-constant-time admin token comparison [FIXED]

**File:** `app/main.py:301`
**Risk:** The `_admin_auth` guard compared the Bearer token against `CLEARVIEW_ADMIN_TOKEN` using the `!=` operator (`if token != expected:`). Python string inequality short-circuits on the first differing byte, leaking timing information that an attacker with many requests could exploit to recover the token character-by-character via a remote timing attack.

**Remediation:** Replaced with `secrets.compare_digest(token, expected)` (added `import secrets` to the module-level imports). Fixed in this run.

---

### M-2 — `/metrics` endpoint unauthenticated [DEFERRED — design decision needed]

**File:** `app/main.py:2538`
**Risk:** The Prometheus-compatible `/metrics` endpoint has no authentication gate. It exposes per-`team_id` request counts, cost totals, token throughput, and provider/tier breakdowns. In a multi-tenant deployment this leaks tenant usage patterns to any caller who can reach the service port.

**Proposed design:** Add an opt-in env var `CLEARVIEW_METRICS_TOKEN`; if set, `/metrics` calls `_admin_auth` exactly as the other admin routes do. A Prometheus scrape job sets `Authorization: Bearer <token>` via `bearer_token` in its scrape config. When unset, behaviour is unchanged (backward-compatible). Deferred because toggling auth on an existing metrics endpoint changes operator scrape config and should be a conscious choice.

---

## Low Findings

### L-1 — Dockerfile runs as root [FIXED]

**File:** `Dockerfile:20`
**Risk:** The image had no `USER` directive, so the process ran as UID 0 inside the container. A container escape or file-write vulnerability would land as root.

**Remediation:** Added `RUN useradd -r -u 1001 -s /sbin/nologin clearview` and `USER clearview` before the `CMD` line. The `/data` volume is writable by UID 1001; operators using a named volume (the default in `docker-compose.yml`) need no changes — Docker mounts it writable. Operators bind-mounting a host path must `chown 1001` that path.

---

### L-2 — docker-compose publishes service on all interfaces [DEFERRED]

**File:** `docker-compose.yml:8`
**Risk:** `ports: ["8000:8000"]` binds to `0.0.0.0`, exposing the gateway to all network interfaces on the Docker host. On a shared or cloud host, this makes the service reachable without firewall rules.

**Proposed fix:** Change to `"127.0.0.1:8000:8000"` for local-dev and document a production note that operators should place a reverse proxy in front or use Docker network isolation. Deferred because the current setup is intentional for Docker Desktop / single-machine dev; changing it is a breaking change for some setups.

---

### L-3 — Team tokens stored as plaintext primary key [DEFERRED]

**File:** `app/teams.py:117,146`
**Risk:** Team Bearer tokens (`cv_team_<32 hex chars>`) are stored verbatim as the `id` primary key in the SQLite `teams` table. If the database file is read by an attacker (e.g., via path traversal, backup exposure, or volume mount misconfiguration), all active team tokens are directly readable.

**Proposed design:** Store a SHA-256 hash of the token as the primary key; accept the plaintext token from the caller, hash it on the fly, and look up the hash. Requires a one-time migration. Deferred because it is a behavior-changing refactor involving migration, and the attack vector requires DB read access which itself is a severe compromise.

---

## Dependency CVEs (pip-audit)

28 vulnerabilities found across 3 packages. All deferred — none are direct dependencies; `pip` and `setuptools` are environment tooling, not shipped in the container; `filelock` is a transitive dependency of `litellm`.

| Package    | Installed | CVEs / IDs | Fix version |
|------------|-----------|------------|-------------|
| filelock   | 3.32.7    | CVE-2026-112940, CVE-2026-112913, CVE-2026-112907, CVE-2026-112938, CVE-2026-112936, CVE-2026-112910, CVE-2026-112909, CVE-2026-112911, CVE-2026-112939, CVE-2026-112915, CVE-2026-112935, CVE-2026-112912, CVE-2026-112908 | 4.2.1 (some 4.0.9) |
| pip        | 24.0      | PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-196, PYSEC-2026-3721 | ≥26.2 |
| setuptools | 79.0.1    | PYSEC-2026-3447, GHSA-xmj3-9gf7-h52w | 84.1.0 |

**Recommended action:** Pin `litellm` to a release that pulls `filelock>=4.2.1` as a transitive dep, or add an explicit `filelock>=4.2.1` override in `pyproject.toml`. Upgrade `pip` and `setuptools` in the build environment.

---

## Carried-Over Deferred Items

*None (first run).*

---

## Minimum-Sweep Checklist

| Check | Result |
|-------|--------|
| All `@app.{get,post,delete}` routes call `_admin_auth`, `_client_key_allowed`, `_resolve_team`, or `_require_chat_team`, or are documented-open | ✅ — `/health`, `/v1/models`, `/chat` (serve HTML), `/feedback` (open by design, per inline comment), `/chat/login`, `/chat/logout` are intentionally open. `/metrics` is open (flagged as M-2). All others gated. |
| `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, `yaml.load` | ✅ — Not found. CLI providers use `asyncio.create_subprocess_exec` (safe). |
| SQL parameterized — no f-string interpolation of user-controlled values | ✅ — `teams.py:218` f-string constructs only column names from hardcoded lists; `telemetry.py:268,275` constructs only `WHERE` clauses from hardcoded strings. All `VALUES` are parameterized. |
| No tracked secrets (env/key/pem files, live-looking key strings) | ✅ — Only `.env.example` and `clearview-client.env.example` tracked (examples, no live values). Test files contain dummy keys (`sk-x`, `sk-test`). |
| Secret comparisons use `secrets.compare_digest` | ✅ after this fix — `_admin_auth` now uses `secrets.compare_digest`. Team token lookup is a DB equality check (deferred L-3). |
| Token generation uses `secrets` module | ✅ — `teams.py:117` uses `secrets.token_hex(16)`. |
| Dockerfile has non-root `USER` | ✅ after this fix. |
| docker-compose does not publish unauthenticated services beyond loopback | ⚠️ — flagged as L-2, deferred. |

---

## Positive Observations

- Team tokens generated with `secrets.token_hex(16)` (cryptographically random, 128-bit entropy).
- Admin endpoints consistently gated by `_admin_auth`; team token listing redacts to first 18 chars to prevent log/shoulder-surfing exposure.
- Chat cookie set with `HttpOnly=True, SameSite=Lax` — mitigates XSS token theft and CSRF.
- Budget enforcement guards against runaway spend; per-team daily and monthly caps available.
- No `shell=True` subprocess calls; all CLI providers use `asyncio.create_subprocess_exec`.
- SQLite queries throughout use parameterized values for all user-supplied data.

---

## Remediation Status

| Finding | Status |
|---------|--------|
| M-1 Non-constant-time admin token comparison | **Fixed** — `secrets.compare_digest` in `app/main.py` |
| M-2 `/metrics` unauthenticated | **Deferred** — needs operator design decision |
| L-1 Dockerfile runs as root | **Fixed** — `USER clearview` added |
| L-2 docker-compose all-interfaces bind | **Deferred** — breaking change for dev setups |
| L-3 Team tokens stored as plaintext | **Deferred** — requires DB migration |
| 28 CVEs (filelock, pip, setuptools) | **Deferred** — transitive/tooling deps |
