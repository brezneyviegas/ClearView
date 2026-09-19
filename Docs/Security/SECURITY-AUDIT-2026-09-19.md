# ClearView Security Audit — 2026-09-19

**Auditor:** Automated daily audit (Claude Code scheduled task)  
**Branch audited:** `main` @ `e8ad2e3`  
**Remediation branch:** `security/audit-2026-09-19`  
**Previous report:** None (first audit)  
**pip-audit result:** No known CVEs

---

## Severity Summary

| Severity | Count | Fixed this run | Deferred |
|----------|-------|---------------|---------|
| High     | 0     | —             | —       |
| Medium   | 3     | 1             | 2       |
| Low      | 2     | 1             | 1       |
| Info     | 1     | 0             | 1       |

---

## Findings

### Medium

#### M1 — Non-constant-time admin token comparison ✅ FIXED

**Location:** `app/main.py:301` (`_admin_auth`)  
**Risk:** `token != expected` is a Python string equality check that short-circuits on the first differing byte. An attacker able to send many requests and measure response latency can enumerate the `CLEARVIEW_ADMIN_TOKEN` value one character at a time. Because the admin endpoints control team management, cost caps, and routing configuration, a compromised admin token is high-impact.  
**Remediation applied:** Added `import secrets` (line 10) and replaced `token != expected` with `not secrets.compare_digest(token, expected)`.  
**Status:** Fixed in this PR; tests pass.

---

#### M2 — `/metrics` endpoint is unauthenticated ⚠️ DEFERRED

**Location:** `app/main.py:2538` (`metrics`)  
**Risk:** The Prometheus `/metrics` endpoint exposes per-team IDs (`team_id` label), total USD spend (native and plan-equivalent), output token counts, and routing provider breakdowns — all without authentication. In a multi-tenant deployment this leaks business-sensitive data about every customer's usage to any network observer who can reach port 8000. Even in single-tenant setups it discloses operational cost data.  
**Proposed fix:** Gate `GET /metrics` behind `_admin_auth(request)`. Prometheus scrapers must be reconfigured to send `Authorization: Bearer <CLEARVIEW_ADMIN_TOKEN>` (supported via `bearer_token` in `prometheus.yml`). Alternatively, add a separate `CLEARVIEW_METRICS_TOKEN` env var so metrics scraping can be separated from admin operations.  
**Deferred because:** Gating a previously-open Prometheus endpoint requires operator action to reconfigure scrapers. Enabling this without a migration window would break existing monitoring. Flagged for explicit sign-off before enabling.

---

#### M3 — Session cookie missing `Secure` flag ⚠️ DEFERRED

**Location:** `app/main.py:2651` (`chat_login`, `set_cookie`)  
**Risk:** The `/chat` session cookie (`cv_session`) is set with `httponly=True, samesite="lax"` but without `secure=True`. Without `Secure`, the browser sends the cookie over plain HTTP as well as HTTPS. If ClearView is deployed behind a reverse proxy that terminates TLS, any HTTP request (redirect before HTTPS upgrade, mixed-content scenario) would include the team Bearer token in the clear.  
**Proposed fix:** Add `secure=True` to `resp.set_cookie(...)`. For local HTTP-only development this can be conditioned on a `CLEARVIEW_HTTPS=1` env flag.  
**Deferred because:** Enabling `secure=True` unconditionally breaks local HTTP development (the primary documented use case). Needs a config switch to be safe.

---

### Low

#### L1 — Dockerfile runs as root ✅ FIXED

**Location:** `Dockerfile` (line 17, before fix)  
**Risk:** Without a `USER` directive the container process runs as UID 0 (root). A container escape or app-level RCE grants an attacker root access inside the container, making privilege escalation within the container trivial and increasing blast radius for volume mounts.  
**Remediation applied:** Added a `clearview` system user and `USER clearview` directive. The `/data` volume directory is pre-created and owned by this user so the DB write path is unaffected.  
**Status:** Fixed in this PR.

---

#### L2 — docker-compose binds to all interfaces ⚠️ DEFERRED

