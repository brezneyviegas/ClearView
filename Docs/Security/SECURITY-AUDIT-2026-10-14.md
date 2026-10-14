# ClearView Security Audit — 2026-10-14

**Auditor:** Automated daily audit (scheduled)
**Branch audited:** `main` @ `e8ad2e3`
**Previous report:** None (first audit)

---

## Severity Summary

| Severity | New | Fixed This Run | Deferred | Total Open |
|----------|-----|----------------|----------|------------|
| High     | 0   | 0              | 0        | 0          |
| Medium   | 2   | 2              | 0        | 0          |
| Low      | 0   | 0              | 0        | 0          |

---

## Findings

### Medium — M-001: Non-constant-time admin token comparison

**File:** `app/main.py:299`
**Risk:** `_admin_auth` compares the incoming bearer token with the expected
`CLEARVIEW_ADMIN_TOKEN` using Python's `!=` operator. String comparison in
CPython short-circuits on the first differing byte, making the comparison
vulnerable to a remote timing oracle. An attacker with enough requests could
recover the admin token character-by-character.

```python
# Before
if token != expected:
    raise HTTPException(status_code=401, detail="invalid admin token")
```

**Remediation:** Replace with `secrets.compare_digest(token, expected)`.
**Status:** **Fixed in this run** (commit on this branch).

---

### Medium — M-002: Dockerfile runs container as root

**File:** `Dockerfile`
**Risk:** No `USER` directive is present. The application process runs as
`root` inside the container. A vulnerability in FastAPI, litellm, or any
dependency that leads to code execution would immediately give an attacker
root privileges, bypassing the container's normal isolation layer.

**Remediation:** Add a dedicated non-root system user and switch to it before
the final `CMD`.

```dockerfile
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser
USER appuser
```

**Status:** **Fixed in this run** (commit on this branch).

---

## Deferred Items

None. (First audit — no carry-overs.)

---

## Positive Observations

- **Token generation** — `teams.create()` uses `secrets.token_hex(16)`, producing
  a cryptographically random 32-hex-char Bearer token. ✓
- **SQL parameterization** — All SQLite queries in `telemetry.py`, `teams.py`,
  `cache.py`, and `chat.py` use `?` placeholders with bound parameters. No
  f-string or string-concatenation queries were found interpolating external
  values. ✓
- **No `shell=True` subprocess usage** — The CLI provider adapters
  (`claude_cli.py`, `codex_cli.py`, `gemini_cli.py`) use
  `asyncio.create_subprocess_exec`, which never passes arguments through a
  shell. ✓
- **No dangerous builtins** — No `eval(`, `exec(`, `pickle`, or unsafe
  `yaml.load` calls found in application code. ✓
- **No tracked secrets** — `git ls-files` contains no `.env`, `.pem`, or `.key`
  files. A grep for `sk-`, `AKIA`, and `ghp_` patterns across tracked files
  returned no matches. ✓
- **Route authentication gates** — Every admin endpoint (`/admin/*`) calls
  `_admin_auth`. Every `/chat/*` endpoint (except the static `/chat` page)
  calls `_require_chat_team`. Public API routes (`/v1/chat/completions`,
  `/v1/messages`, `/v1/responses`, Gemini shims) call `_client_key_allowed`
  and `_resolve_team` as intended. `/health` and `/metrics` are intentionally
  open. ✓
- **Cookie security** — `/chat/login` sets `httponly=True, samesite="lax"`.
  The token is not readable by JavaScript. ✓
- **pip-audit** — Zero known CVEs found across all installed dependencies. ✓

---

## Remediation Status

| ID    | Description                           | Status        |
|-------|---------------------------------------|---------------|
| M-001 | Non-constant-time admin token compare | Fixed         |
| M-002 | Dockerfile non-root user              | Fixed         |
