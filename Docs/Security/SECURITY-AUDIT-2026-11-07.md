# Security Audit — 2026-11-07

**Auditor:** Claude Code (automated daily audit)
**Branch audited:** `main` @ `e8ad2e3`
**Previous report:** None (first audit)
**Carried-over deferred items:** None

---

## Severity Summary

| Severity | New | Fixed this run | Deferred |
|----------|-----|----------------|----------|
| High     | 1   | 1              | 0        |
| Medium   | 4   | 1              | 3        |
| Low      | 2   | 0              | 2        |

---

## High Findings

### H-1 · Non-constant-time admin token comparison — **FIXED**

| Field      | Value |
|------------|-------|
| File       | `app/main.py:301` |
| Function   | `_admin_auth` |
| Risk       | Timing side-channel: an attacker on a low-latency link can brute-force `CLEARVIEW_ADMIN_TOKEN` one byte at a time by measuring response duration. |
| Remediation | Replace `token != expected` with `not secrets.compare_digest(token, expected)`. Added `import secrets` to imports. |
| Status     | **Fixed in this run** (commit on branch `security/audit-2026-11-07`) |

---

## Medium Findings

### M-1 · Session cookie missing `secure` flag — **FIXED**

| Field      | Value |
|------------|-------|
| File       | `app/main.py:2652–2656` (post-fix: 2652–2657) |
| Function   | `chat_login` |
| Risk       | Without `secure=True`, the `cv_session` team-token cookie is transmitted over plain HTTP. An on-path attacker or HTTP downgrade can capture team bearer tokens. |
| Remediation | Added `secure=True` to `set_cookie()`. Browsers exempt `localhost` from the secure restriction, so local development is unaffected. |
| Status     | **Fixed in this run** |

### M-2 · Dockerfile runs as root — **DEFERRED**

| Field      | Value |
|------------|-------|
| File       | `Dockerfile` |
| Risk       | The container runs as `root` (no `USER` directive). An RCE vulnerability would grant full root within the container, and volume-mounted data would be owned by root. |
| Remediation | Add `RUN useradd -r -u 1001 appuser && chown -R appuser /app /data` and `USER appuser` before `CMD`. Requires testing that the `/data` SQLite volume is writable by the new user. |
| Status     | **Deferred** — behavior-changing (may break existing volume mounts); requires a migration plan for existing deployments. |

### M-3 · `/metrics` endpoint unauthenticated — **DEFERRED**

| Field      | Value |
|------------|-------|
| File       | `app/main.py:2538` |
| Risk       | The Prometheus `/metrics` endpoint is publicly accessible with no authentication. It leaks team IDs, provider names, tier usage counts, cumulative cost, and output token totals. While not directly exploitable, it reveals operational intelligence (which teams exist, which providers are active, approximate spend). |
| Remediation | Gate behind `_admin_auth` or a separate `CLEARVIEW_METRICS_TOKEN` env var, with documentation for Prometheus `bearer_token` scrape config. |
| Status     | **Deferred** — adding auth would break existing Prometheus scrapers; requires a proposed design and operator communication before merging. |

### M-4 · `filelock` CVEs (13 CVEs, fix: 4.2.1) — **DEFERRED**

| Field      | Value |
|------------|-------|
| Packages   | `filelock==3.32.7` (transitive via `litellm`) |
| CVEs       | CVE-2026-112940, CVE-2026-112913, CVE-2026-112907, CVE-2026-112938, CVE-2026-112936, CVE-2026-112910, CVE-2026-112909, CVE-2026-112911, CVE-2026-112939, CVE-2026-112915, CVE-2026-112935, CVE-2026-112912, CVE-2026-112908 |
| Fix        | `filelock>=4.2.1` (all CVEs); `filelock>=4.0.9` (CVE-2026-112936) |
| Risk       | Medium — `filelock` is used by `litellm` (a core production dependency via `huggingface_hub`). CVE details indicate lock file manipulation / TOCTOU issues. |
| Remediation | Pin `filelock>=4.2.1` in `pyproject.toml` or wait for `litellm` to update its own constraint. Run `pip install "filelock>=4.2.1"` to verify compatibility. |
| Status     | **Deferred** — dependency update; requires validating `litellm` compatibility with `filelock` 4.x. |

