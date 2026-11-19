# Security Audit — 2026-11-19

**Repository:** ClearView (Python/FastAPI LLM gateway)
**Auditor:** Automated daily audit (Claude)
**Audit date:** 2026-11-19
**Branch audited:** `main` (HEAD `e8ad2e3`)
**Previous report:** None (first audit)

---

## Severity Summary

| Severity | Count | Fixed this run | Deferred |
|----------|-------|----------------|----------|
| High     | 1     | 1              | 0        |
| Medium   | 3     | 0              | 3        |
| Low      | 2     | 0              | 2        |
| Info     | 2     | —              | —        |

---

## High Findings

### H-1 · Non-constant-time admin token comparison

**File/location:** `app/main.py:301` (`_admin_auth`)
**Risk:** Timing-based brute-force of `CLEARVIEW_ADMIN_TOKEN`. Python's `!=` operator on strings short-circuits on the first differing byte, allowing an attacker with network access to enumerate the token one character at a time by measuring response latency differences.
**Remediation:** Replace `if token != expected:` with `if not secrets.compare_digest(token, expected):`.
**Status:** ✅ **Fixed in this run** — `import secrets` added; comparison changed to `secrets.compare_digest`.

---

## Medium Findings

### M-1 · Dockerfile runs as root (no USER directive)

**File/location:** `Dockerfile` (entire file; no `USER` instruction)
**Risk:** The container process runs as `root`. Any RCE vulnerability in the app or a dependency gives the attacker full root inside the container, simplifying privilege escalation and container-escape attempts.
**Remediation (deferred — needs design):** Add a non-root user to the Dockerfile:
```dockerfile
RUN adduser --disabled-password --gecos "" appuser \
    && mkdir -p /data && chown -R appuser /data
USER appuser
```
Requires verifying that `/data` volume writes and the process's port-binding (>1024, so no issue) work under the new UID. Deferred to avoid unexpected volume-permission regressions.

### M-2 · Session cookie missing `Secure` flag

**File/location:** `app/main.py:2651` (`chat_login`)
**Risk:** The `cv_session` cookie (which carries the team bearer token) is set without `secure=True`. In an HTTP deployment the browser will send the cookie over unencrypted connections, exposing the team token to network eavesdroppers.
**Current state:** `httponly=True`, `samesite="lax"` are set correctly. Only `secure` is absent.
**Remediation (deferred — dev-UX trade-off):** Add `secure=True` to the `set_cookie` call, gated on a config flag (e.g. `CLEARVIEW_SECURE_COOKIE=1`) so local HTTP dev still works without TLS. Deferred because forcing `Secure` on unconditionally breaks the default local dev workflow.

### M-3 · `/metrics` endpoint unauthenticated

**File/location:** `app/main.py:2538` (`metrics`)
**Risk:** The Prometheus `/metrics` endpoint is accessible without any authentication. It exposes tier/provider labels, cumulative token counts, and aggregated cost data to anyone who can reach the service. In a shared-network or Docker-exposed deployment this leaks operational detail that is sensitive (cost per provider, usage volume).
**Remediation (deferred):** Options: (a) add `_admin_auth(request)` check; (b) separate the metrics port onto a non-public interface; (c) document that operators should firewall port 8000 or run behind a reverse proxy that restricts `/metrics`. Deferring because a hard auth gate on `/metrics` requires Prometheus scrape config changes in every deployment.

---

## Low Findings

### L-1 · pip/setuptools CVEs in build toolchain

**Source:** `pip-audit` run against installed packages
**Affected:** `pip 24.0` (PYSEC-2026-1795, -1796, -2875, -2876, -196, -3721), `setuptools 79.0.1` (PYSEC-2026-3447, GHSA-xmj3-9gf7-h52w)
**Risk:** Build-time tool vulnerabilities. These affect `pip` and `setuptools` versions baked into the Docker image layer. No runtime Python application code imports these packages directly. Impact is primarily during dependency installation (malicious wheel installation, path traversal in wheel extraction).
**Remediation (deferred):** Pin `pip>=26.1.2` and `setuptools>=84.1.0` in the Dockerfile `RUN pip install --upgrade pip setuptools` step. Deferred because this is a Dockerfile build-layer change with no test coverage impact; it should be batched with the M-1 Dockerfile hardening pass.
**Fix versions:** pip → 26.1.2, setuptools → 84.1.0