**Location:** `docker-compose.yml:8` (`ports: "8000:8000"`) and `:20` (`"11434:11434"` in Ollama profile)  
**Risk:** Port binding `"8000:8000"` listens on `0.0.0.0` by default, exposing the gateway on every host network interface. On a multi-homed developer machine or cloud VM with a public IP, this makes the API reachable without the operator explicitly intending it. The Ollama port `11434` in the `local` profile has the same issue.  
**Proposed fix:** Change to `"127.0.0.1:8000:8000"` (and `"127.0.0.1:11434:11434"`) for the default compose profile. Document an override for deployments that intentionally bind externally.  
**Deferred because:** Some users legitimately want LAN-accessible ClearView (e.g., a team on the same network). This is a config-hardening change that needs explicit documentation and user communication rather than a silent tightening.

---

### Info / Informational

#### I1 — `/v1/models` and `/v1/models/{model}:generateContent` open without auth

**Location:** `app/main.py:364`  
**Risk:** These endpoints enumerate both virtual tier names (`clearview-auto`, `clearview-cheap`, etc.) and the real underlying model IDs configured in `policy.yaml`. An unauthenticated caller can discover the full model inventory. For most deployments the model list is not sensitive; OpenAI-compatible clients also depend on `/v1/models` being open.  
**Recommendation:** If model names are considered sensitive (e.g., a private fine-tuned model is in the tier list), gate this endpoint. Otherwise, document the intentional open posture.  
**Status:** No action taken; noted for awareness.

---

## Audit Checklist Results

| Check | Result |
|-------|--------|
| Auth coverage — all `@app.get/post/delete` routes have auth or documented open reason | ✅ Pass (all compat shims delegate to `_handle_chat_completions` which calls `_client_key_allowed` + `_resolve_team`; `/health`, `/feedback`, `/v1/models` documented open) |
| Injection — `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, `yaml.load` | ✅ None found |
| SQL parameterization — no f-string interpolation of user-controlled values | ✅ Pass (dynamic `WHERE` clauses use only hardcoded column names; values always passed as `?` parameters) |
| Tracked secrets — `.env`/`key`/`pem` files, live-looking API keys in tracked files | ✅ `.env` gitignored; no `sk-`, `AKIA`, `ghp_` patterns found in tracked files |
| Token handling — comparisons via `secrets.compare_digest`, generation via `secrets` module | ⚠️ Fixed (admin token); team tokens already use `secrets.token_hex(16)` |
| Dockerfile non-root `USER` | ⚠️ Fixed in this PR |
| docker-compose loopback binding | ⚠️ Deferred (M2 / L2) |
| pip-audit CVE scan | ✅ No known vulnerabilities |

---

## Deferred Findings Summary

| ID | Title | Proposed design |
|----|-------|----------------|
| M2 | `/metrics` unauthenticated | Gate behind `_admin_auth`; scraper migration required |
| M3 | Session cookie missing `Secure` flag | `secure=True` conditioned on `CLEARVIEW_HTTPS` env var |
| L2 | docker-compose binds to all interfaces | Change to `127.0.0.1:…:…` with documented override |
| I1 | `/v1/models` open (informational) | Document intentional posture or gate if models are sensitive |

---

## Positive Observations

- Team tokens minted with `secrets.token_hex(16)` — cryptographically strong PRNG throughout.
- `cv_session` cookie is `HttpOnly` and `SameSite=Lax` — prevents XSS token theft and CSRF.
- `.env` and `*.db` files are gitignored; no live credentials in tracked files.
- All admin endpoints (`/admin/*`, team management) consistently guarded by `_admin_auth`.
- No use of `shell=True`, `eval`, `exec`, `pickle`, or unsafe `yaml.load` anywhere in the codebase.
- SQL queries use parameterized `?` bindings throughout; dynamic SQL only assembles column names from hardcoded strings.
- Dependency audit clean — `pip-audit` finds no CVEs across `fastapi`, `litellm`, `uvicorn`, `pyyaml`, `httpx`, `pydantic`, `jinja2`.
- `/feedback` endpoint's intentional open posture is documented inline.

---

## Remediation Status

| Finding | Status |
|---------|--------|
| M1 — Non-constant-time admin token compare | ✅ Fixed (`secrets.compare_digest`) |
| M2 — `/metrics` unauthenticated | ⚠️ Deferred — needs scraper migration plan |
| M3 — Cookie missing `Secure` flag | ⚠️ Deferred — needs `CLEARVIEW_HTTPS` flag |
| L1 — Dockerfile runs as root | ✅ Fixed (`USER clearview`) |
| L2 — docker-compose all-interfaces binding | ⚠️ Deferred — intentional for dev; needs docs |
| I1 — `/v1/models` open | ℹ️ Noted — likely intentional for compat |
