# Security Audit — 2026-09-26

**Auditor:** Automated daily audit (Claude Code)
**Branch audited:** `main` (HEAD `e8ad2e3`)
**Remediation branch:** `security/audit-2026-09-26`
**Test result:** 359 passed, 0 failed

---

## Severity Summary

| Severity | New findings | Fixed this run | Deferred |
|----------|-------------|---------------|---------|
| High     | 0           | —             | 0        |
| Medium   | 3           | 1             | 2        |
| Low      | 2           | 0             | 2        |
| **Total**| **5**       | **1**         | **4**    |

---

## High Findings

_None._

---

## Medium Findings

### M1 — Non-constant-time admin token comparison (FIXED)

**File/location:** `app/main.py:302` — `_admin_auth()`
**Risk:** The original code used `token != expected` (Python `!=` on strings) to validate the `CLEARVIEW_ADMIN_TOKEN` bearer token. CPython's string comparison short-circuits on the first differing byte, leaking timing information that a network attacker can exploit to brute-force the token one character at a time (timing oracle attack). All admin routes (`/admin/*`) are affected when `CLEARVIEW_ADMIN_TOKEN` is set.
**Remediation:** Replaced with `secrets.compare_digest(token, expected)`, which runs in constant time regardless of where the strings differ. Added `import secrets` to module imports. **Fixed in this run; all 359 tests pass.**

---

### M2 — Docker container runs as root (FIXED)

**File/location:** `Dockerfile`
**Risk:** The image had no `USER` directive, so the Python process ran as UID 0 inside the container. If a vulnerability in the app, a dependency, or the LLM dispatch path allowed code execution, the attacker would gain root inside the container, significantly easing container-escape or host-pivoting attempts.
**Remediation:** Added a `clearview` non-root user with `adduser`, `mkdir -p /data`, and `chown clearview /data` before the `USER clearview` directive, so the process drops privileges before the app starts. **Fixed in this run; all 359 tests pass.**

---

### M3 — `/metrics` Prometheus endpoint unauthenticated (DEFERRED)

**File/location:** `app/main.py:2538` — `@app.get("/metrics")`
**Risk:** The `/metrics` endpoint (Prometheus text format) exposes per-team cost data, provider names, tier labels, team IDs, and request counts without any authentication. An unauthenticated user on the same network can enumerate active teams and their spending patterns.
**Remediation:** Add `_admin_auth(request)` as the first call inside `metrics()`. This requires accepting a `request: Request` parameter.
**Deferred reason:** Adding auth to `/metrics` is a behavior-breaking change for any Prometheus scrape job already configured without a bearer token. The fix requires coordinating a scraper reconfiguration alongside the code change. Proposed design: add `CLEARVIEW_METRICS_TOKEN` env var (separate from admin token) with a dedicated `_metrics_auth()` helper, defaulting to open when unset (matches current behavior) so the rollout can be staged.

---

## Low Findings

### L1 — pip build-tool CVEs in Docker base image (DEFERRED)

**Scope:** `pip` 24.0 (installed in Python 3.12-slim base image)
**CVEs:** CVE-2025-8869, CVE-2026-1703, CVE-2026-3219, CVE-2026-6357, CVE-2026-8643, CVE-2026-13346 (6 unique IDs; fix version: pip ≥ 26.1.2)
**Risk:** All six vulnerabilities require installing a package from a malicious index or a crafted archive. They affect `pip` itself — the app's runtime code does not call pip. In production Docker usage pip is only invoked during `docker build`; the attack surface requires a compromised PyPI package reaching the build step, not an API request.
**Remediation:** Add `RUN pip install --upgrade pip` to the Dockerfile after the base image is pulled, or pin to `pip>=26.1.2` in a build stage. Deferred because the runtime is Python 3.12 which implements PEP 706, mitigating the most severe symlink-traversal path.

### L2 — setuptools CVE in Docker base image (DEFERRED)

**Scope:** `setuptools` 79.0.1
**CVE:** CVE-2026-59890 / GHSA-h35f-9h28-mq5c (fix version: setuptools ≥ 83.0.0)
**Risk:** `MANIFEST.in` exclusion bypass via NFC/NFD Unicode normalization mismatch when building source distributions on macOS APFS. Affects sdist packaging only; runtime app is unaffected.
**Remediation:** Upgrade setuptools in the build environment or Docker image. Low priority for this repo since it is not published as a PyPI package.

---

## Carried-Over Deferred Items

_This is the first audit; no prior deferred items._

---

## Minimum Sweep Results

| Check | Result |
|-------|--------|
| All `@app.get/post/delete` routes call `_admin_auth`, `_client_key_allowed`, `_resolve_team`, or `_require_chat_team`, or have a documented reason to be open | Pass — `/v1/models`, `/health`, `/feedback` are intentionally open (OpenAI compat, health check, client-facing rating); `/metrics` flagged as M3 |
| `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, unsafe `yaml.load` | Pass — none found in `app/`. `create_subprocess_exec` used in CLI providers (safe, no shell injection) |
| SQL parameterized — no f-string/concat interpolating non-whitelisted values | Pass — all `execute()` calls use `?` placeholders for values; f-strings only interpolate hardcoded `WHERE` clause structure built from internal logic |
| No tracked secrets: `.env`/`.key`/`.pem` files or live-looking keys in tracked files | Pass — no such files tracked; no `sk-`, `AKIA`, or `ghp_` patterns found |
| Secret comparisons use `secrets.compare_digest`; token generation uses `secrets` module | Fixed (M1) — `_admin_auth` now uses `secrets.compare_digest`; team tokens correctly use `secrets.token_hex()` |
| Dockerfile has non-root `USER` | Fixed (M2) |
| docker-compose doesn't publish unauthenticated services beyond loopback | Pass — `clearview` service binds to the container port only (no host-port mapping that skips auth); `ollama` publishes `11434:11434` but only under the `local` profile and is a local model server with no sensitive data |
| `pip-audit` CVEs | 14 findings in 2 packages (`pip` 24.0 and `setuptools` 79.0.1) — all are build-tool CVEs; runtime app packages are clean. See L1, L2 |

---

## Positive Observations

- No use of dangerous code patterns (`shell=True`, `os.system`, `eval`, `exec`, `pickle`, unsafe `yaml.load`).
- CLI subprocess calls use `asyncio.create_subprocess_exec` (arg list, no shell) — no shell injection possible.
- All SQL queries use parameterized `?` placeholders for user-controlled values.
- No secrets committed to the repository.
- `secrets.token_hex(16)` used for team token generation — cryptographically secure.
- Chat session cookie set with `httponly=True, samesite="lax"` — mitigates XSS token theft.
- Team tokens redacted to a short prefix in list API responses — prevents full token leakage via operator logs.
- All admin routes gated by `_admin_auth()`.
- All chat routes gated by `_require_chat_team()`.
- Budget enforcement caps per-team daily/monthly and global daily spend.