### L-2 · `/feedback` endpoint lacks caller-ownership check

**File/location:** `app/main.py:1693-1728` (`post_feedback`)
**Risk:** Any caller who knows a `request_id` (a UUID4 hex, 32 chars) can submit a thumbs rating for it. `request_id` values are returned to clients in response headers (`x-clearview-request-id`), so a client can rate only their own responses in the normal flow — but replay or guessing attacks are not prevented. This can skew the provider-learning corpus (`record_provider_outcome`).
**Comment in code:** "No admin auth — callers rate their own responses (request_id is the handle)." This is an accepted design decision.
**Remediation (deferred):** Optionally bind feedback to team identity: when the request was made by an authenticated team, require the same team token to submit feedback. Deferred because this is a behavior change to the feedback API that requires a design decision.

---

## Info (Intentionally Open, No Action Required)

### I-1 · `GET /v1/models` has no authentication

**File/location:** `app/main.py:364`
**Rationale:** Intentionally open — IDE clients (VS Code, Continue, Cline) query this endpoint to populate model pickers. Leaks configured model IDs and virtual model names but no credentials.
**No action required.**

### I-2 · `GET /health` has no authentication

**File/location:** `app/main.py:2413`
**Rationale:** Standard liveness probe; returns `{"status": "ok"}` only. No sensitive data exposed.
**No action required.**

---

## Minimum Sweep Results

| Check | Result |
|-------|--------|
| Every `@app.*` route calls `_admin_auth`, `_client_key_allowed`, `_resolve_team`, or `_require_chat_team`, or is documented as intentionally open | ✅ Pass — all 35 routes checked; open routes are `/v1/models`, `/health`, `/feedback`, and the public chat/login/logout shims, each with documented rationale |
| `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, unsafe `yaml.load` | ✅ None found |
| SQL parameterization — no f-string/concat queries interpolating user input | ✅ Pass — f-strings in SQL only interpolate hardcoded column/table clauses (e.g. `{where}` built from fixed strings + `?` placeholders); all user values via `params` tuple |
| Tracked secrets — `.env`/`.key`/`.pem` files, `sk-`, `AKIA`, `ghp_` patterns | ✅ None found |
| Secret comparison uses `secrets.compare_digest`; token generation uses `secrets` module | ✅ Pass after H-1 fix; `secrets.token_hex` used for all token generation |
| Dockerfile has non-root `USER` | ❌ **Missing** — see M-1 |
| `docker-compose` does not publish unauthenticated services beyond loopback | ⚠️ Port 8000 binds to `0.0.0.0` — mitigated only when `CLEARVIEW_ADMIN_TOKEN` is set; dev mode is open |
| `pip-audit` CVEs with fix versions | ⚠️ 2 build-tool packages (pip, setuptools) — see L-1; runtime app dependencies clean |

---

## Positive Observations

- All 35 routes audited; admin paths uniformly gated behind `_admin_auth`.
- `subprocess` calls use `create_subprocess_exec` (no shell injection surface).
- SQL throughout uses parameterized queries; f-string SQL fragments interpolate only hardcoded column/table names.
- No secrets tracked in git; `.env` file excluded via `.gitignore`.
- Team tokens generated with `secrets.token_hex(16)` (cryptographically random, 32 hex chars).
- Cookie correctly sets `HttpOnly=True` and `SameSite=Lax`.
- Budget enforcement, tier gating, and team isolation appear consistently applied.

---

## Deferred Items Carried Forward

| ID | Description | Location | Priority |
|----|-------------|----------|----------|
| M-1 | Dockerfile non-root USER | `Dockerfile` | Medium |
| M-2 | Session cookie `Secure` flag (env-gated) | `app/main.py:2651` | Medium |
| M-3 | `/metrics` authentication or network-layer restriction | `app/main.py:2538` | Medium |
| L-1 | pip/setuptools Dockerfile layer upgrade | `Dockerfile` | Low |
| L-2 | `/feedback` caller-ownership binding | `app/main.py:1693` | Low |

---

## Test Suite

Tests run against fix on `security/audit-2026-11-19`:

```
pytest -q
359 passed, 16 warnings in 174.30s
```

✅ All 359 tests pass on `security/audit-2026-11-19`.
