# ClearView Security Audit — 2026-09-12

**Auditor:** Automated daily security audit (Claude Sonnet 4.6)
**Branch audited:** `main` (HEAD: e8ad2e3)
**Prior audit:** None (first run)
**Deferred items carried over:** None

---

## Severity Summary

| Severity | New | Fixed in this PR | Deferred | Carried Over |
|----------|-----|-----------------|----------|--------------|
| High     | 1   | 1               | 0        | 0            |
| Medium   | 3   | 1               | 2        | 0            |
| Low      | 2   | 0               | 2        | 0            |
| **Total**| **6** | **2**         | **4**    | **0**        |

---

## High Findings

### H1 — Timing side-channel in `_admin_auth` token comparison ✅ FIXED

- **File:** `app/main.py:302`
- **Risk:** The admin token comparison used Python's `!=` operator (`if token != expected`). CPython's string comparison can short-circuit on the first differing byte, creating a measurable timing oracle. An attacker with network access to `/admin/*` (or any route protected by `_admin_auth`) and the ability to make many requests can enumerate the token character-by-character via statistical timing analysis.
- **Remediation applied:** Replaced with `secrets.compare_digest(token, expected)` and added `import secrets` to the module.
- **Status:** Fixed — `secrets` module imported; constant-time comparison enforced.

---

## Medium Findings

### M1 — Dockerfile runs as root (no non-root USER) ✅ FIXED

- **File:** `Dockerfile`
- **Risk:** Without a `USER` directive, the container process runs as `root` (uid 0). A container-escape or path-traversal vulnerability would give the attacker root on the host kernel's namespace. Defense-in-depth requires a non-privileged UID.
- **Remediation applied:** Added `RUN addgroup --system cv && adduser --system --ingroup cv cv` plus `chown cv:cv /data`, followed by `USER cv` before `CMD`. The data volume (`/data`) is pre-created and owned by the new user so the app can write its SQLite databases on first run.
- **Status:** Fixed.

### M2 — `cv_session` cookie missing `Secure` flag ⚠️ DEFERRED

