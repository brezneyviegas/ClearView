# ClearView Security Audit — 2026-11-02

**Auditor:** Automated daily audit (Claude Code)
**Branch audited:** `main` @ `e8ad2e3`
**Fix branch:** `security/audit-2026-11-02`
**Previous report:** _(none — first audit run)_

---

## Severity Summary

| Severity | New | Fixed this run | Deferred |
|----------|-----|----------------|----------|
| High     | 1   | 1              | 0        |
| Medium   | 4   | 1              | 3        |
| Low      | 2   | 0              | 2        |

---

## High Findings

### H-1 — Timing-attack vulnerability in admin token comparison ✅ FIXED

**File:** `app/main.py:300` (`_admin_auth`)
**Risk:** An attacker who can make rapid requests to any `/admin/*` endpoint can exploit Python's short-circuit string comparison (`!=`) to oracle-brute-force `CLEARVIEW_ADMIN_TOKEN` character by character. In a local or container-exposed deployment (see M-4) this is directly reachable.
**Remediation applied:** Replaced `token != expected` with `hmac.compare_digest(token, expected)`, which is guaranteed constant-time for equal-length strings.

---

## Medium Findings

### M-1 — Dockerfile runs as root (no `USER` directive) ✅ FIXED

**File:** `Dockerfile`
**Risk:** If any application code or dependency is exploited, the attacker gains root inside the container, making container-escape or host pivot easier.
**Remediation applied:** Added `useradd clearview` (UID 10001) and `USER clearview` before `VOLUME`/`EXPOSE`. The `/data` directory is `chown`-ed to this user at build time so new volumes are writable.

### M-2 — `/v1/models` endpoint unauthenticated (deferred)

**File:** `app/main.py:364` (`list_models`)
**Risk:** Exposes the full configured tier/model list to any caller without requiring `CLEARVIEW_CLIENT_KEYS` or `CLEARVIEW_ADMIN_TOKEN`. Low direct impact (no writes, no sensitive data) but reveals internal provider configuration.
**Proposed remediation:** Add `_client_key_allowed(request)` before returning. Requires client SDK compat review — some OpenAI-compatible clients call `/v1/models` pre-flight without a key.
**Status:** Deferred — behavior-changing; needs operator sign-off.

### M-3 — `/metrics` Prometheus endpoint unauthenticated (deferred)

**File:** `app/main.py:2538` (`metrics`)
**Risk:** Exposes request counts, cost totals, tier/provider labels, and drift percentages without authentication. In a shared or internet-exposed deployment this leaks operational intelligence.
**Proposed remediation:** Gate behind `_admin_auth` and document scraper configuration. Alternatively, bind metrics to a separate internal port via a `CLEARVIEW_METRICS_TOKEN` env var.
**Status:** Deferred — changing Prometheus scrape config is an ops decision; marking for next sprint.

### M-4 — docker-compose publishes gateway on all interfaces (deferred)

**File:** `docker-compose.yml:9` (`ports: - "8000:8000"`)
**Risk:** `0.0.0.0:8000` means the ClearView gateway (and unauthenticated `/metrics`) is reachable from any network interface on the Docker host, not just loopback. In a cloud VM this often means it's public.
**Proposed remediation:** Change to `"127.0.0.1:8000:8000"` and document that a reverse proxy (nginx, Caddy) should terminate TLS and forward.
**Status:** Deferred — behavior-changing; may break existing user setups. Document in README.

---

## Low Findings

### L-1 — Session cookie missing `Secure` flag (deferred)

**File:** `app/main.py:2651` (`chat_login`)
**Risk:** `cv_session` cookie is set without `secure=True`. On a plain-HTTP deployment the token is transmitted in cleartext, enabling session hijacking on the same network.
**Proposed remediation:** Pass `secure=True` when running behind HTTPS (detect via `request.headers.get("x-forwarded-proto") == "https"` or a `CLEARVIEW_SECURE_COOKIES=1` env var).
**Status:** Deferred — requires TLS proxy awareness; not a standalone change.

### L-2 — Vulnerable dependencies (deferred)

**Tool:** `pip-audit` (run 2026-11-02)

| Package    | Version | CVEs / IDs                              | Fix Version |
|------------|---------|-----------------------------------------|-------------|
| filelock   | 3.32.7  | CVE-2026-112940, -112913, -112907, et al (13 total) | 4.2.1 |
| pip        | 24.0    | PYSEC-2026-1795, -1796, -2875, -2876, -196, -3721 (6 total) | 26.1.2 |
| setuptools | 79.0.1  | PYSEC-2026-3447                         | 83.0.0 |

**Risk:** `filelock` CVEs are the highest concern as it is a runtime dependency. `pip`/`setuptools` CVEs affect the build environment and supply-chain security but not the running service.
**Proposed remediation:** `pip install "filelock>=4.2.1" "setuptools>=83.0.0"` in the `[dev]` extra and/or constrain in `pyproject.toml`. Upgrade pip in Dockerfile.
**Status:** Deferred — needs test coverage on filelock update; schedule for routine dependency bump.

---

## Minimum Sweep Results

| Check | Result |
|-------|--------|
| All `/admin/*` routes call `_admin_auth` | ✅ Pass |
| All `/chat/*` routes call `_require_chat_team` (except login/logout/page) | ✅ Pass |
| `shell=True` / `os.system` usage | ✅ None found |
| `eval()` / `exec()` usage | ✅ None found |
| `pickle` usage | ✅ None found |
| `yaml.load` (unsafe) usage | ✅ None found |
| SQL parameterization | ✅ All user input bound via `?`; f-string WHERE clauses use whitelisted fragments only |
| Tracked secret files (`.env`, `.key`, `.pem`) | ✅ None found |
| Live-looking API keys in tracked files (`sk-`, `AKIA`, `ghp_`) | ✅ None found |
| Token generation uses `secrets` module | ✅ `teams.py:117` (team tokens), `chat.py:62` (conversation IDs) |
| `subprocess` uses `create_subprocess_exec` (not shell) | ✅ All provider CLIs use `exec` form |

---

## Positive Observations

- All admin routes consistently call `_admin_auth` before any data access.
- Team token generation uses `secrets.token_hex(16)` (cryptographically strong).
- SQL throughout the app uses parameterized queries; f-string WHERE clauses are assembled only from whitelisted string literals, never user input.
- No shell=True, os.system, eval, exec, pickle, or unsafe yaml.load patterns anywhere in the codebase.
- No tracked secrets or live API keys found.
- `_team_to_redacted_dict` deliberately truncates team bearer tokens in list responses to prevent log/shoulder-surfing leakage.
- Budget enforcement on spend caps fires before routing to avoid surprise overruns.

---

## Remediation Status

| ID  | Description                            | Status   | Branch                       |
|-----|----------------------------------------|----------|------------------------------|
| H-1 | Timing-safe admin token comparison     | ✅ Fixed  | security/audit-2026-11-02    |
| M-1 | Dockerfile non-root user               | ✅ Fixed  | security/audit-2026-11-02    |
| M-2 | `/v1/models` auth gate                 | Deferred | Proposed: add `_client_key_allowed` |
| M-3 | `/metrics` auth gate                   | Deferred | Proposed: add `_admin_auth` or separate port |
| M-4 | docker-compose loopback bind           | Deferred | Proposed: `127.0.0.1:8000:8000` |
| L-1 | Session cookie `Secure` flag           | Deferred | Proposed: `CLEARVIEW_SECURE_COOKIES` env var |
| L-2 | Dependency CVEs (filelock, pip, setuptools) | Deferred | Proposed: pin ≥ fix versions |
