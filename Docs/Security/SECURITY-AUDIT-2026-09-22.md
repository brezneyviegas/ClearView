# Security Audit — 2026-09-22

**Auditor:** Automated daily security audit (Claude Code)  
**Branch audited:** `main` (HEAD `e8ad2e3`)  
**Previous audit:** None (first audit run)  
**Test result:** 359 passed, 0 failed

---

## Severity Summary

| Severity | New | Fixed this run | Deferred |
|----------|-----|----------------|----------|
| High     | 1   | 1              | 0        |
| Medium   | 2   | 1              | 1        |
| Low      | 2   | 0              | 2        |
| **Total**| **5** | **2**        | **2**    |

---

## High Findings

### H-1 — Non-constant-time admin token comparison ✅ FIXED

**File/Location:** `app/main.py:300` (`_admin_auth`)  
**Risk:** The original code compared the bearer token against `CLEARVIEW_ADMIN_TOKEN` using Python's `!=` operator, which short-circuits on the first differing byte. A remote attacker on a low-latency network could mount a timing oracle to recover the token character-by-character without any brute-force rate limiting.  
**Remediation:** Replaced `token != expected` with `secrets.compare_digest(token, expected)` (constant-time). Added `import secrets` to the module header.  
**Status:** Fixed — commit on `security/audit-2026-09-22`. All 359 tests pass.

---

## Medium Findings

### M-1 — Dockerfile runs container as root ✅ FIXED

**File/Location:** `Dockerfile` (no `USER` directive)  
**Risk:** If the application process is compromised via a vulnerability in a dependency, the attacker gains root within the container. This can be pivoted to escape via kernel exploits or mounted-volume abuse.  
**Remediation:** Added `RUN useradd -r -u 1001 -g root clearview` and `USER clearview` before `CMD`. The `/data` volume mount is chown'd at build time; runtime writes succeed because the volume is owned by the new UID.  
**Status:** Fixed — commit on `security/audit-2026-09-22`. All 359 tests pass.

### M-2 — pip (24.0) and setuptools (79.0.1) CVEs in development environment

**File/Location:** `.venv` (development venv), base Docker image `python:3.12-slim`  
**CVEs:**
- `pip 24.0`: PYSEC-2026-1795, -1796, -196, -2875, -2876, -3721 (fix: pip ≥ 26.2)
- `setuptools 79.0.1`: PYSEC-2026-3447 (fix: setuptools ≥ 83.0.0)

**Risk:** pip vulnerabilities primarily affect the package-install surface (malicious package processing); setuptools CVE affects build-time parsing. Neither is a runtime attack vector for a deployed gateway instance. Docker builds using the base image inherit the vulnerable pip version.  
**Proposed remediation:** Add `RUN pip install --upgrade pip setuptools` to the Dockerfile before the application install step, and update the dev environment with `pip install --upgrade pip setuptools`. This is a safe, mechanical change but touches the build layer and may cause pip resolver behavior changes, so it is listed as deferred pending operator review.  
**Status:** **Deferred** — not auto-applied; operator should upgrade pip/setuptools in Dockerfile and dev venv.

---

## Low Findings

### L-1 — `/metrics` Prometheus endpoint unauthenticated

**File/Location:** `app/main.py:2538` (`GET /metrics`)  
**Risk:** The endpoint exposes per-tier request counts, cost totals, provider names, and token counts without any authentication. On a shared or internet-facing network this leaks business metrics. The data is not directly exploitable but aids targeted attacks.  
**Proposed remediation:** Gate `/metrics` behind `_admin_auth` or a dedicated `CLEARVIEW_METRICS_TOKEN`. This is a behaviour change (Prometheus scrapers need credentials updated), so it is listed as deferred.  
**Status:** **Deferred** — documented design decision (open scrape endpoint); operator should firewall this path at the network layer in production.

### L-2 — `/feedback` endpoint unauthenticated

**File/Location:** `app/main.py:1693` (`POST /feedback`)  
**Risk:** Any client with a `request_id` (returned in response headers) can submit a thumbs-up or thumbs-down rating. Malicious actors could poison the provider-scoring corpus by bulk-submitting false feedback on intercepted or guessed request IDs, skewing routing decisions over time.  
**Proposed remediation:** Require a matching team bearer token to submit feedback for a request owned by that team. This is a behaviour change (existing feedback integrations break). Deferred pending design.  
**Status:** **Deferred** — low exploitability requires knowledge of live `request_id` values; accept for now.

---

## Carried-over Deferred Items

None (first audit run).

---

## Positive Observations

- **Token generation:** Team tokens (`cv_team_`) and conversation IDs (`cv_chat_`) are generated with `secrets.token_hex` — cryptographically secure.
- **SQL parameterization:** All SQLite queries in `app/main.py` and telemetry use parameterized queries (`?` placeholders + tuple params). f-string interpolation is used only for query structure (column names, WHERE clauses with literal SQL keywords), not for user-supplied values. No injection risk found.
- **No tracked secrets:** `git ls-files` finds no `.env`, `.key`, `.pem`, or `.crt` files. Grep for `sk-`, `AKIA`, `ghp_` across all tracked files returns no hits.
- **No dangerous subprocess calls:** `subprocess.exec` (not `shell=True`) is used exclusively in provider CLI adapters with validated arguments. No `os.system`, `eval(`, `exec(`, `pickle`, or unsafe `yaml.load(` patterns found.
- **Session cookie security:** The `/chat/login` cookie is set with `httponly=True, samesite="lax"`, preventing JS access and CSRF.
- **Auth coverage:** All admin endpoints (`/admin/*`) are gated by `_admin_auth`. All chat endpoints requiring identity (`/chat/me`, `/chat/conversations*`, `/chat/send*`) are gated by `_require_chat_team`. Unauthenticated endpoints (`/health`, `/v1/models`, `/chat/login`, `/chat/logout`, `/metrics`, `/feedback`) each have documented reasons to be open.
- **docker-compose secrets:** API keys are passed via environment variable references (`${ANTHROPIC_API_KEY:-}`), not hardcoded.

---

## Remediation Status

| ID  | Finding                              | Status   | Commit                   |
|-----|--------------------------------------|----------|--------------------------|
| H-1 | Non-constant-time token compare      | Fixed    | security/audit-2026-09-22 |
| M-1 | Dockerfile runs as root              | Fixed    | security/audit-2026-09-22 |
| M-2 | pip/setuptools CVEs in build env     | Deferred | Upgrade pip ≥ 26.2, setuptools ≥ 83.0.0 in Dockerfile |
| L-1 | `/metrics` unauthenticated           | Deferred | Gate or firewall in prod |
| L-2 | `/feedback` corpus poisoning         | Deferred | Require team-token auth on feedback |
