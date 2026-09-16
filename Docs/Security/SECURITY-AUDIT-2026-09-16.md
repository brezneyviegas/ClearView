# ClearView Security Audit — 2026-09-16

**Auditor:** Automated daily security audit (Claude Code)  
**Branch audited:** `main` (HEAD: `e8ad2e3`)  
**Prior audit:** None (first run)  
**Scope:** Full baseline sweep + code changed since project inception  

---

## Severity Summary

| Severity | New Findings | Fixed This Run | Deferred |
|----------|-------------|----------------|----------|
| High     | 0           | 0              | 0        |
| Medium   | 3           | 2              | 1        |
| Low      | 8           | 0              | 8        |
| **Total**| **11**      | **2**          | **9**    |

---

## High Findings

_None._

---

## Medium Findings

### M-1 · Non-constant-time admin token comparison ✅ FIXED
**File:** `app/main.py:301`  
**Risk:** The `_admin_auth` guard compared the bearer token with a simple `token != expected` equality check. A remote attacker with a high-speed connection could use timing differences to oracle-guess the `CLEARVIEW_ADMIN_TOKEN` character by character, eventually authenticating without the token.  
**Remediation:** Replaced with `secrets.compare_digest(token, expected)` (added `import secrets` to the module). All tests pass.  
**Status:** Fixed on this branch.

---

### M-2 · Dockerfile runs as root ✅ FIXED
**File:** `Dockerfile`  
**Risk:** The container had no `USER` directive, so the application process ran as UID 0 (root). If a future vulnerability in FastAPI, litellm, or any provider adapter allowed code execution, the attacker would gain root inside the container — widening the blast radius to any mounted volumes, adjacent containers on the same bridge network, and host kernel exploits.  
**Remediation:** Added `RUN useradd -r -u 1001 -g root clearview && chown -R clearview:root /data /app` and `USER clearview` before the `CMD`. The `/data` volume remains writable by the new user.  
**Status:** Fixed on this branch.

---

### M-3 · Unauthenticated `/metrics` endpoint leaks cost and usage telemetry — DEFERRED
**File:** `app/main.py:2538`  
**Risk:** The Prometheus `/metrics` endpoint has no auth gate. It exposes per-tier/provider/team request counts, cumulative USD costs, total output tokens, and drift percentage. In a multi-tenant or production deployment this reveals operator spend and traffic patterns to any unauthenticated requester.  
**Proposed design:** Gate `/metrics` behind `_admin_auth` (consistent with all other `/admin/*` endpoints). Operators using a Prometheus scraper should add `Authorization: Bearer <CLEARVIEW_ADMIN_TOKEN>` to the scraper config (`scrape_configs[].basic_auth` or `authorization.credentials`). No functional change required on the Python side beyond adding `_admin_auth(request)` at the top of `metrics()`. Because this may break existing Prometheus setups, this is a non-silent config change and is deferred pending a deprecation notice in the changelog.  
**Status:** Deferred — see proposed design above.

---

## Low Findings (all deferred — CVEs in transitive dependencies)

All CVEs below affect transitive dependencies pulled in by `litellm`, `httpx`, or the build toolchain. None are in code ClearView authors directly. Upgrades require testing against the full litellm/httpx compatibility matrix and should be scheduled as a dependency-update PR.

### L-1 · cryptography 41.0.7 — 6 CVEs
| ID | Fix Version |
|----|-------------|
| PYSEC-2024-225 | 42.0.4 |
| PYSEC-2026-1283 | 42.0.0 |
| PYSEC-2026-1285 | 42.0.2 |
| PYSEC-2026-2141 | 46.0.5 |
| PYSEC-2026-35 | 46.0.6 |
| PYSEC-2026-3553 | 49.0.0 |
| PYSEC-2026-3554 | 49.0.0 |
| GHSA-h4gh-qq45-vh27 | 43.0.1 |
| GHSA-537c-gmf6-5ccf | 48.0.1 |

