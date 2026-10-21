# ClearView Security Audit — 2026-10-21

**Auditor:** Automated daily audit (Claude Code)
**Branch audited:** `main` (HEAD: e8ad2e3)
**Remediation branch:** `security/audit-2026-10-21`
**Previous report:** None (first audit)

---

## Severity Summary

| Severity | New | Fixed this run | Deferred |
|----------|-----|----------------|----------|
| High     | 0   | 0              | 0        |
| Medium   | 3   | 2              | 1        |
| Low      | 3   | 0              | 3        |

---

## High Findings

_None._

---

## Medium Findings

### M-1 — Non-constant-time admin token comparison ✅ FIXED
**File:** `app/main.py:301`
**Risk:** `_admin_auth` compared the presented bearer token to `CLEARVIEW_ADMIN_TOKEN` with
`token != expected` (Python string equality). String comparison short-circuits on the first
differing byte, leaking timing information that allows remote timing attacks to recover the
admin token character by character.
**Remediation:** Replaced with `secrets.compare_digest(token, expected)`. Added `import secrets`
to the module imports.

---

### M-2 — Dockerfile runs container as root ✅ FIXED
**File:** `Dockerfile`
**Risk:** The image had no `USER` directive, so the application process runs as `root` (uid 0).
If an attacker achieves RCE or container escape, root access gives the widest blast radius for
host exploitation and violates the principle of least privilege.
**Remediation:** Added `RUN useradd -r -u 1001 -s /sbin/nologin clearview` and `USER clearview`
before the `CMD`. The `/data` volume directory is `chown`ed to the new user so the SQLite
databases remain writable.

---

### M-3 — `/metrics` Prometheus endpoint unauthenticated ⏸ DEFERRED
**File:** `app/main.py:2538`
**Risk:** The `/metrics` endpoint returns aggregate request counts broken down by tier, provider,
status, and `team_id` without any authentication. An unauthenticated caller can enumerate
active team identifiers and observe usage patterns. This is sensitive in a multi-tenant
deployment.
**Proposed design:** Add an optional `CLEARVIEW_METRICS_TOKEN` env var. When set, gate `/metrics`
with bearer-token auth identical to `_admin_auth`. When unset, leave it open (Prometheus
scrape in local dev). The fix is behavior-changing for any operator who scrapes without a token,
so it is deferred for operator review.

---

## Low Findings

### L-1 — `cv_session` cookie missing `Secure` flag ⏸ DEFERRED
**File:** `app/main.py:2651`
**Risk:** The `set_cookie` call in `chat_login` sets `httponly=True` and `samesite="lax"` but
omits `secure=True`. If the application is deployed behind an HTTP reverse proxy without the
flag, browsers may send the session token over plain HTTP, enabling interception.
**Proposed design:** Set `secure=True` when a `CLEARVIEW_HTTPS=1` (or `CLEARVIEW_COOKIE_SECURE=1`)
env var is set. Conditional flag avoids breaking local HTTP dev setups while hardening
production deployments.

---

### L-2 — pip 24.0 vulnerabilities in Docker base image ⏸ DEFERRED
**Tool:** `pip-audit`
**CVEs:** PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-196, PYSEC-2026-2875, PYSEC-2026-2876,
PYSEC-2026-3721. All fixed in `pip >= 26.2`.
**Risk:** pip vulnerabilities are exploitable only during package installation or when an attacker
can influence the package index or install commands. Runtime risk is low. However the Docker
image build uses pip 24.0 and inherits these vulnerabilities.
**Remediation:** Add `RUN pip install --upgrade pip` to the Dockerfile `RUN` layer, or pin
`pip>=26.2` in `pyproject.toml`'s build dependencies. Deferred — Dockerfile changes require
image rebuild testing and are not purely mechanical.

---

### L-3 — setuptools 79.0.1 vulnerability ⏸ DEFERRED
**Tool:** `pip-audit`
**CVE:** PYSEC-2026-3447. Fixed in `setuptools >= 83.0.0`.
**Risk:** Build-time only. Same risk profile as L-2.
**Remediation:** Upgrade `setuptools` in the base image or constrain it in `pyproject.toml`.
Deferred alongside L-2.

---

## Carried-Over Deferred Items

_None — this is the first audit run._

---

## Positive Observations

- **Team token generation** uses `secrets.token_hex(16)` (32-hex-char, 128-bit entropy)
  (`app/teams.py:117`). Strong.
- **Chat conversation IDs** use `secrets.token_hex(8)` (`app/chat.py:62`). Adequate.
- **All `/admin/*` routes** call `_admin_auth`. No admin endpoint found without the gate.
- **All `/chat/*` action routes** (model_options, me, conversations, send, send_stream) call
  `_require_chat_team`. The bare `/chat` GET serves the HTML page and correctly shows
  `logged_in: False` without leaking data.
- **No shell injection**: subprocess calls use `asyncio.create_subprocess_exec` (no `shell=True`),
  no `os.system`, `eval()`, `exec()`, `pickle`, or unsafe `yaml.load` found.
- **SQL is parameterized**: dynamic SQL in `app/teams.py` and `app/telemetry.py` interpolates
  only code-controlled column-name literals; all user-supplied values use `?` placeholders.
- **No tracked secrets**: `git ls-files` found no `.env`, `.key`, or `.pem` files in the index;
  no `sk-`, `AKIA`, or `ghp_` patterns in tracked Python/YAML/TOML sources.
- **Budget caps** enforced per-team (daily + monthly) before upstream dispatch.
- **HttpOnly** on session cookie prevents JS-side token theft.

---

## Remediation Status (this run)

| ID  | Title                               | Status  | Commit |
|-----|-------------------------------------|---------|--------|
| M-1 | Non-constant-time token comparison  | Fixed   | TBD    |
| M-2 | Dockerfile runs as root             | Fixed   | TBD    |
| M-3 | /metrics unauthenticated            | Deferred| —      |
| L-1 | Cookie missing Secure flag          | Deferred| —      |
| L-2 | pip 24.0 CVEs                       | Deferred| —      |
| L-3 | setuptools 79.0.1 CVE               | Deferred| —      |