---

## Low Findings

### L-1 · `pip` and `setuptools` CVEs (environment tools) — **DEFERRED**

| Field      | Value |
|------------|-------|
| Packages   | `pip==24.0` (10 CVEs, fix: 26.2), `setuptools==79.0.1` (3 CVEs, fix: 84.1.0) |
| Risk       | Low — these are build/environment tools, not application runtime dependencies. They affect the build environment and developer machines, not running containers (which install a pinned wheel). |
| Remediation | Upgrade `pip` and `setuptools` in developer environments and CI: `pip install --upgrade pip setuptools`. Consider adding a `requirements-dev.txt` or Dependabot config for environment tooling. |
| Status     | **Deferred** — low runtime impact; addressed via developer tooling updates. |

### L-2 · Admin routes open by default (no `CLEARVIEW_ADMIN_TOKEN` set) — **DEFERRED**

| Field      | Value |
|------------|-------|
| File       | `app/main.py:292–302` (`_admin_auth`), all `/admin/*` routes |
| Risk       | Low (by design for local dev) — When `CLEARVIEW_ADMIN_TOKEN` is not set, all admin endpoints are publicly accessible. This is intentional for single-user local deployment, but a misconfigured production instance would expose team management, telemetry, and tuning controls. |
| Remediation | Add a startup warning when `CLEARVIEW_ADMIN_TOKEN` is not set (e.g., log a `WARNING` in `lifespan`). Consider requiring the token in Docker deployments via documentation or a startup guard. |
| Status     | **Deferred** — intentional design for local dev; a startup warning is a low-risk addition but still a behavior change. |

---

## Carried-Over Deferred Items

None (first audit).

---

## Positive Observations

- **Secret generation** uses `secrets.token_hex` for all team and conversation IDs (`app/teams.py:117`, `app/chat.py:62`). No `random` or `uuid4` for security-sensitive tokens.
- **SQL queries** are consistently parameterized. The two f-string queries in `app/teams.py` and `app/telemetry.py` interpolate only hardcoded column-name strings, never user input — no SQL injection risk.
- **Subprocess calls** use `asyncio.create_subprocess_exec` (not `shell=True`) with user data passed via `stdin`, not as command-line arguments. No command injection risk in `app/providers/`.
- **No hardcoded secrets** in tracked files. Test files use clearly synthetic values (`sk-x`, `sk-test`) that cannot be real credentials.
- **Admin token redaction**: the `GET /admin/teams` list endpoint returns only a `id_short` prefix, preventing full bearer token exposure in operator logs or the UI.
- **Team token validation**: `_resolve_team` uses a database lookup with a 401 on unknown/disabled teams, preventing token enumeration.
- **CSRF partial protection**: session cookie uses `SameSite=Lax`, limiting cross-site request forgery in most browser scenarios.
- **No `shell=True`, `eval()`, `exec()`, `pickle`, or unsafe `yaml.load()`** found in application code.

---

## Remediation Status Summary

| ID  | Severity | Finding                               | Status  |
|-----|----------|---------------------------------------|---------|
| H-1 | High     | Non-constant-time admin token compare | Fixed   |
| M-1 | Medium   | Session cookie missing `secure` flag  | Fixed   |
| M-2 | Medium   | Dockerfile runs as root               | Deferred|
| M-3 | Medium   | `/metrics` unauthenticated            | Deferred|
| M-4 | Medium   | `filelock` CVEs                       | Deferred|
| L-1 | Low      | `pip`/`setuptools` CVEs               | Deferred|
| L-2 | Low      | Admin open by default (no-op warning) | Deferred|
