# Security Audit — 2026-11-28

**Auditor:** Automated daily audit (Claude Code)
**Branch audited:** `main` @ `e8ad2e3`
**Previous report:** None (first audit)

---

## Severity Summary

| Severity | New | Deferred from prior | Fixed this run |
|----------|-----|---------------------|----------------|
| High     | 0   | —                   | 0              |
| Medium   | 3   | —                   | 2              |
| Low      | 1   | —                   | 0              |

---

## High Findings

None.

---

## Medium Findings

### M-1 — Non-constant-time admin token comparison ✅ Fixed
**File:** `app/main.py:302`
**Risk:** `_admin_auth` compared the bearer token with `token != expected`, which short-circuits on the first mismatched byte. An attacker with many fast requests to a local or low-latency network can use timing differences to brute-force the admin token one character at a time.
**Remediation applied:** Replaced with `secrets.compare_digest(token, expected)`.

### M-2 — Dockerfile runs as root ✅ Fixed
**File:** `Dockerfile`
**Risk:** No `USER` directive was present; the container process ran as `root` (uid 0). A container escape or RCE would yield full root on the host if the container is not seccomp/AppArmor-confined.
**Remediation applied:** Added `USER nobody` before `CMD`.

### M-3 — `/metrics` endpoint unauthenticated — DEFERRED
**File:** `app/main.py:2538`
**Risk:** The Prometheus `/metrics` endpoint exposes `team_id` labels, per-tier request counts, and cost counters. Anyone who can reach port 8000 can read this data without authentication — a data-exfiltration risk on shared or public networks.
**Proposed design:** Add an optional `CLEARVIEW_METRICS_TOKEN` env var checked by a `_metrics_auth(request)` guard (pattern of `_admin_auth`), defaulting to open (dev) when unset. Deferred because it requires deciding whether the same token or a separate one gates metrics, and whether Prometheus scrapers need a bearer header vs IP allowlist.

---

## Low Findings

### L-1 — pip / setuptools CVEs in base image — DEFERRED
**Location:** Python 3.12-slim base image (pip 24.0, setuptools 79.0.1)
**CVEs:**
- `pip 24.0`: PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-196, PYSEC-2026-3721 (fix: upgrade pip ≥ 26.1.2)
- `setuptools 79.0.1`: PYSEC-2026-3447, GHSA-xmj3-9gf7-h52w (fix: upgrade setuptools ≥ 84.1.0)
**Risk:** These packages are only used during `pip install`; they are not imported by the running application. Exploitability requires a malicious package index or crafted `.whl` file at build time — not a runtime risk.
**Proposed design:** Add `RUN pip install --upgrade pip setuptools` after the base stage in the Dockerfile, or pin a newer `python:3.12-slim` digest that ships updated tooling. Deferred to avoid untested Dockerfile layer changes.

---

## Deferred Items Carried Over

None — this is the first audit.

---

## Positive Observations

- **Team token generation** uses `secrets.token_hex(16)` (32 hex chars → 40-char `cv_team_` prefixed id). Cryptographically strong.
- **No `shell=True`** in any subprocess call; all provider CLIs use `asyncio.create_subprocess_exec` with argument arrays.
- **SQL is fully parameterized**: f-string interpolation in queries is limited to safe, hardcoded clause strings (e.g. `"ts >= ?"`, `"team_id = ?"`); user-supplied values are always passed as `?` parameters.
- **No tracked secrets**: `git ls-files` returns no `.env`, `.key`, or `.pem` files; no live API keys (`sk-`, `AKIA`, `ghp_`) found in tracked files.
- **All `/admin/*` routes** call `_admin_auth`; all `/chat/*` data routes call `_require_chat_team`.
- **Open endpoints are intentional and low-risk**: `/health` (monitoring), `/v1/models` (IDE compatibility), `/feedback` (client rating own responses), `/chat` (login page, no data exposed).
- **Team budget enforcement** uses separate daily/monthly caps enforced before dispatch.
- **Cookie security**: session cookies set `httponly=True, samesite="lax"` on login.

---

## Remediation Status

| ID  | Finding                            | Status   | Commit |
|-----|------------------------------------|----------|--------|
| M-1 | Non-constant-time token comparison | Fixed    | this PR |
| M-2 | Dockerfile runs as root            | Fixed    | this PR |
| M-3 | `/metrics` unauthenticated         | Deferred | — |
| L-1 | pip/setuptools CVEs in base image  | Deferred | — |