- **File:** `app/main.py:2651–2656` (`chat_login`)
- **Risk:** The session cookie carrying the team bearer token is set without `secure=True`. Browsers transmit cookies without the `Secure` attribute over plain-HTTP connections, making the session token capturable by a network observer (MITM, shared-network sniffing) when the chat UI is accessed over HTTP.
- **Deferred reason:** Adding `secure=True` breaks the test suite (httpx's cookie jar correctly refuses to send Secure cookies back to `http://` test-client URLs), and — more importantly — it would also break the intended local-dev workflow (users running ClearView over plain HTTP on localhost). A safe implementation requires either: (a) detecting whether the request arrived over HTTPS (via `X-Forwarded-Proto` or TLS detection) and only setting `Secure` on HTTPS connections, or (b) introducing a `CLEARVIEW_SECURE_COOKIES=1` env-var flag that operators enable when deploying behind a TLS terminator.
- **Proposed design:** Check `request.url.scheme == "https"` (or `request.headers.get("x-forwarded-proto") == "https"`) and add `secure=True` only when over HTTPS. Alternatively, read `CLEARVIEW_SECURE_COOKIES` env var; default off. This is a one-line conditional fix once the approach is agreed on.

### M3 — `/metrics` Prometheus endpoint unauthenticated ⚠️ DEFERRED

- **File:** `app/main.py:2538`
- **Risk:** The `/metrics` endpoint is open with no authentication gate. It exposes request counts broken down by tier, provider, team ID, and status, plus cumulative cost figures and drift percentages. An external party with access to the port can observe per-team usage patterns, identify which providers are configured, and watch real-time spend.
- **Impact:** Telemetry and cost data disclosure to unauthenticated clients with network access.
- **Deferred reason:** Adding authentication to `/metrics` would break all existing Prometheus scraper configurations (operators must supply a bearer to the scraper). This requires a documented breaking change and migration path rather than a silent fix. The `/metrics` endpoint binding is already restricted to `127.0.0.1` in the default `.env.example` config (`CLEARVIEW_HOST=127.0.0.1`), so exposure is limited to deployments that intentionally bind to `0.0.0.0` (e.g., Docker, shared hosts).
- **Proposed design:** Add an optional `CLEARVIEW_METRICS_TOKEN` env var; when set, `/metrics` requires `Authorization: Bearer <token>`. When unset, default open (backward-compatible). Gate addition behind the existing `_admin_auth` function pattern, which already handles the opt-in case.

---

## Low Findings

### L1 — `pip` 24.0 CVEs in build tooling ⚠️ DEFERRED

- **Packages:** `pip==24.0` in `.venv` (build/dev tooling only)
- **CVEs:** PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-196, PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-3721
- **Fix versions:** pip ≥ 26.2
- **Risk:** These affect the `pip` version used for installation only, not the running application. Exploitability requires an attacker who can influence package installation (e.g., supply-chain attack during `docker build`). Not a runtime risk.
- **Deferred reason:** Build tooling; no application runtime exposure. Recommend upgrading pip in CI/Dockerfile with `RUN pip install --upgrade pip` if the build environment is exposed to untrusted package sources.

### L2 — `setuptools` 79.0.1 CVE in build tooling ⚠️ DEFERRED

- **Package:** `setuptools==79.0.1` in `.venv`
- **CVE:** PYSEC-2026-3447
- **Fix version:** setuptools ≥ 83.0.0
- **Risk:** Same category as L1 — build-time only. Not a running service vulnerability.
- **Deferred reason:** Build tooling. Upgrade alongside pip on the next dependency refresh.

---

## Minimum Sweep Checklist

| Check | Result |
|-------|--------|
| (a) Route auth gates | ✅ All `@app.get/post/delete` admin routes call `_admin_auth`; all `/chat/*` routes call `_require_chat_team`; `/v1/chat/completions`, `/v1/messages`, `/v1/responses`, and Gemini shim routes all call `_client_key_allowed` + `_resolve_team`; `/feedback` intentionally open (documented); `/health` and `/metrics` open (see M3 for metrics) |
| (b) Dangerous exec patterns | ✅ No `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, or unsafe `yaml.load` in `app/`. All subprocess calls use `asyncio.create_subprocess_exec` with argument lists — no shell injection surface. |
| (c) Parameterized SQL | ✅ All SQL queries use `?` placeholders for user-supplied values. The dynamic `sets` list in `teams.update()` consists of hardcoded column-assignment strings, not user input; values still flow through parameters. |
| (d) Tracked secrets | ✅ No `.env`, `.key`, `.pem`, or credential files tracked in git. Test files use clearly dummy placeholders (`sk-x`, `sk-test`). |
| (e) Token generation / constant-time compare | ✅ (post-fix) Token generation uses `secrets.token_hex()`. `_admin_auth` now uses `secrets.compare_digest()`. |
| (f) Dockerfile non-root / docker-compose exposure | ✅ (post-fix) Dockerfile now has non-root `USER cv`. `docker-compose.yml` publishes port 8000 on all interfaces but this is expected for a gateway; Ollama is gated behind the `local` profile and its port is not published by default. |
| (g) pip-audit CVEs | ⚠️ 14 CVEs found in `pip` 24.0 and `setuptools` 79.0.1 — both build-time tooling only. See L1/L2. No CVEs found in runtime application dependencies. |

---

## Positive Observations

- Team token generation correctly uses `secrets.token_hex(16)` (40 hex chars, 128 bits entropy).
- Chat conversation IDs use `secrets.token_hex(8)` — adequate for non-secret identifiers.
- The `_resolve_team` function caches the resolved team on `request.state` to avoid duplicate DB lookups and prevent TOCTOU races.
- `_admin_auth` is called consistently on every `/admin/*` route (verified by code review of all 17 admin endpoints).
- The `cv_session` cookie was already set with `httponly=True` and `samesite="lax"`, providing XSS and CSRF protection.
- Token redaction is implemented in team list responses: `_team_to_redacted_dict` only returns the first 18 chars of the token; the full token is returned only on creation.
- SQL queries use parameterized placeholders throughout `app/telemetry.py`, `app/teams.py`, `app/chat.py`, and `app/cache.py`.
- No `.env` or secret files are tracked; `.gitignore` excludes `.env` and the SQLite database.

---

## Remediation Status

| Finding | Status | Branch |
|---------|--------|--------|
| H1 — timing compare in `_admin_auth` | Fixed | `security/audit-2026-09-12` |
| M1 — Dockerfile runs as root | Fixed | `security/audit-2026-09-12` |
| M2 — cookie missing `Secure` flag | Deferred — proposed design above | — |
| M3 — `/metrics` unauthenticated | Deferred — proposed design above | — |
| L1 — pip CVEs | Deferred — build tooling only | — |
| L2 — setuptools CVE | Deferred — build tooling only | — |
