# Security Audit — 2026-09-11

**Auditor:** Automated daily audit (Claude Code)
**Repo:** ClearView — Python/FastAPI LLM gateway
**Branch audited:** `main` (HEAD `e8ad2e3`)
**Prior audit:** 2026-09-10 (`security/audit-2026-09-10`)

---

## Severity Summary

| Severity | New Findings | Fixed This Run | Deferred |
|----------|-------------|---------------|----------|
| High     | 0           | 0             | 0        |
| Medium   | 0           | 2             | 1        |
| Low      | 0           | 0             | 2        |

**Note:** No code was committed to `main` since the 2026-09-10 audit (HEAD remains `e8ad2e3`). The two Medium findings fixed today (M-1, M-2) are re-applications of fixes that were committed to `security/audit-2026-09-10` but whose PR was not merged into `main`. They appear here as fixed-this-run to restore the intended remediations.

---

## High Findings

_None._

---

## Medium Findings

### M-1 — Non-constant-time admin token comparison ✅ FIXED (re-applied)
**File/Location:** `app/main.py` (`_admin_auth`, line ~302)
**Risk:** Python string `!=` short-circuits on the first differing byte, leaking timing information that could allow an attacker to recover `CLEARVIEW_ADMIN_TOKEN` byte-by-byte via timing side-channel.
**Status:** The fix (`secrets.compare_digest`) was applied in `security/audit-2026-09-10` but the PR was not merged. Re-applied to this branch.
**Remediation (applied):** Added `import secrets` to `app/main.py` imports. Replaced `if token != expected:` with `if not secrets.compare_digest(token.encode(), expected.encode()):`.

### M-2 — Dockerfile runs as root (no USER directive) ✅ FIXED (re-applied)
**File/Location:** `Dockerfile`
**Risk:** Any exploited dependency or route handler gains root inside the container, enabling broader privilege escalation, capability abuse, and volume data exfiltration.
**Status:** The fix was applied in `security/audit-2026-09-10` but the PR was not merged. Re-applied to this branch.
**Remediation (applied):** Added `RUN useradd -r -u 1001 -s /sbin/nologin clearview && mkdir -p /data && chown -R clearview:clearview /app /data` and `USER clearview` to Dockerfile before `CMD`.

### M-3 — `/metrics` endpoint open without authentication (DEFERRED — carried forward)
**File/Location:** `app/main.py` (`metrics` route, line ~2538)
**Risk:** The Prometheus metrics endpoint exposes per-team_id call counts, cost totals, provider usage, and token throughput without any auth gate. An unauthorized party can enumerate active teams (by team_id label) and infer traffic patterns and spend.
**First seen:** 2026-09-10
**Proposed design:** Add `_admin_auth(request)` call to the `metrics()` handler, with an optional separate scrape-key env var (`CLEARVIEW_METRICS_TOKEN`) for Prometheus scrapers. Deferred — adding auth to `/metrics` is a behavior change that may break existing Prometheus scrape configs and requires explicit user sign-off.

---

## Low Findings

### L-1 — CVEs in pip and setuptools (build tooling only, DEFERRED — carried forward)
**Source:** `pip-audit`
**Packages:** `pip==24.0` (PYSEC-2026-196, -1795, -1796, -2875, -2876, -3721; fix: pip ≥ 26.2) and `setuptools==79.0.1` (PYSEC-2026-3447; fix: setuptools ≥ 83.0.0)
**Risk:** These vulnerabilities affect build-time tooling baked into the Python base image (`python:3.12-slim`), not the runtime application. No runtime CVEs were found in application dependencies.
**First seen:** 2026-09-10
**Remediation:** Upgrade base image to a Python 3.12 release with pip ≥ 26.2 and setuptools ≥ 83.0.0, or add `RUN pip install --upgrade pip setuptools` to Dockerfile after package install. Deferred — runtime risk is low; an image rebuild is a deployment concern.

### L-2 — docker-compose Ollama service publishes on all interfaces (DEFERRED — carried forward)
**File/Location:** `docker-compose.yml` (`ports: "11434:11434"` under `local` profile)
**Risk:** When `--profile local` is used, the Ollama model server is reachable from any host on port 11434. Ollama has no auth by default; any client on the LAN or exposed internet can submit inference requests.
**First seen:** 2026-09-10
**Proposed design:** Change to `"127.0.0.1:11434:11434"`. The `clearview` service can still reach `ollama` via the Docker internal network. Deferred — behavior-changing for users who expose Ollama intentionally; should be documented rather than silently changed.

