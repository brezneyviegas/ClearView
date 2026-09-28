# ClearView Security Audit — 2026-09-28

**Auditor:** Automated daily audit (Claude Code)
**Branch audited:** `main` (HEAD `e8ad2e3`)
**Audit date:** 2026-09-28
**Previous report:** None (first audit)

---

## Severity Summary

| Severity | New | Fixed This Run | Deferred | Carried Over |
|----------|-----|----------------|----------|--------------|
| High     | 1   | 1              | 0        | 0            |
| Medium   | 4   | 1              | 3        | 0            |
| Low      | 1   | 0              | 1        | 0            |

---

## High Findings

### H-1 — Non-constant-time admin token comparison ✅ FIXED

**File:** `app/main.py:302` (`_admin_auth`)
**Risk:** Timing side-channel allows an attacker to enumerate `CLEARVIEW_ADMIN_TOKEN` character-by-character using response-time differences, reducing the brute-force space from O(charset^length) to O(charset × length).
**Remediation:** Replace `token != expected` with `secrets.compare_digest(token, expected)`.
**Status:** Fixed in this PR — `import secrets` added at line 11; comparison changed to `secrets.compare_digest`.

---

## Medium Findings

### M-1 — Dockerfile: container runs as root ✅ FIXED

**File:** `Dockerfile` (pre-fix: no `USER` directive)
**Risk:** If an attacker achieves RCE through the app, they have root inside the container, simplifying container-escape and filesystem attacks.
**Remediation:** Add a non-root user before `CMD`.
**Status:** Fixed in this PR — `appuser` (uid 1001) created and set as `USER` before the `CMD` directive.

### M-2 — `/metrics` endpoint unauthenticated ⏳ DEFERRED

**File:** `app/main.py:2538`
**Risk:** The Prometheus `/metrics` endpoint is publicly reachable without auth. It exposes per-tier/provider request counts, cost totals, token counts, and drift percentage — enough to infer pricing configuration and usage patterns.
**Proposed design:** Add an optional `CLEARVIEW_METRICS_TOKEN` environment variable; when set, `_admin_auth`-style bearer check applied to `/metrics`. When unset, the endpoint remains open for local Prometheus scraping (common pattern). This preserves the default-open Prometheus workflow while allowing operators to lock it down in shared deployments.
**Reason deferred:** Behavior-changing for existing Prometheus setups; requires operator documentation update.

### M-3 — docker-compose: port 8000 bound to all interfaces ⏳ DEFERRED

**File:** `docker-compose.yml:8` (`ports: "8000:8000"`)
**Risk:** The gateway is exposed on `0.0.0.0:8000`, meaning any host on the Docker host's network can reach it. On a multi-tenant machine this bypasses the expectation that ClearView is a local gateway.
**Proposed design:** Change to `127.0.0.1:8000:8000` for single-developer setups, or document that production deployments must sit behind a reverse proxy that controls external exposure.
**Reason deferred:** Could break existing docker-compose users who rely on LAN access; warrants operator documentation, not a silent config change.

### M-4 — pip/setuptools CVEs in build toolchain ⏳ DEFERRED

**Tool:** `pip==24.0`, `setuptools==79.0.1` (dev/build environment)
**CVEs:**
- `pip`: PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-196, PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-3721 (fix: upgrade to pip ≥ 26.2.1)
- `setuptools`: PYSEC-2026-3447 (fix: upgrade to setuptools ≥ 83.0.0)
**Risk:** These affect the build/install toolchain, not runtime app code. Exploitation requires a malicious package to be installed during a build step. In CI/CD and Docker builds that run `pip install`, a crafted package could exploit the vulnerability.
**Proposed design:** Pin `pip>=26.2.1` and `setuptools>=83.0.0` in `pyproject.toml`'s build dependencies or add `RUN pip install --upgrade pip setuptools` to the Dockerfile before the app install step.
**Reason deferred:** Requires testing of the full build pipeline; no shipped app runtime impact.

---

## Low Findings

### L-1 — `/metrics` leaks provider model names via label values ⏳ DEFERRED

**File:** `app/main.py:2547` (`metrics()`)
**Risk:** Provider/model names embedded in Prometheus label values (`provider="anthropic"`, `tier="frontier"`) reveal the configured provider roster to anyone who can reach `/metrics`. This is a sub-issue of M-2; fixing M-2 resolves it.
**Reason deferred:** Subsumes into M-2 deferred design.

---

## Minimum-sweep checklist

| Check | Result |
|-------|--------|
| Every `@app.*` route calls `_admin_auth`, `_client_key_allowed`, `_resolve_team`, or `_require_chat_team`, or has a documented reason to be open | **Pass** — `/v1/models`, `/health`, `/metrics`, `/feedback` are intentionally public (see M-2 for metrics concern); all `/admin/*` routes call `_admin_auth`; all `/chat/*` routes call `_require_chat_team` or `_resolve_team` |
| `shell=True` / `os.system` / `eval(` / `exec(` / `pickle` / `yaml.load` | **Pass** — none found; providers use `asyncio.create_subprocess_exec` (no shell) |
| SQL parameterized — no f-string/concat with non-whitelisted values | **Pass** — f-string `WHERE` clauses assemble from hardcoded column/operator strings only; all user-supplied values go through `?` parameters |
| No tracked secrets: `.env`/`.pem`/key files in git | **Pass** — only `.env.example` and `clearview-client.env.example` (examples, no live values) |
| Live-looking keys in tracked files (`sk-`, `AKIA`, `ghp_`) | **Pass** — grep found no live keys |
| Secret comparisons use `secrets.compare_digest` | **Fixed** — H-1 patched this run |
| Token generation uses `secrets` module | **Pass** — `teams.py:117` and `chat.py:62` both use `secrets.token_hex` |
| Dockerfile has non-root `USER` | **Fixed** — M-1 patched this run |
| docker-compose doesn't publish unauthenticated services beyond loopback | **Deferred** — M-3 |
| pip-audit: no runtime CVEs | **Pass (with note)** — CVEs found only in `pip` and `setuptools` (build toolchain, not app runtime); see M-4 |

---

## Positive Observations

- Team tokens generated with `secrets.token_hex(16)` (128-bit entropy) — strong.
- Chat session cookies are `HttpOnly`, `SameSite=Lax` — protects against XSS token theft.
- Team token listing redacts to a short prefix (`id_short`) — leakage via operator UI minimized.
- Budget enforcement (daily/monthly per-team + global) in place with reject/warn/allow modes.
- `CLEARVIEW_ADMIN_TOKEN` defaults to open in dev mode; all `/admin/*` routes consistently gated.
- No `shell=True` subprocess calls; all CLI providers use `create_subprocess_exec`.
- All SQL queries use parameterized `?` bindings for user-supplied values.

---

## Remediation Status

| ID  | Finding                                 | Status    | Commit |
|-----|-----------------------------------------|-----------|--------|
| H-1 | Non-constant-time admin token compare   | ✅ Fixed  | This PR |
| M-1 | Dockerfile non-root USER missing        | ✅ Fixed  | This PR |
| M-2 | `/metrics` unauthenticated              | ⏳ Deferred | — |
| M-3 | docker-compose all-interface binding    | ⏳ Deferred | — |
| M-4 | pip/setuptools CVEs in build toolchain  | ⏳ Deferred | — |
| L-1 | `/metrics` leaks provider names         | ⏳ Deferred (subsumes into M-2) | — |
