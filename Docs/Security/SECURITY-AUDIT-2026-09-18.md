# Security Audit — 2026-09-18

**Auditor:** Automated daily audit (Claude Code scheduled task)
**Branch audited:** `main` (HEAD `e8ad2e3`)
**Audit branch:** `security/audit-2026-09-18`
**Previous report:** None (first audit)

---

## Severity Summary

| Severity | New | Fixed this run | Deferred |
|----------|-----|---------------|----------|
| High     | 0   | 0             | 0        |
| Medium   | 3   | 2             | 1        |
| Low      | 4   | 0             | 4        |
| **Total**| **7**| **2**        | **5**    |

---

## Findings

### Medium

#### M-1 — Non-constant-time admin token comparison (FIXED)

**Location:** `app/main.py:301` — `_admin_auth()`
**Risk:** `token != expected` is a plain string equality check. An attacker making many requests could exploit the timing difference between early-exit on mismatch vs full comparison to brute-force the admin token one character at a time. Exploitable when the gateway is internet-accessible and `CLEARVIEW_ADMIN_TOKEN` is set.
**Remediation:** Replaced with `secrets.compare_digest(token, expected)`, which runs in constant time regardless of where strings diverge.
**Status:** ✅ Fixed in this run (`app/main.py` — `import secrets` added, comparison updated).

---

#### M-2 — Dockerfile runs container as root (FIXED)

**Location:** `Dockerfile`
**Risk:** No `USER` directive means the Python process (and any process it spawns, including the CLI providers) runs as root inside the container. If an attacker achieves code execution inside the container, they have root, making container escapes easier and giving full write access to any mounted volume.
**Remediation:** Added `RUN groupadd -r clearview && useradd -r -g clearview clearview && mkdir -p /data && chown clearview:clearview /data` followed by `USER clearview` before `VOLUME` and `CMD`.
**Status:** ✅ Fixed in this run (`Dockerfile`).

---

#### M-3 — `/metrics` endpoint unauthenticated (DEFERRED)

**Location:** `app/main.py:2538` — `GET /metrics`
**Risk:** The Prometheus metrics endpoint returns request counts, cost totals, tier and provider breakdowns, and token totals — without requiring any authentication. An unauthenticated caller can enumerate which LLM providers are in use and monitor spend rates. This is information disclosure, not data exfiltration, but it exposes operational topology.
**Proposed design:** Gate `/metrics` with `_admin_auth` when `CLEARVIEW_ADMIN_TOKEN` is set, controlled by a `CLEARVIEW_METRICS_OPEN` env var (default `0`) so Prometheus scrapers that use a separate bearer token continue to work. Adding mandatory auth would break existing scrapers — deferred for operator sign-off.
**Status:** ⏳ Deferred — behavior-changing; requires operator sign-off.

---

### Low

#### L-1 — docker-compose binds to all interfaces (DEFERRED)

**Location:** `docker-compose.yml:8` — `ports: - "8000:8000"`
**Risk:** Docker publishes port 8000 on `0.0.0.0` by default. On a multi-tenant host this exposes the gateway to the local network and any container on the host with routed access. On a VPS, it bypasses the host firewall (Docker manipulates iptables directly).
**Proposed design:** Change to `127.0.0.1:8000:8000` and document that a reverse proxy (nginx/caddy) should front the service for production. Deferred because many single-machine deployments intentionally expose the port for IDE integrations.
**Status:** ⏳ Deferred — intentionally open in single-host dev mode; operator decision.

---

#### L-2 — Session cookie missing `Secure` flag (DEFERRED)

