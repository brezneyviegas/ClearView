# Security Audit — 2026-11-11

**Repository:** ClearView (Python/FastAPI LLM gateway)  
**Auditor:** Automated daily audit  
**Audit date:** 2026-11-11  
**Previous report:** None (first audit)  
**Branch reviewed:** `main` @ e8ad2e3  

---

## Severity Summary

| Severity | Count | Fixed this run | Deferred |
|----------|-------|----------------|---------|
| High     | 0     | —              | —       |
| Medium   | 3     | 2              | 1       |
| Low      | 1     | 0              | 1       |
| Info     | 0     | —              | —       |

---

## High Findings

None.

---

## Medium Findings

### M-1 — Non-constant-time admin token comparison [FIXED]

**File:** `app/main.py` line ~302 (`_admin_auth`)  
**Risk:** Python's `!=` operator short-circuits on the first differing character, leaking timing information about how many leading bytes of `CLEARVIEW_ADMIN_TOKEN` match a probe value. An attacker with repeated low-latency access to the `/admin/*` endpoints could brute-force the token one character at a time.  
**Remediation:** Replaced `token != expected` with `secrets.compare_digest(token, expected)`. Applied in this run.

---

### M-2 — Dockerfile runs process as root [FIXED]

**File:** `Dockerfile`  
**Risk:** No `USER` directive was present. The FastAPI process (and any exploit code that escapes it) ran as `root` inside the container, maximising the blast radius of a container escape or code-execution vulnerability.  
**Remediation:** Added `RUN useradd -r -u 1001 -g root clearview && mkdir -p /data && chown clearview /data` and `USER clearview` before `CMD`. Applied in this run.

---

### M-3 — `/metrics` endpoint is unauthenticated [DEFERRED]

**File:** `app/main.py` line ~2538 (`metrics`)  
**Risk:** The Prometheus `/metrics` endpoint exposes per-team spend totals, tier/provider call counts, token volumes, and cost-drift percentages without requiring any authentication. In a shared-network or internet-exposed deployment this leaks billing and usage intelligence to any observer.  
**Proposed fix:** Add `_admin_auth(request)` to the `metrics` handler (requires accepting `request: Request` as a parameter), or introduce a dedicated `CLEARVIEW_METRICS_TOKEN` env var so a Prometheus scraper can authenticate without sharing the admin token.  
**Deferred because:** Adding `_admin_auth` without a Prometheus-compatible auth path (Basic auth or bearer) would break existing monitoring setups. Needs operator input on the preferred auth mechanism before applying.

---

## Low Findings

### L-1 — `/chat/login` session cookie missing `Secure` flag [DEFERRED]

**File:** `app/main.py` line ~2651 (`chat_login`, `resp.set_cookie`)  
**Risk:** The `cv_session` token cookie is issued with `httponly=True, samesite="lax"` but no `secure=True`. On a network where HTTP is possible (local dev, misconfigured proxy), the cookie could be sent in cleartext and intercepted.  
**Proposed fix:** Set `secure=True` on the cookie, or set it conditionally when the request arrives over HTTPS (`request.url.scheme == "https"`).  
**Deferred because:** Forcing `secure=True` unconditionally would break local HTTP dev mode. Should be paired with a TLS-deployment guide or an env-flag (`CLEARVIEW_SECURE_COOKIES=1`).

---

## Minimum-Sweep Checklist

| Check | Result |
|-------|--------|
| Every `@app.get/post/delete` route calls an auth gate or is documented open | ✅ See note below |
| `shell=True` / `os.system` / `eval(` / `exec(` / `pickle` / unsafe `yaml.load` | ✅ None found |
| SQL parameterized (no f-string/concat injecting user values) | ✅ All queries use `?` placeholders; f-strings build only fixed structural clauses |
| Tracked secrets (`.env`/`.pem`/`.key` files, live API keys in git) | ✅ None found |
| Secret comparisons use `secrets.compare_digest` | ✅ Fixed M-1; team tokens compared via DB lookup (I/O-dominated, not Python-level) |
| Token generation uses `secrets` module | ✅ `cv_team_` tokens use `secrets.token_hex(16)` |
| Dockerfile non-root `USER` | ✅ Fixed M-2 |
| docker-compose publishes beyond loopback | ⚠️ Port 8000 bound to `0.0.0.0` — expected for a local-network gateway; operator should ensure firewall/VPC controls |
| pip-audit CVEs | ✅ No known vulnerabilities |

**Route auth note:**  
- `/v1/models`, `/v1/chat/completions`, `/v1/messages`, `/v1/responses`, Gemini shims: controlled by `_client_key_allowed` + `_resolve_team`; open by default when `CLEARVIEW_CLIENT_KEYS` is unset (documented local-dev behaviour).  
- `/feedback`: intentionally open — callers rate their own responses by `request_id` handle.  
- `/health`: intentionally open.  
- `/metrics`: unauthenticated — see M-3.  
- `/chat`: shows login page; no gating needed.  
- All `/admin/*`, `/chat/me`, `/chat/conversations/*`, `/chat/model_options`: properly gated.  

---

## Positive Observations

- Team token generation (`cv_team_<32 hex chars>`) uses `secrets.token_hex` — cryptographically strong.
- SQL queries across `telemetry.py`, `teams.py`, and inline in `main.py` all use `?`-parameterised statements; no string-concatenation injection paths found.
- `_client_key_allowed` and `_resolve_team` share a consistent pattern and are called at the top of the main chat-completions handler before any routing or budget logic.
- Cookie is set `httponly=True, samesite="lax"` — mitigates XSS token theft and CSRF.
- Sensitive admin routes redact team bearer tokens in list responses (`id_short` prefix only).
- `asyncio.create_subprocess_exec` (not `shell=True`) used in all CLI provider adapters — no shell injection risk.
- No tracked secret files or live API keys found in the git history.

---

## Deferred Items Carried Forward

| ID  | Severity | Summary |
|-----|----------|---------|
| M-3 | Medium   | `/metrics` endpoint unauthenticated — needs auth mechanism design before fixing |
| L-1 | Low      | `cv_session` cookie missing `secure=True` — needs TLS deployment design |

---

## Remediation Status

| Finding | Status | Notes |
|---------|--------|-------|
| M-1 non-constant-time compare | **Fixed** | `secrets.compare_digest` applied |
| M-2 Dockerfile root | **Fixed** | `USER clearview` (uid 1001) added |
| M-3 `/metrics` unauthed | **Deferred** | Needs operator design decision |
| L-1 cookie Secure flag | **Deferred** | Needs TLS deployment design |
