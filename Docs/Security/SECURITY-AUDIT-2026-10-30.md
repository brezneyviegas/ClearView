# Security Audit — 2026-10-30

**Repository:** ClearView (Python/FastAPI LLM gateway)  
**Auditor:** Automated daily audit (Claude Code)  
**Branch audited:** `main` (HEAD `e8ad2e3`)  
**Audit date:** 2026-10-30  
**Previous report:** None (first audit)

---

## Severity Summary

| Severity | New | Fixed in this run | Deferred | Carried over |
|----------|-----|-------------------|----------|--------------|
| High     | 0   | 0                 | 0        | 0            |
| Medium   | 3   | 2                 | 1        | 0            |
| Low      | 2   | 0                 | 2        | 0            |

---

## High Findings

_None._

---

## Medium Findings

### M-1 · Non-constant-time admin token comparison — **FIXED**

| Field | Detail |
|-------|--------|
| File | `app/main.py:301` |
| Function | `_admin_auth` |
| Status | Fixed in this run |

**Risk:** `CLEARVIEW_ADMIN_TOKEN` was compared with `token != expected`, a Python `!=` string comparison that can short-circuit on the first differing byte. This creates a timing side-channel that an attacker on a low-latency link (e.g. co-located on the same host) could exploit to brute-force the admin token one character at a time, dramatically reducing the search space.

**Remediation:** Replaced with `secrets.compare_digest(token, expected)`, which runs in constant time regardless of where the strings differ. Added `import secrets` to the module imports.

---

### M-2 · Dockerfile runs as root — **FIXED**

| Field | Detail |
|-------|--------|
| File | `Dockerfile` |
| Status | Fixed in this run |

**Risk:** The previous Dockerfile had no `USER` directive, meaning the container process ran as UID 0 (root). Any code-execution vulnerability in the gateway (e.g. via a malicious upstream provider response, a FastAPI deserialization bug, or a compromised dependency) would immediately grant root access inside the container, enabling trivial container-escape attempts and unrestricted writes to mounted volumes.

**Remediation:** Added a `clearview` system user/group and `USER clearview` before the final `CMD`. The `/data` volume directory is `chown`ed to `clearview` so the process can still write the SQLite database.

---

### M-3 · `/metrics` endpoint has no authentication — **DEFERRED**

| Field | Detail |
|-------|--------|
| File | `app/main.py:2538` |
| Route | `GET /metrics` |
| Status | Deferred — behavior change |

**Risk:** The Prometheus `/metrics` endpoint exposes per-team identifiers (`team_id` label), call counts, cost totals, and tier/provider breakdowns without any authentication. In a shared or cloud deployment this leaks billing and usage patterns to any unauthenticated client that can reach port 8000.

**Proposed design:** Gate with `_admin_auth` by default. Alternatively, provide an opt-out env var `CLEARVIEW_METRICS_OPEN=1` for operators who rely on network-level Prometheus scraping (standard practice) and don't want to configure a bearer token in their scrape config. The existing `CLEARVIEW_ADMIN_TOKEN` bearer auth is the simplest mechanism to add.

---

## Low Findings

### L-1 · Session cookie missing `Secure` flag — **DEFERRED**

| Field | Detail |
|-------|--------|
| File | `app/main.py:2651-2654` |
| Route | `POST /chat/login` |
| Status | Deferred — could break local HTTP deployments |

