# ClearView Security Audit — 2026-10-15

**Auditor:** Automated daily audit (scheduled)
**Branch audited:** `main` @ `e8ad2e3`
**Previous report:** `Docs/Security/SECURITY-AUDIT-2026-10-14.md` (on `security/audit-2026-10-14`)

---

## Severity Summary

| Severity | New | Fixed This Run | Deferred | Carry-Over Open |
|----------|-----|----------------|----------|-----------------|
| High     | 1   | 0              | 1        | 0               |
| Medium   | 2   | 2              | 0        | 0               |
| Low      | 1   | 1              | 0        | 0               |

---

## Findings

### High — H-001: 30+ consecutive unmerged security audit PRs (Process Failure)

**Location:** GitHub PRs #15–#44 (`security/audit-2026-09-14` through `security/audit-2026-10-14`)
**Risk:** Every daily audit since 2026-09-14 has identified M-001 (timing oracle) and M-002
(Dockerfile root user), applied fixes on a branch, and opened a PR. All 30 PRs remain open and
unmerged. The fixes have never landed in `main`. An attacker with network access can still exploit
the timing oracle against the admin token, and any code-execution vulnerability in the runtime
executes as root inside the container.

**Remediation:** A human reviewer must merge one of the open audit PRs — or approve and merge
today's PR — to bring the fixes into `main`. Once merged, the carry-over findings below become
resolved. This cannot be automated by the audit routine.

**Status:** **Deferred — requires human action.** (Today's fixes re-apply the same patches; they
are blocked on PR review.)

---

### Medium — M-001: Non-constant-time admin token comparison (carry-over)

**File:** `app/main.py:301`
**Risk:** `_admin_auth` compares the incoming bearer token against `CLEARVIEW_ADMIN_TOKEN` using
Python's `!=` operator. CPython short-circuits on the first differing byte, enabling a remote
timing oracle. An attacker with sufficient request volume could recover the admin token
character-by-character.

```python
# Before (on main)
if token != expected:
    raise HTTPException(status_code=401, detail="invalid admin token")

# After (this branch)
if not secrets.compare_digest(token, expected):
    raise HTTPException(status_code=401, detail="invalid admin token")
```

**Status:** **Fixed in this run** (commit on `security/audit-2026-10-15`).
*Note: identical fix was applied on each of the 30 preceding audit branches; blocked from `main`
by the process failure at H-001.*

---

### Medium — M-002: Dockerfile runs container as root (carry-over)

**File:** `Dockerfile`
**Risk:** No `USER` directive. The application process runs as root inside the container. A
code-execution vulnerability in FastAPI, litellm, or any dependency would immediately grant root
privileges, bypassing normal container isolation.

**Remediation applied:**
```dockerfile
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser
...
USER appuser
```

**Status:** **Fixed in this run** (commit on `security/audit-2026-10-15`).
*Note: same fix was applied on 30 preceding audit branches; blocked from `main` by H-001.*

---

### Low — L-001: Vulnerable pip and setuptools in Docker base layer (new)

**File:** `Dockerfile` (via `python:3.12-slim` base image)
**Risk:** The base image ships `pip 24.0` and `setuptools 79.0.1`, both of which have known CVEs:

| Package    | Version | CVE IDs                                                     | Fix Version |
|------------|---------|-------------------------------------------------------------|-------------|
| pip        | 24.0    | PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-196,          | ≥ 26.2      |
|            |         | PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-3721          |             |
| setuptools | 79.0.1  | PYSEC-2026-3447                                             | ≥ 83.0.0    |

These packages are installation-time tools; they are not called by application code at runtime.
Exploitability requires an attacker to supply a malicious package during `pip install` (e.g.
supply-chain scenario), not via normal API traffic. Severity is Low.

**Remediation applied:** Added `pip install --upgrade pip setuptools` before the application
install in the Dockerfile, ensuring the build layer uses fixed versions.

**Status:** **Fixed in this run** (commit on `security/audit-2026-10-15`).

---

## Deferred Items

| ID    | Description                              | Status                              | First Reported |
|-------|------------------------------------------|-------------------------------------|----------------|
| H-001 | 30+ unmerged audit PRs — fixes not in main | Deferred — requires human PR merge | 2026-09-14     |

---

## Positive Observations

- **Token generation** — `teams.create()` uses `secrets.token_hex(16)`, `chat.py` uses
  `secrets.token_hex(8)`. Cryptographically random. ✓
- **SQL parameterization** — All queries in `telemetry.py`, `teams.py`, `cache.py`, `chat.py`
  use `?` bound parameters. No f-string or concatenation queries found. ✓
- **No shell=True** — CLI adapters (`claude_cli.py`, `codex_cli.py`, `gemini_cli.py`) use
  `asyncio.create_subprocess_exec`. No shell injection surface. ✓
- **No dangerous builtins** — No `eval(`, `exec(`, `pickle`, or unsafe `yaml.load` in
  application code. ✓
- **No tracked secrets** — `git ls-files` contains only `.env.example` (no real values). Grep
  for `sk-`, `AKIA`, `ghp_` across tracked files returned only `sk-x` / `sk-test` test
  fixtures in `tests/`. ✓
- **Route authentication gates** — All `/admin/*` routes call `_admin_auth`. All `/chat/*`
  routes (except the public login page, logout, and the rendered `/chat` page) call
  `_require_chat_team`. Public API routes (`/v1/chat/completions`, `/v1/messages`,
  `/v1/responses`, Gemini shims) call `_client_key_allowed` and `_resolve_team` as intended.
  `/v1/models`, `/health`, `/metrics`, and `/feedback` are intentionally open (documented). ✓
- **Cookie security** — `/chat/login` sets `httponly=True, samesite="lax"`. ✓
- **Project runtime dependencies** — `pip-audit` found zero CVEs in fastapi, uvicorn, litellm,
  pydantic, pyyaml, jinja2, httpx, and transitive dependencies. ✓

---

## Remediation Status

| ID    | Description                                | Status                        |
|-------|--------------------------------------------|-------------------------------|
| H-001 | 30+ unmerged audit PRs                     | Deferred — human action needed |
| M-001 | Non-constant-time admin token compare      | Fixed (this run)              |
| M-002 | Dockerfile non-root user                   | Fixed (this run)              |
| L-001 | Vulnerable pip/setuptools in Docker layer  | Fixed (this run)              |
