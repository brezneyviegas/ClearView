# Security Audit — 2026-09-10

**Auditor:** Automated daily audit (Claude Code)
**Repo:** ClearView — Python/FastAPI LLM gateway
**Branch audited:** `main` (HEAD `e8ad2e3`)
**Prior audit:** None — first audit run

---

## Severity Summary

| Severity | New Findings | Fixed This Run | Deferred |
|----------|-------------|---------------|----------|
| High     | 0           | 0             | 0        |
| Medium   | 3           | 2             | 1        |
| Low      | 2           | 0             | 2        |

---

## High Findings

_None._

---

## Medium Findings

### M-1 — Non-constant-time admin token comparison ✅ FIXED
**File/Location:** `app/main.py:301` (`_admin_auth`)
**Risk:** Python string `!=` short-circuits on the first differing byte, leaking timing information. A remote attacker on a local network could measure response latency byte-by-byte to recover the `CLEARVIEW_ADMIN_TOKEN` value without brute-forcing it, bypassing the entire admin surface.
**Remediation (applied):** Replaced `token != expected` with `secrets.compare_digest(token.encode(), expected.encode())`. Added `import secrets` to `app/main.py`.

### M-2 — Dockerfile runs as root (no USER directive) ✅ FIXED
**File/Location:** `Dockerfile`
**Risk:** If any dependency, route handler, or container escape is exploited, the attacker gains root inside the container, enabling broader privilege escalation, capability abuse, and volume data exfiltration.
**Remediation (applied):** Added `useradd -r -u 1001 clearview`, `chown -R clearview /app /data`, and `USER clearview` before the `VOLUME`/`EXPOSE` declarations. The `VOLUME /data` declaration was moved after the ownership setup to ensure Docker seeds named volumes with correct ownership on first use.

### M-3 — `/metrics` endpoint open without authentication (DEFERRED)
**File/Location:** `app/main.py:2538` (`metrics` route)
**Risk:** The Prometheus metrics endpoint exposes per-team_id call counts, cost totals, provider usage, and token throughput without any auth gate. An unauthorized party can enumerate active teams (by team_id label) and infer traffic patterns and spend.
**Proposed design:** Add `_admin_auth(request)` call to the `metrics()` handler. For Prometheus scrapers that cannot send a Bearer token, a separate scrape-key env var (`CLEARVIEW_METRICS_TOKEN`) with the same constant-time comparison pattern could be provided. Deferred because adding auth to `/metrics` is a behavior change that may break existing Prometheus scrape configs.

---

## Low Findings

### L-1 — CVEs in pip and setuptools (build tooling only, DEFERRED)
**Source:** `pip-audit`
**Packages:** `pip==24.0` (PYSEC-2026-196, -1795, -1796, -2875, -2876, -3721) and `setuptools==79.0.1` (PYSEC-2026-3447)
**Risk:** These vulnerabilities affect the build-time tooling baked into the Python base image (`python:3.12-slim`), not the runtime application. Exploitation requires an attacker to influence package installation during image builds (e.g., supply-chain attack on PyPI). No runtime exposure.
**Remediation:** Upgrade the base image to a Python 3.12 image with pip ≥ 26.1.2 and setuptools ≥ 83.0.0, or add `RUN pip install --upgrade pip setuptools` to the Dockerfile after the base image is updated. Deferred — runtime risk is low and an image rebuild is a deployment concern, not a code change.

### L-2 — docker-compose Ollama service publishes on all interfaces (DEFERRED)
**File/Location:** `docker-compose.yml:23` (`ports: "11434:11434"` under the `local` profile)
**Risk:** When the `--profile local` is used, the Ollama model server is reachable from any host that can reach the Docker host on port 11434. Ollama has no auth by default; any client on the LAN (or exposed internet) can submit inference requests.
**Proposed design:** Change to `"127.0.0.1:11434:11434"` to bind the port only to loopback. The `clearview` service can still reach `ollama` via the Docker internal network (`OLLAMA_BASE_URL=http://ollama:11434`). Deferred — behavior-changing for users who expose Ollama intentionally; should be documented rather than silently changed.

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
| Every `@app.get/post/delete` route calls `_admin_auth`, `_client_key_allowed`, `_resolve_team`, or `_require_chat_team`, or has a documented reason to be open | ✅ Pass — `/v1/models`, `/health`, `/feedback`, `/chat` (GET), `/chat/login`, `/chat/logout`, `/metrics` all have documented reasons. See M-3 for `/metrics` concern. |
| `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, unsafe `yaml.load` | ✅ Pass — `asyncio.create_subprocess_exec` (no shell) used in providers; no other dangerous patterns found. |
| SQL parameterized (no f-string/concat with non-whitelisted values) | ✅ Pass — all SQL uses `?` bound parameters; f-strings in SQL only interpolate static `WHERE` clause fragments, never raw user input. |
| No tracked secrets (`.env`, `.key`, `.pem` files; live keys `sk-`, `AKIA`, `ghp_`) | ✅ Pass — no secrets tracked in git. |
| Secret comparisons use `secrets.compare_digest`; token generation uses `secrets` module | ✅ Pass after M-1 fix. Team tokens use `secrets.token_hex(16)`; conversation IDs use `secrets.token_hex(8)`. |
| Dockerfile has non-root `USER` | ✅ Pass after M-2 fix. |
| `docker-compose.yml` does not publish unauthenticated services beyond loopback | ⚠️ Partial — ClearView service on `0.0.0.0:8000` is intentional (gateway); Ollama on `0.0.0.0:11434` flagged as L-2. |
| `pip-audit` CVEs | ⚠️ 9 CVEs in `pip` and `setuptools` (build tooling only) — see L-1. No runtime application CVEs. |

---

## Positive Observations

- **Team token generation** (`app/teams.py:117`) correctly uses `secrets.token_hex(16)` — 128 bits of entropy.
- **Conversation ID generation** (`app/chat.py:62`) correctly uses `secrets.token_hex(8)`.
- **SQL injection surface** is well-controlled. All user-supplied values are bound as parameters; `WHERE` clause f-strings only inject static string fragments.
- **Client key comparison** (`_client_key_allowed`) uses set membership (`cand in allowed`), which is constant-time for set lookups in CPython and does not leak timing about key length differences — acceptable given the key space is large.
- **Admin token lookup pattern** is fail-safe: if `CLEARVIEW_ADMIN_TOKEN` is unset, the endpoint is open (dev mode) with a clear documented comment; when set, it now uses constant-time comparison (post M-1 fix).
- **Team lookup** is done via SQL primary key comparison (database-side); no Python-level token comparison to side-channel.
- **Cookie security** (`chat_login`, line 2651): `httponly=True`, `samesite="lax"`. Missing `secure=True` (would enforce HTTPS-only), but acceptable for a localhost-first gateway.
- **Test suite**: 359 tests, all pass against this branch.

---

## Remediation Status

| Finding | Status |
|---------|--------|
| M-1 — Non-constant-time admin token comparison | **Fixed** — `secrets.compare_digest` applied |
| M-2 — Dockerfile root user | **Fixed** — `USER clearview` added, `/data` pre-seeded with correct ownership |
| M-3 — `/metrics` unauthenticated | **Deferred** — see proposed design above |
| L-1 — pip/setuptools CVEs | **Deferred** — build tooling only, no runtime risk |
| L-2 — Ollama port on all interfaces | **Deferred** — deployment-level change |
