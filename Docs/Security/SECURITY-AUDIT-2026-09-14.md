# ClearView Security Audit — 2026-09-14

**Auditor:** Automated daily security audit  
**Branch audited:** `main` (HEAD `e8ad2e3`)  
**Audit branch:** `security/audit-2026-09-14`  
**Previous report:** None (first audit)

---

## Severity Summary

| Severity | New | Fixed this run | Deferred |
|----------|-----|---------------|----------|
| High     | 2   | 2             | 0        |
| Medium   | 1   | 0             | 1        |
| Low      | 1   | 0             | 1        |
| Info     | 3   | 0             | 3        |

---

## High Findings

### H1 — Non-constant-time admin token comparison ✅ FIXED

**File:** `app/main.py:301` (`_admin_auth`)  
**Risk:** The original comparison `if token != expected` is not constant-time. An attacker who can make many rapid requests to any admin-gated endpoint can measure response latency and use it to leak the admin token byte-by-byte (timing oracle). Real-world exploitability depends on network jitter but is achievable on localhost and LAN deployments.  
**Remediation applied:** Replaced `if token != expected:` with `if not secrets.compare_digest(token, expected):` and added `import secrets` to `app/main.py`. `secrets.compare_digest` runs in constant time regardless of the number of bytes that match.  
**Test coverage:** Existing auth tests pass; no behavior change for valid/invalid token paths.

---

### H2 — Dockerfile runs as root ✅ FIXED

**File:** `Dockerfile`  
**Risk:** The image had no `USER` directive, so the container process ran as UID 0 (root). If the application is compromised (e.g., via an upstream provider callback or a future RCE), the attacker gains root inside the container, which makes container-escape exploits more dangerous and violates least-privilege.  
**Remediation applied:** Added a `clearview` system user/group and switched to it before `CMD`. Also pre-creates and chowns `/data` so the non-root user can still write the SQLite database volume.

```dockerfile
RUN groupadd -r clearview && useradd -r -g clearview -d /app clearview \
    && mkdir -p /data && chown clearview:clearview /data
USER clearview
```

---

## Medium Findings

### M1 — `/metrics` Prometheus endpoint is unauthenticated ⏳ DEFERRED

**File:** `app/main.py:2538` (`metrics()`)  
**Risk:** The Prometheus metrics endpoint is accessible to any client that can reach port 8000. It exposes: per-team-ID call counts, provider names, spend totals (tokens, cost), and error rates. This is potentially sensitive business intelligence. In a shared-network deployment without `CLEARVIEW_CLIENT_KEYS` set, it is open to all LAN clients.  
**Proposed fix (deferred):** Gate `/metrics` behind a new env var `CLEARVIEW_METRICS_TOKEN` (a separate secret so Prometheus scrape credentials are distinct from the admin API token). When unset, fall back to the existing `CLEARVIEW_ADMIN_TOKEN` for backward compatibility. This change would break any existing Prometheus scrape config that relies on the endpoint being open, so it is deferred for operator review.  
**Reason deferred:** Behavior-changing for existing deployments; requires coordination with Prometheus scrape configuration.

---

## Low Findings

### L1 — Build-tool CVEs in Docker image ⏳ DEFERRED

**Tool:** `pip-audit` against installed packages  
**Findings:**
- `pip 24.0` — PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-196, PYSEC-2026-3721 (fix: pip ≥ 26.2.1)
- `setuptools 79.0.1` — PYSEC-2026-3447 (fix: setuptools ≥ 83.0.0)

**Risk:** These CVEs are in build-time tools (`pip`, `setuptools`), not in the application's runtime dependencies. They could affect the build pipeline (supply-chain integrity, index spoofing) but do not directly affect ClearView's running service. The application itself (`litellm`, `fastapi`, `pydantic`, etc.) had **zero CVEs** from pip-audit.  
**Proposed fix (deferred):** Pin `pip` and `setuptools` upgrades in the Dockerfile (`RUN pip install --upgrade pip setuptools`) after `pip install .`. Deferred because it requires verifying compatibility with the Python 3.12-slim base and re-testing the build.

---

## Info / Undocumented-Open Routes

The following routes have no auth gate. Per the audit policy, each must have a documented reason; the reasons are listed here for the permanent record.

| Route | Method | No-auth rationale |
|-------|--------|-------------------|
| `GET /v1/models` | GET | OpenAI API compatibility — model-discovery endpoint must be publicly accessible for IDE clients (Continue, Cline, etc.) to enumerate models without pre-auth. Acceptable: returns only virtual model names, not secrets. |
| `POST /feedback` | POST | Documented in code as intentional: callers rate their own responses by `request_id`, which acts as a capability token. No admin gate avoids a chicken-and-egg problem where a client would need two credentials to give feedback. |
| `GET /health` | GET | Standard liveness probe; intentionally public so orchestrators (k8s, load balancers) can poll without credentials. |
| `GET /chat` | GET | Chat landing page; serves the HTML shell only. No data is returned without a valid `cv_session` cookie (the rest of the `/chat/*` API requires it). |

No action required for these. They are documented here for traceability.

---

## Positive Observations

- **Parameterized SQL throughout:** All `admin_timeseries`, `admin_calls_detail`, `admin_routing_quality`, `admin_rule_hits`, `admin_shadow_verdicts`, `admin_shadow_compare`, `admin_ticker` endpoints build WHERE clauses with Python lists of `?` placeholders and pass values as `tuple(params)` — no f-string interpolation of user-controlled values. No SQL injection surface found.
- **Team token generation uses `secrets.token_hex(16)`** (`app/teams.py:117`) — cryptographically strong.
- **`yaml.safe_load` used** (`app/config.py:51`) — no unsafe YAML deserialisation.
- **No tracked secrets:** `git ls-files` contains no `.env`, `.pem`, `.key` files. No live-looking API keys (`sk-`, `AKIA`, `ghp_`) found in tracked content.
- **No `shell=True`, `os.system`, `eval()`, `exec()`, or `pickle`** in `app/*.py`. CLI providers use `asyncio.create_subprocess_exec` (argument list, not shell string) — no command injection surface.
- **Cookie auth uses `httponly=True, samesite="lax"`** (`app/main.py:2653`) — appropriate cookie security for the `/chat` session token.
- **All `/admin/*` routes call `_admin_auth`**; all `/chat/*` data routes call `_require_chat_team`.
- **`/v1/chat/completions`, `/v1/messages`, `/v1/responses`, Gemini shims** all flow through `_handle_chat_completions` which calls `_client_key_allowed` and `_resolve_team` — consistent auth path.

---

## Remediation Status

| ID  | Description                                    | Status   | Commit |
|-----|------------------------------------------------|----------|--------|
| H1  | Constant-time admin token comparison           | Fixed    | this PR |
| H2  | Dockerfile non-root USER                       | Fixed    | this PR |
| M1  | `/metrics` auth gate                           | Deferred | see M1 above |
| L1  | pip/setuptools CVEs in build tools             | Deferred | see L1 above |

---

## Carried-Over Deferred Items

None (first audit).

---

*Generated by automated daily security audit. Never contains `.env` contents or live token values.*
