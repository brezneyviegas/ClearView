# Security Audit — 2026-11-23

**Repository:** ClearView (Python/FastAPI LLM gateway)
**Auditor:** Automated daily audit (Claude Code)
**Branch audited:** `main` (HEAD `e8ad2e3`)
**Previous report:** None (first audit)

---

## Severity Summary

| Severity | New | Fixed this run | Deferred | Total open |
|----------|-----|---------------|----------|-----------|
| High     | 0   | 0             | 0        | 0         |
| Medium   | 2   | 2             | 0        | 0         |
| Low      | 2   | 0             | 2        | 2         |

---

## Findings

### Medium — Fixed

#### M-1: Non-constant-time admin token comparison (timing side-channel)

**File:** `app/main.py:301`
**Risk:** `_admin_auth` compared the incoming bearer token against `CLEARVIEW_ADMIN_TOKEN`
using `token != expected` (Python string equality), which short-circuits on the first
differing byte. An attacker on the same network who can send many requests can distinguish
correct prefix bytes from incorrect ones via latency, enabling a character-by-character
brute-force of the admin token.
**Remediation (applied):** Replaced with `secrets.compare_digest(token, expected)`, which
compares in constant time regardless of where the strings first differ. Added `import secrets`
(the standard library module was not previously imported in `main.py`).

#### M-2: Dockerfile runs container as root

**File:** `Dockerfile`
**Risk:** No `USER` directive was present, so the application process ran as UID 0 inside
the container. If a path traversal, SSRF, or remote-code vulnerability were exploited, the
attacker would gain root-equivalent access within the container, making container escape
easier and increasing blast radius in a compromised pod.
**Remediation (applied):** Added an `adduser --disabled-password appuser` layer and
`USER appuser` directive after the data-volume `mkdir`. The `/data` volume directory is
pre-created and `chown`-ed to `appuser` so the app can still write its SQLite database.

---

### Low — Deferred

#### L-1: `/metrics` endpoint unauthenticated — exposes `team_id` labels

**File:** `app/main.py:2538`
**Risk:** The Prometheus `/metrics` endpoint has no auth gate (`_admin_auth` is not called).
It exposes `team_id` values as label names in the `clearview_requests_total` counter, which
leaks the existence and identifiers of configured teams to any network-reachable client.
In a shared-network or cloud deployment this constitutes information disclosure.
**Proposed design (deferred):** Gate `/metrics` under the same `CLEARVIEW_ADMIN_TOKEN`
bearer check as other admin endpoints, or add a separate `CLEARVIEW_METRICS_TOKEN`
environment variable so Prometheus scrapers can be given a narrower credential. A behavior-
changing fix requires deciding which design is preferred and verifying the Prometheus
scrape config is updated — deferred for operator review.

#### L-2: pip 24.0 and setuptools 79.0.1 CVEs in Docker build layer

**Tool:** `pip-audit` (run against installed packages)
**Packages / IDs:** `pip 24.0` (PYSEC-2026-1795, -1796, -2875, -2876, -196, -3721 — fix:
26.1.2+), `setuptools 79.0.1` (PYSEC-2026-3447, GHSA-xmj3-9gf7-h52w — fix: 84.1.0).
**Risk:** These are vulnerabilities in the `pip` and `setuptools` build tools baked into
the `python:3.12-slim` base image at build time. They affect the package-installation step
during `docker build`, not the running application (neither package is imported at runtime).
Real exploitability requires a malicious package source during the build. Application
dependencies themselves have zero known CVEs.
**Proposed remediation (deferred):** Upgrade pip/setuptools as the first `RUN` step in
the Dockerfile (`RUN pip install --upgrade pip setuptools`). Deferring until the base image
pin decision is made (a pinned digest may be preferable to avoid supply-chain drift).

---

## Positive Observations

- All `/admin/*` routes call `_admin_auth` before any data access.
- All `/chat/*` data routes call `_require_chat_team`, which validates the session cookie.
- Team tokens are generated with `secrets.token_hex(16)` — cryptographically random, not
  predictable.
- No `shell=True`, `os.system`, `eval()`, `exec()`, or `pickle` patterns found anywhere in
  `app/`.
- All SQL queries use parameterized bindings (f-strings are used only for structural
  `WHERE`-clause assembly with pre-validated column names, not for interpolating user values).
- No tracked secrets found (`git ls-files` and grep for `sk-`, `AKIA`, `ghp_` returned only
  test monkeypatch placeholders in test files, not real credentials).
- `/chat/login` stores the session in an `HttpOnly; SameSite=Lax` cookie, mitigating XSS
  token theft.
- Team tokens are redacted to an `id_short` prefix in the list endpoint — the full token is
  returned only on creation.
- Input clamping (`max(1, min(value, cap))`) applied to all time-window query parameters to
  prevent resource abuse.

---

## Remediation Status

| ID  | Description                              | Status              | Branch                     |
|-----|------------------------------------------|---------------------|----------------------------|
| M-1 | Constant-time admin token compare        | Fixed               | security/audit-2026-11-23  |
| M-2 | Dockerfile non-root USER                 | Fixed               | security/audit-2026-11-23  |
| L-1 | /metrics unauthenticated                 | Deferred — design   | —                          |
| L-2 | pip/setuptools CVEs in build layer       | Deferred — ops      | —                          |