**Remediation:** Upgrade `cryptography` to ≥ 46.0.6 (clears all but the two 49.0.0 entries). Pin in `pyproject.toml` as `cryptography>=46.0.6` under an explicit constraint if litellm allows it.

### L-2 · pyjwt 2.7.0 — 4 CVEs
| ID | Fix Version |
|----|-------------|
| PYSEC-2026-120 | 2.12.0 |
| PYSEC-2026-175 | 2.13.0 |
| PYSEC-2026-177 | 2.13.0 |
| PYSEC-2026-179 | 2.13.0 |

**Remediation:** Upgrade `pyjwt` to ≥ 2.13.0. Note: `PYSEC-2025-183` has no published fix version yet.

### L-3 · urllib3 2.6.3 — 2 CVEs (fixed in 2.7.0)
`PYSEC-2026-141`, `PYSEC-2026-142`. Upgrade to ≥ 2.7.0.

### L-4 · idna 3.11 — 1 CVE (fixed in 3.15)
`PYSEC-2026-215`. Upgrade to ≥ 3.15.

### L-5 · httplib2 0.20.4 — 1 CVE (fixed in 0.32.0)
`PYSEC-2026-3444`. Upgrade to ≥ 0.32.0.

### L-6 · setuptools 68.1.2 — 3 CVEs
`PYSEC-2025-49` (fix: 78.1.1), `PYSEC-2026-1918` (fix: 70.0.0), `PYSEC-2026-3447` (fix: 83.0.0). Build-time only; not a runtime risk for deployed containers, but should be updated in the build environment.

### L-7 · pip 24.0 — multiple CVEs (fixed in 25.3 → 26.2.1)
Build-time only. Not a runtime risk.

### L-8 · wheel 0.42.0 — CVE-2026-24049 (fixed in 0.46.2)
Build-time only. Not a runtime risk.

---

## Carried-Over Deferred Items

_None (first audit)._

---

## Positive Observations

- **Token generation uses `secrets` module** — `teams.create()` uses `secrets.token_hex(16)` for team tokens; chat sessions use `secrets.token_hex(8)`. Correct.
- **SQL injection: all queries are parameterised** — All `f`-string SQL in `main.py`, `telemetry.py`, and `teams.py` interpolates only statically-constructed column/clause strings; all user-supplied values are bound via `?` parameters. No injection vectors found.
- **No tracked secrets or key files** — `git ls-files` shows no `.env`, `.pem`, or `.key` files tracked; grep for `sk-`, `AKIA`, and `ghp_` prefixes returned no hits.
- **No dangerous shell execution** — No `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, or unsafe `yaml.load` calls found anywhere in `app/`.
- **Admin routes consistently gated** — Every `/admin/*` endpoint calls `_admin_auth(request)` as its first statement. The `/feedback`, `/health`, `/v1/models`, and `/chat/login` endpoints are intentionally open and are documented as such.
- **Cookie auth hardened** — `/chat/login` sets `httponly=True` and `samesite="lax"` on the session cookie. Secure flag is absent (local dev default) but not a finding at this maturity stage.
- **Team tokens redacted in list view** — `GET /admin/teams` returns only a truncated prefix, preventing token leakage via operator tooling.
- **docker-compose secrets via environment** — API keys in `docker-compose.yml` are injected via `${VAR:-}` substitution, not hardcoded.

---

## Remediation Status This Run

| Finding | Status |
|---------|--------|
| M-1: non-constant-time admin token compare | ✅ Fixed (`secrets.compare_digest`) |
| M-2: Dockerfile runs as root | ✅ Fixed (added `USER clearview`) |
| M-3: unauthenticated `/metrics` | ⏳ Deferred (proposed design documented above) |
| L-1 through L-8: CVEs in transitive deps | ⏳ Deferred (dependency-update PR recommended) |