**Location:** `app/main.py:2649-2655` — `chat_login()` / `resp.set_cookie(...)`
**Risk:** `cv_session` cookie is set with `httponly=True, samesite="lax"` but without `secure=True`. If the service is accessed over HTTPS and the client is later redirected to HTTP (e.g. during a downgrade), the browser will send the session token in cleartext.
**Proposed design:** Set `secure=True` when a `CLEARVIEW_HTTPS=1` env var is set (or derive from `X-Forwarded-Proto` header). Default remains insecure to preserve localhost HTTP dev workflow.
**Status:** ⏳ Deferred — localhost HTTP dev use case; safe to defer until HTTPS deployment guidance is added.

---

#### L-3 — pip 24.0 — multiple CVEs in build tooling (DEFERRED)

**Location:** Docker build environment / `.venv`
**Risk:** pip 24.0 has 6 known vulnerabilities (PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-196, PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-3721). Fix: upgrade to `pip>=26.2`. These are build-tool vulnerabilities, not runtime library vulnerabilities — the running gateway does not call pip after startup. Risk is limited to the build stage or developer machines running pip installs.
**Remediation:** Add `RUN pip install --upgrade pip` to the Dockerfile (or pin `pip>=26.2` in build toolchain). Low operational priority.
**Status:** ⏳ Deferred — build-tool only; not in runtime path.

---

#### L-4 — setuptools 79.0.1 — CVE in build tooling (DEFERRED)

**Location:** Docker build environment / `.venv`
**Risk:** setuptools 79.0.1 has PYSEC-2026-3447. Fix: upgrade to `setuptools>=83.0.0`. Same scope as L-3 — build-time only.
**Status:** ⏳ Deferred — build-tool only; not in runtime path.

---

## Carried-Over Deferred Findings

None — this is the first audit.

---

## Positive Observations

- **No injection vectors found.** No `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, or unsafe `yaml.load(` usage across the codebase. All CLI subprocess calls use `asyncio.create_subprocess_exec` with a fixed, user-input-free argument list; user data flows only over stdin.
- **All SQL is parameterized.** Every SQLite query in `app/telemetry.py`, `app/teams.py`, `app/cache.py`, and `app/chat.py` uses `?` placeholders. No f-string or concatenation SQL found.
- **No tracked secrets.** `.env` is in `.gitignore`. No live API keys (`sk-`, `AKIA`, `ghp_`) found in tracked files.
- **Secure token generation.** Team bearer tokens use `secrets.token_hex(16)` and chat IDs use `secrets.token_hex(8)`.
- **YAML uses `safe_load`.** `app/config.py` calls `yaml.safe_load`, not `yaml.load`.
- **Cookie hardened (partially).** `cv_session` uses `httponly=True` (XSS-safe) and `samesite="lax"` (CSRF-safe). Only `secure=True` is missing (L-2).
- **Admin routes consistently gated.** All `GET /admin/*` and `POST /admin/*` routes call `_admin_auth`. All `/chat/*` routes call `_require_chat_team`. Open routes (`/v1/models`, `/health`, `/feedback`) are intentionally documented as open.
- **Client key comparison uses set lookup.** `_client_key_allowed` checks `cand in allowed` against a Python `set`. While not cryptographically constant-time, client keys are operator-configured static values with low brute-force surface. The higher-risk admin token comparison is now fixed (M-1).

---

## Remediation Status

| ID  | Finding                                | Severity | Status  | Commit |
|-----|----------------------------------------|----------|---------|--------|
| M-1 | Non-constant-time admin token compare  | Medium   | Fixed   | this PR |
| M-2 | Dockerfile runs as root                | Medium   | Fixed   | this PR |
| M-3 | `/metrics` unauthenticated             | Medium   | Deferred | needs operator design |
| L-1 | docker-compose all-interface binding   | Low      | Deferred | operator decision |
| L-2 | Cookie missing `Secure` flag           | Low      | Deferred | needs HTTPS env var |
| L-3 | pip 24.0 CVEs (build tool)             | Low      | Deferred | upgrade pip in Dockerfile |
| L-4 | setuptools 79.0.1 CVE (build tool)     | Low      | Deferred | upgrade setuptools in Dockerfile |
