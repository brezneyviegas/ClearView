# ClearView Security Audit — 2026-09-05

**Auditor:** Claude Code automated daily audit  
**Branch audited:** `main` (HEAD: e8ad2e3)  
**Previous report:** None (first audit)  
**Audit window:** Full codebase sweep (no prior baseline)

---

## Severity Summary

| Severity | New | Fixed This Run | Deferred | Total Open |
|----------|-----|----------------|----------|------------|
| High     |  0  |       0        |    0     |     0      |
| Medium   |  2  |       2        |    0     |     0      |
| Low      |  2  |       0        |    2     |     2      |

---

## Findings

### Medium

#### M-1 — Non-constant-time admin token comparison (FIXED)

- **Location:** `app/main.py:301`, `_admin_auth()`
- **Risk:** When `CLEARVIEW_ADMIN_TOKEN` is set, the comparison `token != expected` is a plain string equality check. This leaks information through timing: an attacker who can make many requests can distinguish a prefix-correct guess from a fully wrong one, enabling a character-by-character brute-force of the token.
- **Remediation:** Replace with `secrets.compare_digest(token, expected)` which runs in constant time regardless of where the strings differ.
- **Status:** ✅ Fixed in this run.

#### M-2 — Dockerfile runs as root (FIXED)

- **Location:** `Dockerfile` (no `USER` directive)
- **Risk:** The container process runs as UID 0 (root). If an attacker achieves code execution inside the container they have root inside it, making container-escape attacks and filesystem manipulation easier.
- **Remediation:** Add `USER nobody` before the `CMD` line.
- **Status:** ✅ Fixed in this run.

---

### Low

#### L-1 — Session cookie missing `Secure` flag (DEFERRED)

- **Location:** `app/main.py:2651–2656`, `chat_login()`
- **Risk:** The `cv_session` cookie carrying the team Bearer token is set without `secure=True`. If the deployment operates over plain HTTP (e.g. a local network behind a TLS-terminating proxy that doesn't enforce HTTPS), the cookie can be intercepted by a network observer. The token in the cookie is a full bearer credential.
- **Remediation:** Add `secure=True` to the `set_cookie()` call and document that HTTPS is required. If HTTP is needed for local dev, gate the flag on an env var (e.g. `CLEARVIEW_COOKIE_SECURE=1`).
- **Reason deferred:** Unconditionally adding `secure=True` would break development environments and test suites that run over plain HTTP. This requires a design decision (env-var gate vs. always-on). Proposed design: read `CLEARVIEW_COOKIE_SECURE` (default `"0"`) and set the flag accordingly; document in README that HTTPS deployments should set it to `"1"`.

#### L-2 — CVEs in build-tool packages (`pip`, `setuptools`) (DEFERRED)

- **Location:** `.venv/` (runtime venv)
- **Risk:** `pip 24.0` has 7 known CVEs (fix: ≥ 26.1.2); `setuptools 79.0.1` has 1 CVE (PYSEC-2026-3447, fix: 83.0.0). These are build / install-time tools, not application runtime dependencies — they are not loaded by the running ClearView server. The CVEs relate to package installation security, not to the gateway itself.
- **Remediation:** Upgrade the pip and setuptools in the build / CI environment: `pip install --upgrade pip setuptools`.
- **Reason deferred:** Build-tool upgrades need CI validation and are out of scope for an in-process code fix.

---

## Carried-Over Deferred Findings

None (first audit).

---

## Checklist Results

| Check | Result |
|---|---|
| Auth coverage — all admin routes call `_admin_auth` | ✅ Pass |
| Auth coverage — all chat routes call `_require_chat_team` | ✅ Pass |
| Public API routes intentionally open (`/v1/chat/completions`, `/v1/models`, `/health`, `/metrics`, `/feedback`) | ✅ Documented intent |
| `shell=True` / `os.system` / `eval(` / `exec(` / `pickle` / `unsafe yaml.load` | ✅ None found |
| SQL queries parameterized (no f-string value interpolation) | ✅ Pass — f-strings used only for WHERE-clause structure with `?` placeholders |
| No live secrets tracked in git | ✅ Pass — only `.env.example` and `clearview-client.env.example` (example files) |
| Token generation uses `secrets` module | ✅ Pass — `secrets.token_hex(16)` in `teams.create()`, `secrets.token_hex(8)` in `chat.py` |
| Secret comparisons use `secrets.compare_digest` | ✅ Fixed this run (was `!=` in `_admin_auth`) |
| Dockerfile has non-root `USER` | ✅ Fixed this run |
| docker-compose port binding | ℹ️ Port 8000 exposed on all interfaces — expected for a gateway; flag for network-level review in shared-network deployments |
| pip-audit runtime deps | ✅ No CVEs in application runtime dependencies; CVEs only in build tools (`pip`, `setuptools`) |

---

## Positive Observations

- **Auth architecture is consistent.** Every admin endpoint calls `_admin_auth`, every chat endpoint calls `_require_chat_team`/`_resolve_team`. The separation between open LLM-proxy routes and protected admin/chat routes is clearly structured.
- **Secrets module used throughout.** Team token minting (`cv_team_` Bearer tokens) and conversation ID generation both use `secrets.token_hex`.
- **No injection vectors.** No `shell=True`, no `os.system`, no `eval`/`exec`, no unsafe YAML loading, no `pickle`.
- **SQL is parameterized correctly.** Even in admin endpoints that build dynamic WHERE clauses with f-strings, all user-supplied values are bound through SQLite's `?` placeholder mechanism, not interpolated into the query string.
- **Session cookie has HttpOnly and SameSite=Lax.** These reduce XSS cookie-theft risk and CSRF exposure respectively.
- **Team tokens redacted in listing endpoints.** `_team_to_redacted_dict()` only exposes the first 18 chars of the token in the list view, protecting full tokens from shoulder-surf / log exposure.

---

## Remediation Status

| ID  | Description                              | Status    |
|-----|------------------------------------------|-----------|
| M-1 | Constant-time admin token compare        | ✅ Fixed  |
| M-2 | Dockerfile non-root user                 | ✅ Fixed  |
| L-1 | Session cookie `Secure` flag             | ⏸ Deferred (proposed design above) |
| L-2 | CVEs in pip/setuptools (build tools)     | ⏸ Deferred (CI/build environment change) |
