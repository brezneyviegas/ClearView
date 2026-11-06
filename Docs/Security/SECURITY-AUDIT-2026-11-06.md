# ClearView Security Audit — 2026-11-06

**Auditor:** Automated daily audit (Claude Code)
**Branch audited:** `main` (HEAD `e8ad2e3`)
**Remediation branch:** `security/audit-2026-11-06`

---

## Severity Summary

| Severity | New | Fixed in this run | Deferred |
|----------|-----|-------------------|----------|
| High     | 1   | 1                 | 0        |
| Medium   | 4   | 1 (Dockerfile)    | 3        |
| Low      | 1   | 0                 | 1        |

---

## High Findings

### H-1 — Non-constant-time admin token comparison ✅ FIXED

- **File:** `app/main.py:301` (function `_admin_auth`)
- **Risk:** `if token != expected` uses Python string equality, which is not constant-time. An attacker with many requests can mount a timing side-channel to brute-force the `CLEARVIEW_ADMIN_TOKEN` byte-by-byte, bypassing the entire admin API gate.
- **Remediation:** Replaced with `hmac.compare_digest(token, expected)`. Added `import hmac` to the module imports.
- **Status:** Fixed in this commit.

---

## Medium Findings

### M-1 — Dockerfile runs as root ✅ FIXED

- **File:** `Dockerfile`
- **Risk:** The image has no `USER` directive, so the FastAPI process runs as `root` inside the container. A container escape or path traversal vulnerability would immediately yield root privileges on the host.
- **Remediation:** Added `RUN useradd -r -s /bin/false clearview && mkdir -p /data && chown clearview:clearview /data` and `USER clearview` before the CMD.
- **Status:** Fixed in this commit.

### M-2 — Login session cookie missing `Secure` flag ⚠ DEFERRED

- **File:** `app/main.py:2651` (function `chat_login`)
- **Risk:** The `cv_session` cookie is set with `httponly=True, samesite="lax"` but no `secure=True`. Without the `Secure` attribute, the cookie is transmitted in plain HTTP, exposing team tokens to passive network observers (coffee-shop Wi-Fi, ISP inspection).
- **Proposed design:** Add `secure=True` to the `set_cookie` call, and document that ClearView must be deployed behind a TLS-terminating reverse proxy for the `/chat` UI. Alternatively, detect the `X-Forwarded-Proto: https` header at login time and set `Secure` conditionally.
- **Why deferred:** Setting `secure=True` unconditionally breaks HTTP-only deployments (the documented default for `CLEARVIEW_HOST=127.0.0.1`). Needs an operator-acknowledged configuration option or proxy-detection logic.

### M-3 — `/metrics` Prometheus endpoint unauthenticated ⚠ DEFERRED

- **File:** `app/main.py:2538` (`/metrics` route)
- **Risk:** The Prometheus scrape endpoint is open with no auth. It exposes call counts, cost totals, tier/provider breakdowns, and team IDs — enough to infer the deployment's budget, provider choices, and team structure. A scraper or port-scanning attacker on the same network could enumerate this.
- **Proposed design:** Gate `/metrics` behind `_admin_auth` (consistent with all other admin endpoints), or accept a Prometheus `Bearer` scrape token separately configured via `CLEARVIEW_METRICS_TOKEN`. Scraping infra would need the token added to its config.
- **Why deferred:** Adding `_admin_auth` to `/metrics` is behavior-changing (all existing scrape configurations would break until reconfigured).

### M-4 — `filelock` 3.32.7 has 13 CVEs (fix: ≥ 4.2.1) ⚠ DEFERRED

- **Scope:** transitive dependency (pulled in by `litellm`)
- **CVEs:** CVE-2026-112907 through CVE-2026-112940 (fix version 4.2.1 covers all)
- **Risk:** Vulnerabilities in the file-locking library used internally by litellm. Severity of individual CVEs not fully assessed from pip-audit alone.
- **Proposed design:** Pin `filelock>=4.2.1` in `pyproject.toml` (or override in requirements) and test that litellm still functions.
- **Why deferred:** Requires a dependency update + full integration test against litellm to rule out breakage.

---

## Low Findings

### L-1 — `pip` 24.0 / `setuptools` 79.0.1 CVEs in build environment ⚠ DEFERRED