---

## Deferred Items (Carried Forward)

| ID  | Severity | Title                                   | First Seen   |
|-----|----------|-----------------------------------------|--------------|
| M-3 | Medium   | `/metrics` unauthenticated              | 2026-09-10   |
| L-1 | Low      | pip/setuptools CVEs (build tooling)     | 2026-09-10   |
| L-2 | Low      | Ollama port exposed on all interfaces   | 2026-09-10   |

---

## Checklist — Minimum Sweep (Every Run)

| Check | Result |
|-------|--------|
| Every `@app.get/post/delete` route calls `_admin_auth`, `_client_key_allowed`, `_resolve_team`, or `_require_chat_team`, or has a documented reason to be open | ✅ Pass — `/v1/models` open (models list, no sensitive data); `/health` open (intentional); `/feedback` open (client-facing rating, uses request_id as handle); `/chat` page open (serves HTML login page, gate happens on API calls); `/chat/login`, `/chat/logout` open (auth handshake endpoints); `/metrics` open — flagged M-3 (deferred). All admin routes use `_admin_auth`. All `/chat/*` data routes use `_require_chat_team`. |
| `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, unsafe `yaml.load` | ✅ Pass — no dangerous patterns found in `app/`. `asyncio.create_subprocess_exec` (no shell) is used in providers. |
| SQL parameterized (no f-string/concat with non-whitelisted values into queries) | ✅ Pass — all SQL uses `?` bound parameters. F-string interpolations in `teams.py:218` and `telemetry.py:268,275` inject only static SQL clause fragments (e.g. `"ts >= ?"`, column names), never raw user input. |
| No tracked secrets (`.env`, `.key`, `.pem` files; live keys `sk-`, `AKIA`, `ghp_`) | ✅ Pass — only `.env.example` and `clearview-client.env.example` are tracked; both contain only empty or placeholder values. No live keys found in tracked files. |
| Secret comparisons use `secrets.compare_digest`; token generation uses `secrets` module | ✅ Pass after M-1 fix. Team tokens: `secrets.token_hex(16)`. Conversation IDs: `secrets.token_hex(8)`. Admin token comparison: `secrets.compare_digest` (fixed). |
| Dockerfile has non-root `USER` | ✅ Pass after M-2 fix — `USER clearview` (uid 1001) added. |
| `docker-compose.yml` does not publish unauthenticated services beyond loopback | ⚠️ Partial — ClearView on `0.0.0.0:8000` is intentional (gateway); Ollama on `0.0.0.0:11434` flagged as L-2 (deferred). |
| `pip-audit` CVEs | ⚠️ 14 findings (deduplicated: 7 unique CVEs) in `pip==24.0` and `setuptools==79.0.1` — build tooling only; no runtime application CVEs. See L-1. |

---

## Positive Observations

- **No code changes on `main` since last audit.** HEAD remains at `e8ad2e3`, confirming the attack surface is unchanged.
- **Team token generation** (`app/teams.py:117`) correctly uses `secrets.token_hex(16)` — 128 bits of entropy.
- **Conversation ID generation** (`app/chat.py:62`) correctly uses `secrets.token_hex(8)`.
- **SQL injection surface** is well-controlled. All user-supplied values are bound as parameters; `WHERE` clause f-strings inject only static string fragments.
- **Client key comparison** (`_client_key_allowed`) uses set membership, which is constant-time for CPython set lookups and does not leak timing about key length differences.
- **Cookie security** (`chat_login`): `httponly=True`, `samesite="lax"`. Missing `secure=True` would enforce HTTPS-only; acceptable for a localhost-first gateway.
- **Test suite:** 359 tests, all pass against this branch (after re-applying M-1 and M-2 fixes).

---

## Remediation Status

| Finding | Status |
|---------|--------|
| M-1 — Non-constant-time admin token comparison | **Fixed** — `secrets.compare_digest` applied (re-applied; prior PR not merged) |
| M-2 — Dockerfile root user | **Fixed** — `USER clearview` added (re-applied; prior PR not merged) |
| M-3 — `/metrics` unauthenticated | **Deferred** — see proposed design above |
| L-1 — pip/setuptools CVEs | **Deferred** — build tooling only, no runtime risk |
| L-2 — Ollama port on all interfaces | **Deferred** — deployment-level change |