**Risk:** The `cv_session` cookie is issued with `httponly=True` and `samesite="lax"` but without `secure=True`. Without the `Secure` flag the cookie is sent over plain HTTP requests, making it interceptable on unencrypted networks. In a production TLS deployment the token would be exposed if a user accidentally loads the page over HTTP (e.g. a redirect hasn't fired yet, or a mixed-content scenario).

**Proposed design:** Set `secure=True` when `CLEARVIEW_HTTPS=1` (or similar env var) is present. Default remains `secure=False` so local-dev HTTP still works without TLS configuration.

---

### L-2 · docker-compose publishes clearview on all network interfaces — **DEFERRED**

| Field | Detail |
|-------|--------|
| File | `docker-compose.yml:9` |
| Status | Deferred — intentional for many deployments |

**Risk:** `ports: "8000:8000"` binds to `0.0.0.0` (all interfaces). On a machine with a public or shared network interface, ClearView is reachable without a firewall rule. Combined with the default open-auth mode (no `CLEARVIEW_ADMIN_TOKEN` set), admin endpoints would be unauthenticated and publicly accessible.

**Proposed design:** Document the recommendation to set `CLEARVIEW_ADMIN_TOKEN` and `CLEARVIEW_CLIENT_KEYS` before deploying on any non-loopback network. Optionally change the default binding to `"127.0.0.1:8000:8000"` with a comment instructing operators to widen it for remote access.

---

## Route Authentication Audit

Every `@app.get/post/delete` route checked against the four auth helpers:

| Route | Auth helper | Notes |
|-------|-------------|-------|
| `GET /v1/models` | none | Intentionally open (OpenAI compat — clients enumerate models pre-auth) |
| `POST /v1/chat/completions` | `_client_key_allowed` + `_resolve_team` | Open when `CLEARVIEW_CLIENT_KEYS` unset (local dev) |
| `POST /v1/messages` | via `_handle_chat_completions` | Same as above |
| `POST /v1/responses` | via `_handle_chat_completions` | Same as above |
| `POST /v1beta/models/{m}:generateContent` | via `_handle_chat_completions` | Same |
| `POST /v1/models/{m}:generateContent` | via `_handle_chat_completions` | Same |
| `POST /v1beta/models/{m}:streamGenerateContent` | via `_handle_chat_completions` | Same |
| `POST /v1/models/{m}:streamGenerateContent` | via `_handle_chat_completions` | Same |
| `POST /feedback` | none | Documented intentional — clients rate their own requests |
| `GET /health` | none | Intentional health-check endpoint |
| `GET /metrics` | none | **See M-3** |
| `GET /admin/stats` | `_admin_auth` | ✓ |
| `GET /admin/provider_scores` | `_admin_auth` | ✓ |
| `GET /admin/feedback` | `_admin_auth` | ✓ |
| `GET /admin/setup` | `_admin_auth` | ✓ |
| `GET+POST /admin/tune` | `_admin_auth` | ✓ |
| `POST /admin/tune/revert` | `_admin_auth` | ✓ |
| `GET /admin/tune/history` | `_admin_auth` | ✓ |
| `GET /admin/explorer` | `_admin_auth` | ✓ |
| `GET /admin/timeseries` | `_admin_auth` | ✓ |
| `GET /admin/calls_detail` | `_admin_auth` | ✓ |
| `GET /admin/routing_quality` | `_admin_auth` | ✓ |
| `GET /admin/rule_hits` | `_admin_auth` | ✓ |
| `GET /admin/shadow_verdicts` | `_admin_auth` | ✓ |
| `GET /admin/shadow_compare` | `_admin_auth` | ✓ |
| `GET /admin/ticker` | `_admin_auth` | ✓ |
| `POST /admin/teams` | `_admin_auth` | ✓ |
| `GET /admin/teams` | `_admin_auth` | ✓ |
| `PATCH /admin/teams/{id}` | `_admin_auth` | ✓ |
| `DELETE /admin/teams/{id}` | `_admin_auth` | ✓ |
| `GET /chat` | none | Intentional — serves login page to unauthenticated users |
| `POST /chat/login` | none | Intentional — credential submission endpoint |
| `POST /chat/logout` | none | Intentional — no auth needed to clear cookie |
| `GET /chat/model_options` | `_require_chat_team` | ✓ |
| `GET /chat/me` | `_require_chat_team` | ✓ |
| `GET /chat/conversations` | `_require_chat_team` | ✓ |
| `POST /chat/conversations` | `_require_chat_team` | ✓ |
| `GET /chat/conversations/{cid}/messages` | `_require_chat_team` | ✓ |
| `DELETE /chat/conversations/{cid}` | `_require_chat_team` | ✓ |
| `POST /chat/conversations/{cid}/send` | `_require_chat_team` | ✓ |
| `POST /chat/conversations/{cid}/send_stream` | `_require_chat_team` | ✓ |

---

## Code Pattern Sweep

| Check | Result |
|-------|--------|
| `shell=True` / `os.system` | None found. Subprocess calls use `asyncio.create_subprocess_exec` (safe exec-based dispatch) |
| `eval(` / `exec(` | None found |
| `pickle` | None found |
| `yaml.load` without Loader | None found (pyyaml used only through `load_policy`) |
| SQL f-strings with user values | None found. F-strings build only from whitelisted column-name literals; all runtime values are parameterized via `?` placeholders |
| Tracked secrets (`.env`, `.key`, `.pem`) | None found |
| Live-looking keys in tracked files (`sk-`, `AKIA`, `ghp_`) | None found |
| Secret comparison | `secrets.compare_digest` now used in `_admin_auth` (fixed this run) |
| Token generation | `secrets.token_hex` used throughout — secure |

---

## Dependency CVE Scan (pip-audit)

ClearView's declared dependencies (fastapi, uvicorn, litellm, pydantic, pyyaml, jinja2, httpx, python-multipart) show **no CVEs**.

Vulnerable packages found in the execution environment that are **not** ClearView dependencies:

| Package | Installed | CVEs | Fix version |
|---------|-----------|------|-------------|
| `pypdf` | 6.17.0 | PYSEC-2026-4153/4154/4155/4156/4157/4159/4160, CVE-2026-113786/113789/113790/113791/113792/113793/113794/113795, CVE-2026-115572/115580/115585 | 6.21.2 |
| `setuptools` | 68.1.2 | PYSEC-2025-49, PYSEC-2026-1918, PYSEC-2026-3447, GHSA-xmj3-9gf7-h52w | 84.1.0 |
| `wheel` | 0.42.0 | CVE-2026-24049 | 0.46.2 |

These packages are installed in the host execution environment (the audit runner), not in ClearView's application runtime. They do not affect the deployed gateway but should be updated in the base environment.

---

## Positive Observations

- All `/admin/*` routes are consistently gated by `_admin_auth`.
- All `/chat/*` authenticated routes call `_require_chat_team` correctly.
- Team token generation uses `secrets.token_hex(16)` (128 bits of entropy).
- SQL queries consistently use `?` parameterization for user-controlled values.
- Subprocess calls use `asyncio.create_subprocess_exec` — no shell injection surface.
- The `.gitignore` covers `.env`, `*.key`, `*.pem`, and similar secret file patterns.
- No secrets or live API keys found in tracked files.
- Admin token comparison now uses constant-time `secrets.compare_digest` (fixed this run).

---

## Deferred Items Summary

| ID | Title | Proposed design |
|----|-------|-----------------|
| M-3 | `/metrics` unauthenticated | Gate with `_admin_auth`; opt-out via `CLEARVIEW_METRICS_OPEN=1` |
| L-1 | Session cookie missing `Secure` flag | Set `secure=True` when `CLEARVIEW_HTTPS=1` env var present |
| L-2 | docker-compose binds all interfaces | Document `CLEARVIEW_ADMIN_TOKEN` requirement; consider defaulting to loopback bind |