- **Scope:** build/venv tools only, not shipped in the production image
- **pip CVEs:** PYSEC-2026-1795, -1796, -2875, -2876, -196, -3721 (fix: ≥ 26.2)
- **setuptools CVEs:** PYSEC-2026-3447, GHSA-xmj3-9gf7-h52w (fix: ≥ 84.1.0)
- **Risk:** Low for production; affects developer environments and CI pipelines.
- **Proposed design:** Update pip and setuptools in the Docker build layer (`RUN pip install --upgrade pip setuptools`) and in developer setup instructions.
- **Why deferred:** Build-time only; no production exposure.

---

## Minimum Sweep (Mandatory Checks)

### (a) Route auth coverage

All `@app.get/post/delete` routes reviewed:

| Route | Auth | Notes |
|-------|------|-------|
| `GET /v1/models` | None | Intentional — OpenAI-compatible model listing |
| `POST /v1/chat/completions` | `_client_key_allowed` + `_resolve_team` | ✅ |
| `POST /v1/messages` | (delegates to chat completions) | ✅ |
| `POST /v1/responses` | (delegates to chat completions) | ✅ |
| `POST /v1beta/models/.../generateContent` | (delegates) | ✅ |
| `POST /feedback` | None | Documented open by design |
| `GET /health` | None | Standard health check, no sensitive data |
| `GET /metrics` | None | **See M-3 above** |
| `GET /admin/*` | `_admin_auth` | ✅ |
| `GET /chat` | None (render page; login state shown) | ✅ |
| `GET /chat/model_options` | `_require_chat_team` | ✅ |
| `POST /chat/login` | None (pre-auth endpoint) | ✅ |
| `POST /chat/logout` | None | ✅ |
| `GET /chat/me` | `_require_chat_team` | ✅ |
| `GET/POST/DELETE /chat/conversations*` | `_require_chat_team` | ✅ |

### (b) Dangerous builtins

No uses of `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, or `yaml.load` (without SafeLoader) found in `app/` or `app/providers/`. Subprocess calls in providers use `create_subprocess_exec` with a list of arguments — not vulnerable to shell injection.

### (c) SQL parameterization

All SQL queries in `app/main.py` use parameterized queries (`?` placeholders with `tuple(params_l)`). The `WHERE` clauses are assembled from fixed strings with appended parameters — no user-controlled values are interpolated into the SQL text directly. ✅

### (d) Tracked secrets scan

`git ls-files` shows no tracked `.env`, `.key`, `.pem`, or credential files. No live-looking API keys (`sk-`, `AKIA`, `ghp_`) found in tracked files. ✅

### (e) Secret comparison and generation

- Token generation: `secrets.token_hex()` used in `app/teams.py:117` and `app/chat.py:62`. ✅
- Secret comparison: `_admin_auth` previously used `!=` (timing-unsafe). **Fixed to `hmac.compare_digest` (H-1 above).** ✅

### (f) Dockerfile / docker-compose

- Dockerfile: previously ran as root. **Fixed (M-1 above).** ✅
- `docker-compose.yml` publishes port `8000:8000` to all interfaces (`0.0.0.0`). No `CLEARVIEW_ADMIN_TOKEN` or `CLEARVIEW_CLIENT_KEYS` are set by default. This is documented behavior for local dev; production deployments should set these via environment variables and restrict binding to loopback or a private network interface.

### (g) Dependency CVEs

See M-4 (filelock) and L-1 (pip/setuptools) above. No CVEs found in core application dependencies (FastAPI, pydantic, litellm direct).

---

## Positive Observations

- All admin endpoints consistently call `_admin_auth`, which is a named guard making coverage easy to audit.
- Team token generation uses `secrets.token_hex(16)` (cryptographically random, 128-bit entropy).
- SQL queries consistently use parameterized form; no f-string query construction found.
- CLI provider adapters use `create_subprocess_exec` with argument lists, not shell strings.
- Cookie is `HttpOnly` and `SameSite=Lax`, preventing basic CSRF and XSS token theft.
- The `.gitignore` correctly excludes `.env` and `*.db` files; no credentials found in tracked files.

---

## Remediation Status

| ID  | Finding                               | Status  |
|-----|---------------------------------------|---------|
| H-1 | Non-constant-time admin token compare | Fixed ✅ |
| M-1 | Dockerfile non-root USER              | Fixed ✅ |
| M-2 | Cookie missing Secure flag            | Deferred |
| M-3 | /metrics unauthenticated              | Deferred |
| M-4 | filelock CVEs                         | Deferred |
| L-1 | pip/setuptools build CVEs             | Deferred |
