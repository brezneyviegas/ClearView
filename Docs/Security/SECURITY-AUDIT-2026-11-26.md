# Security Audit — 2026-11-26

**Auditor:** Automated daily audit (Claude Sonnet 4.6)  
**Branch reviewed:** `main` (HEAD `e8ad2e3`)  
**Audit branch:** `security/audit-2026-11-26`  
**Previous audit:** None — first run.

---

## Severity Summary

| Severity | New | Fixed this run | Deferred |
|----------|-----|----------------|----------|
| High     | 2   | 2              | 0        |
| Medium   | 2   | 0              | 2        |
| Low      | 2   | 0              | 2        |

---

## High Findings

### H1 — Non-constant-time admin token comparison  
**File:** `app/main.py:299` (`_admin_auth`)  
**Risk:** `token != expected` uses Python's default string comparison, which short-circuits on the first mismatched byte. An attacker with many requests can measure response latency to brute-force the `CLEARVIEW_ADMIN_TOKEN` one character at a time, gaining access to all `/admin/*` endpoints.  
**Remediation:** Replace with `secrets.compare_digest(token, expected)` (stdlib, no new dep).  
**Status: FIXED** — `secrets` imported and `compare_digest` applied in this branch.

---

### H2 — Dockerfile runs application as root  
**File:** `Dockerfile`  
**Risk:** No `USER` directive means the container process runs as UID 0 (root). A container-escape or path-traversal exploit would have full host-filesystem write access. CIS Docker Benchmark §4.1 requires non-root users.  
**Remediation:** Add `RUN useradd -m -u 1000 appuser && mkdir -p /data && chown appuser:appuser /data` and `USER appuser` before `VOLUME`/`EXPOSE`.  
**Status: FIXED** — added in this branch.

---

## Medium Findings

### M1 — `/metrics` endpoint is unauthenticated  
**File:** `app/main.py:2538`  
**Risk:** The Prometheus `/metrics` endpoint exposes internal telemetry: request counts by tier/provider/status/team_id, total native cost, total plan-equivalent cost, total tokens, and drift percentage. This leaks cost structure, provider selection logic, and team identifiers to any unauthenticated caller. Combined with M2, this endpoint is reachable from any network interface when running in Docker.  
**Remediation (proposed):** Add `_admin_auth(request)` guard, or allow scrape by IP allow-list via a Prometheus reverse-proxy. If the Prometheus scraper cannot send Bearer auth, consider a separate scrape endpoint protected at the network layer. Gate on `CLEARVIEW_METRICS_OPEN=1` env var if backward-compat is needed.  
**Status: DEFERRED** — adding auth here would break existing Prometheus scrapers without a migration path. Requires a design decision on scrape credentials or IP allow-listing.

---

### M2 — docker-compose exposes port 8000 on all interfaces  
**File:** `docker-compose.yml:8`  
**Risk:** `ports: - "8000:8000"` publishes to `0.0.0.0:8000` on the host, making every endpoint reachable from any host on the network. When `CLEARVIEW_ADMIN_TOKEN` is unset (default dev mode), all `/admin/*` routes are entirely open. Even in production with the token set, the unauthenticated `/metrics` and `/v1/models` endpoints are exposed.  
**Remediation (proposed):** Change to `"127.0.0.1:8000:8000"` for local/dev deployments; document that production deployments should terminate TLS and restrict inbound access at the network layer (nginx/traefik/security group) rather than relying on a Docker port binding.  
**Status: DEFERRED** — this is a deployment-configuration decision; changing it would break intentional LAN or Docker-network setups without an operator-visible flag or documented guidance.

---

## Low Findings

### L1 — pip 24.0 has six CVEs (install-time path traversal / arbitrary write)  
**Packages:** `pip==24.0`  
**CVEs:** PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-196, PYSEC-2026-3721  
**Risk:** All six affect pip's wheel/tar extraction and URL handling at install time — not at application runtime. Exploitability requires a malicious package source. Impact is to the build environment, not the running gateway.  
**Fix version:** pip>=26.2  
**Status: DEFERRED** — does not affect the running application; the Dockerfile can pin `pip` via `RUN pip install --upgrade pip>=26.2` in a future CI hardening pass.

---

### L2 — setuptools 79.0.1 has two CVEs (namespace_packages path traversal on Windows)  
**Packages:** `setuptools==79.0.1`  
**CVEs:** PYSEC-2026-3447 (fix: 83.0.0), GHSA-xmj3-9gf7-h52w (fix: 84.1.0)  
**Risk:** Both affect Windows-only paths (`namespace_packages.txt` in `.dist-info`) during wheel installation. The production environment is Linux (`python:3.12-slim`); runtime impact is negligible.  
**Status: DEFERRED** — Linux-only deploy; low practical risk. Upgrade in next dependency refresh.

---

## Deferred Items (carry-forward)

| ID  | Description                              | Proposed design                              |
|-----|------------------------------------------|----------------------------------------------|
| M1  | `/metrics` unauthenticated               | Scrape-token or IP allow-list via reverse proxy |
| M2  | docker-compose all-interface bind        | Document network-layer restriction; add note to README |
| L1  | pip CVEs (install-time)                  | Upgrade pip in Dockerfile on next CI hardening pass |
| L2  | setuptools CVEs (Windows-only)           | Upgrade setuptools on next dependency refresh |

---

## Positive Observations

- **SQL queries are parameterized** throughout `app/main.py`. All `WHERE` clauses use `?` placeholders and `params` tuples; no f-string interpolation of user-controlled values was found.
- **No tracked secrets** found. `git ls-files` returns no `.env`, `.key`, or `.pem` files. No `sk-`, `AKIA`, or `ghp_` patterns in tracked source files.
- **Dangerous builtins not used.** No `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, or `yaml.load(` calls in application code (subprocess providers use `asyncio.create_subprocess_exec`, which does not invoke a shell).
- **Token generation uses `secrets` module.** `cv_team_` tokens use `secrets.token_hex(16)` (teams.py:117); conversation IDs use `secrets.token_hex(8)` (chat.py:62).
- **Team tokens are properly gated.** `_resolve_team` and `_require_chat_team` validate team bearer tokens against the DB for all `/chat/*` routes. Admin endpoints consistently call `_admin_auth`.
- **Cookie flags are correct.** The `cv_session` cookie sets `httponly=True, samesite="lax"` (main.py:2651), preventing XSS-based cookie theft.
- **Budget enforcement is in place** for per-team daily/monthly caps and global daily cap with configurable on_breach modes.

---

## Minimum Sweep Results

| Check | Result |
|-------|--------|
| (a) Every `@app.get/post/delete` route calls `_admin_auth`, `_client_key_allowed`, `_resolve_team`, or `_require_chat_team`, or is intentionally open | PASS — `/v1/models`, `/health`, `/metrics`, `/feedback`, `/chat` (GET, public login page) are intentionally open; all others are gated |
| (b) `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, `yaml.load(` | PASS — none found in application code |
| (c) SQL parameterized (no f-string/concat with non-whitelisted values) | PASS |
| (d) No tracked secrets (env/key/pem or live-looking keys) | PASS |
| (e) Secret comparisons use `secrets.compare_digest`; token generation uses `secrets` module | FAIL (H1, now FIXED) |
| (f) Dockerfile non-root USER; docker-compose doesn't publish unauthenticated services beyond loopback | FAIL — Dockerfile (H2, now FIXED); docker-compose (M2, deferred) |
| (g) pip-audit CVEs | 8 unique CVEs across pip and setuptools (L1, L2, deferred) |

---

*Report generated by automated daily audit. Never includes .env contents or database token values.*
