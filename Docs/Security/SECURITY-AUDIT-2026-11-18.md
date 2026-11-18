# ClearView Security Audit — 2026-11-18

**Auditor:** Automated daily audit (Claude Code scheduled routine)
**Branch audited:** `main` (HEAD `e8ad2e3`)
**Remediation branch:** `security/audit-2026-11-18`
**Previous report:** None — first audit run; no deferred items to carry over.

---

## Severity Summary

| Severity | New | Fixed this run | Deferred |
|----------|-----|---------------|----------|
| High     | 1   | 1             | 0        |
| Medium   | 3   | 0             | 3        |
| Low      | 2   | 1             | 1        |
| Info     | 1   | —             | —        |

---

## High Findings

### H-1 · Non-constant-time admin token comparison — **FIXED**

| | |
|---|---|
| **File** | `app/main.py:301` |
| **Risk** | `token != expected` in `_admin_auth` is not constant-time. A remote attacker with many requests can distinguish correct vs. wrong token prefixes via timing, allowing an offline brute-force of `CLEARVIEW_ADMIN_TOKEN`. |
| **Remediation applied** | Replaced with `secrets.compare_digest(token, expected)` and added `import secrets` to the module's import block. |

---

## Medium Findings

### M-1 · `/v1/models` endpoint has no authentication gate — **DEFERRED**

| | |
|---|---|
| **File** | `app/main.py:364` |
| **Risk** | `GET /v1/models` returns all configured model IDs and tier mappings without any authentication check, even when `CLEARVIEW_ADMIN_TOKEN` is set. This leaks the operator's model topology to any unauthenticated caller. |
| **Why deferred** | Adding auth to `/v1/models` would break OpenAI-compatible clients (LiteLLM, `openai` SDK, VS Code extensions) that query the model list before supplying a bearer token, since the OpenAI spec treats `GET /v1/models` as unauthenticated. The correct fix is a configuration knob that lets operators opt-in to requiring auth on this endpoint, defaulting to open for compatibility. |

### M-2 · `/metrics` endpoint has no authentication gate — **DEFERRED**

| | |
|---|---|
| **File** | `app/main.py:2538` |
| **Risk** | `GET /metrics` returns Prometheus counters that include tier labels, provider names, team IDs, and cumulative cost/token data. An unauthenticated caller can observe the operator's spend and routing telemetry. |
| **Why deferred** | Standard Prometheus scrapers do not send auth headers. The fix options are: (a) a `CLEARVIEW_METRICS_TOKEN` env var, (b) a network-policy-only approach (bind `/metrics` to a separate port or loopback), or (c) a flag to disable the endpoint. This needs an operator-visible design choice and cannot safely be done as a flag-day change. |

### M-3 · `cv_session` cookie missing `secure=True` — **FIXED (Low → see L-1 below)**

*(Re-classified; see L-1.)*

---

## Low Findings

### L-1 · `cv_session` cookie missing `secure=True` — **FIXED**

| | |
|---|---|
| **File** | `app/main.py:2654` |
| **Risk** | The `/chat/login` endpoint sets `cv_session` with `HttpOnly` and `SameSite=Lax` but no `Secure` flag. If the deployment is ever accessed over plain HTTP (e.g., behind a misconfigured reverse proxy), the token could be transmitted unencrypted. |
| **Remediation applied** | Added `secure=True` to `resp.set_cookie(...)`. Modern browsers on HTTPS connections ignore this; on plain HTTP the cookie will not be sent, which is the safer failure mode. |

### L-2 · Dockerfile runs container process as root — **DEFERRED**

| | |
|---|---|
| **File** | `Dockerfile` |
| **Risk** | No `USER` directive is present; the application runs as root inside the container. If an attacker achieves arbitrary code execution, they obtain root capabilities within the container. |
| **Why deferred** | The `/data` volume mount requires write permission; a naive `USER nobody` breaks the SQLite database path `CLEARVIEW_DB_PATH=/data/clearview.db`. Correct fix: add `RUN useradd -r -u 1001 clearview && chown clearview /data` and `USER clearview`, verified against the volume mount behavior in docker-compose. Deferred to avoid breaking the one-command setup. |

---

## Deferred Items Summary (carried to next run)

| ID | File | Summary |
|----|------|---------|
| M-1 | `app/main.py:364` | `/v1/models` unauthenticated — needs compat opt-in knob |
| M-2 | `app/main.py:2538` | `/metrics` unauthenticated — needs operator design choice |
| L-2 | `Dockerfile` | No non-root USER — needs volume permission design |

---

## Positive Observations

- **No tracked secrets**: `git ls-files` revealed no `.env`, `.key`, or `.pem` files committed. The repo contains only `.env.example` and `clearview-client.env.example`.
- **No live API keys in source**: No `sk-`, `AKIA`, or `ghp_` patterns found in any tracked file.
- **Secure token generation**: Team tokens use `secrets.token_hex(16)` (cryptographically random, 128-bit entropy).
- **No dangerous code patterns**: No `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, or unsafe `yaml.load` calls found. Subprocess calls use `create_subprocess_exec` with hardcoded argument lists.
- **SQL injection-free**: All SQL queries use `?`-parameterized bindings. The f-string `WHERE` clause builders inject only hardcoded column names and operators, never user input.
- **pip-audit**: Zero known CVEs across all installed dependencies.
- **Admin routes gated**: All `/admin/*` endpoints call `_admin_auth(request)`. All `/chat/*` data endpoints call `_require_chat_team(request)`.
- **SameSite + HttpOnly cookie**: Session cookie already had both flags before this audit.

---

## Checklist Coverage

| Item | Result |
|------|--------|
| (a) Every route calls auth guard or documented open | Pass — `/v1/models`, `/metrics`, `/health`, `/feedback`, `/chat` (HTML) are open; two flagged above (M-1, M-2) |
| (b) `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, unsafe `yaml.load` | None found |
| (c) SQL parameterized — no f-string/concat with user values | Pass |
| (d) No tracked secrets or live-looking keys | Pass |
| (e) Constant-time secret compare, `secrets` module for tokens | Fixed (H-1); generation was already correct |
| (f) Dockerfile non-root USER; docker-compose loopback | Deferred (L-2); docker-compose ollama profile publishes on all interfaces but that is a development-only profile |
| (g) pip-audit CVEs | None found |
