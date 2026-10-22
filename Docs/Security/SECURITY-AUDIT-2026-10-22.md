# Security Audit — 2026-10-22

**Auditor:** automated daily routine  
**Branch audited:** `main` (HEAD `e8ad2e3`)  
**Remediation branch:** `security/audit-2026-10-22`  
**Previous audit:** none (first run — no Security/ directory existed)

---

## Severity Summary

| Severity | New | Fixed this run | Deferred | Carried Over |
|----------|-----|----------------|----------|--------------|
| High     | 0   | 0              | 0        | 0            |
| Medium   | 2   | 2              | 0        | 0            |
| Low      | 3   | 0              | 3        | 0            |

---

## Medium Findings

### M-1 — Non-constant-time admin token comparison  
**File:** `app/main.py:301`  
**Status:** **FIXED** (commit on `security/audit-2026-10-22`)

`_admin_auth` compared the incoming bearer token against `CLEARVIEW_ADMIN_TOKEN` with the `!=` operator:
```python
if token != expected:
```
Python's `!=` on strings short-circuits at the first differing byte, leaking timing information. A local-network attacker making many requests could enumerate the token byte-by-byte.

**Remediation applied:** replaced with `secrets.compare_digest(token, expected)` and added `import secrets` to the module.

---

### M-2 — Dockerfile runs as root  
**File:** `Dockerfile`  
**Status:** **FIXED** (commit on `security/audit-2026-10-22`)

The Dockerfile had no `USER` directive; the application process ran as root inside the container. If an attacker achieved RCE via any upstream dependency the entire container filesystem (including any mounted secrets) would be accessible with root privileges.

**Remediation applied:**
```dockerfile
RUN adduser --disabled-password --gecos '' clearview && \
    mkdir -p /data && chown clearview /data
USER clearview
```
The data volume is pre-chowned so the non-root user can still write the SQLite database.

---

## Low Findings (Deferred)

### L-1 — `/metrics` endpoint unauthenticated  
**File:** `app/main.py:2538`  
**Status:** DEFERRED

The Prometheus `/metrics` endpoint requires no authentication. It exposes cost totals, call counts by tier/provider/status, and `team_id` label values to any requester. In a cloud deployment where the service is reachable beyond localhost this constitutes data leakage.

**Proposed design:** gate `/metrics` behind `_admin_auth` (consistent with all other `/admin/*` endpoints), or add a dedicated `CLEARVIEW_METRICS_TOKEN` so scrape configs can use a separate credential. Either needs a docs update for Prometheus scrape config. Changing this will break any existing Prometheus scraper that relies on the open endpoint — explicit user sign-off required before applying.

---

### L-2 — docker-compose binds port 8000 to all interfaces  
**File:** `docker-compose.yml:9`  
**Status:** DEFERRED

`ports: - "8000:8000"` binds to `0.0.0.0` (all interfaces). When `CLEARVIEW_ADMIN_TOKEN` is not configured, the admin panel and team-management endpoints are fully open to any host that can reach the container. In a shared-network or cloud environment this is an exposure.

**Proposed design:** change to `"127.0.0.1:8000:8000"` by default, or add a note in `.env.example` that `CLEARVIEW_ADMIN_TOKEN` must be set for any non-localhost deployment. Changing the compose default could break existing deployments — user sign-off required.

---

### L-3 — Vulnerable build-time packages (pip-audit)  
**Status:** DEFERRED

`pip-audit` found 14 CVE entries against the virtual-environment's own tooling (not the running application):

| Package | Installed | CVE IDs | Fix |
|---------|-----------|---------|-----|
| `pip` | 24.0 | PYSEC-2026-1795, -1796, -196, -2875, -2876, -3721 | ≥ 26.2 |
| `setuptools` | 79.0.1 | PYSEC-2026-3447 | ≥ 83.0.0 |

None of these packages ship inside the application container (`Dockerfile` only copies `app/` and `pyproject.toml`); the CVEs affect build-time tooling only. The impact is limited to the build environment.

**Proposed remediation:** upgrade base image (currently `python:3.12-slim`) to one that ships pip ≥ 26.2, or pin `pip>=26.2,setuptools>=83.0.0` in CI's tool installation step.

---

## Audit Checklist Results

| Check | Result |
|-------|--------|
| Auth coverage — all routes checked | Pass (see notes) |
| `shell=True` / `os.system` / `eval` / `exec` / `pickle` | Clean |
| `yaml.load` (unsafe) | Clean |
| SQL parameterization | Pass — all queries use `?` placeholders; no f-string interpolation of user values into SQL |
| Tracked secrets (`git ls-files`) | Clean — no `.env`, `.key`, `.pem` tracked |
| Live-looking keys in tracked files | Clean — only `sk-x`/`sk-test` monkeypatched in tests |
| Token comparison uses `secrets.compare_digest` | Fixed this run (M-1) |
| Token generation uses `secrets` module | Pass — `teams.py` and `chat.py` use `secrets.token_hex` |
| Dockerfile `USER` (non-root) | Fixed this run (M-2) |
| docker-compose loopback binding | Deferred (L-2) |
| `pip-audit` CVE scan | Deferred (L-3) — build tooling only |

### Auth coverage notes

Routes intentionally open (no auth gate, documented reason):

| Route | Reason open |
|-------|-------------|
| `GET /v1/models` | OpenAI API compatibility — model listing is not sensitive |
| `GET /health` | Health-check endpoint — must be open for load balancers |
| `POST /chat/login` | Login flow entry point |
| `POST /chat/logout` | Logout — clearing a cookie, no secrets exposed |
| `POST /feedback` | Client-facing thumbs up/down; callers use `request_id` as handle, no admin data exposed |
| `GET /chat` | Returns a static login page for unauthenticated visitors |
| `GET /metrics` | **L-1** — deferred finding; currently open, see above |

All `/admin/*` routes call `_admin_auth`. All `/chat/*` routes that return user data call `_require_chat_team`. All `/v1/chat/completions` and compatibility shim routes call `_client_key_allowed` + `_resolve_team`.

---

## Positive Observations

- Strong use of `secrets.token_hex` for all team and conversation ID generation.
- All SQLite queries use parameterized `?` placeholders — no SQL injection surface found.
- Subprocess calls in provider adapters (`claude_cli`, `codex_cli`, `gemini_cli`) use `subprocess.run`/`create_subprocess_exec` with explicit list arguments and no `shell=True`; model names passed to the CLI are derived from the internal policy whitelist, not user input.
- Admin token is entirely optional in dev mode (`CLEARVIEW_ADMIN_TOKEN` unset → open); when set, it's checked consistently on every admin route.
- No `.env` or credential files tracked in git.
- Team tokens are redacted to a 18-char prefix in the list endpoint (`_team_to_redacted_dict`) — full Bearer tokens only returned on create.
- `httponly=True` on the `cv_session` cookie prevents JS token exfiltration.

---

## Remediation Status

| Finding | Fixed | Deferred | Notes |
|---------|-------|----------|-------|
| M-1 non-constant-time compare | ✅ | — | `secrets.compare_digest` applied; 359/359 tests pass |
| M-2 Dockerfile root user | ✅ | — | `USER clearview` added; tests pass |
| L-1 `/metrics` unauthed | — | ✅ | Needs sign-off; breaks Prometheus scrape configs |
| L-2 compose 0.0.0.0 bind | — | ✅ | Needs sign-off; breaks existing deployments |
| L-3 pip/setuptools CVEs | — | ✅ | Build tooling only; base image upgrade needed |

---

*No `.env` contents or database token values are included in this report.*
