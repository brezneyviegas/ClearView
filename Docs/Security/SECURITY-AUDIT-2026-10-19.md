# Security Audit — 2026-10-19

**Auditor:** Automated daily audit (Claude Code scheduled routine)
**Scope:** Full repository — first audit run, no prior baseline.
**Branch audited:** `main` (HEAD `e8ad2e3`)
**Audit branch:** `security/audit-2026-10-19`

---

## Severity Summary

| Severity | New | Fixed this run | Deferred | Carried Over |
|----------|-----|----------------|----------|--------------|
| High     | 1   | 1              | 0        | 0            |
| Medium   | 2   | 2              | 0        | 0            |
| Low      | 2   | 0              | 2        | 0            |

---

## High Findings

### H-1 — Non-constant-time admin token comparison ✅ FIXED

- **File:** `app/main.py:301`
- **Risk:** `token != expected` uses Python's short-circuit string equality, enabling timing side-channel attacks against `CLEARVIEW_ADMIN_TOKEN`. An attacker making repeated requests can measure response latency to recover the token byte-by-byte, bypassing the auth gate on all `/admin/*` routes.
- **Remediation applied:** Replaced with `not secrets.compare_digest(token, expected)`. Added `import secrets` to the module imports (`app/main.py`). All 359 tests pass.

---

## Medium Findings

### M-1 — Dockerfile runs as root (no USER directive) ✅ FIXED

- **File:** `Dockerfile`
- **Risk:** Without a `USER` instruction the container process runs as UID 0 (root). A container escape or RCE via the application grants full root on the container filesystem and increases blast radius for any supply-chain or dependency exploit.
- **Remediation applied:** Added a `clearview` system user (UID 1001) and `USER clearview` before the `CMD`. The `/data` volume directory is `chown`-ed to this user so the database remains writable. All 359 tests pass.

### M-2 — Ollama API published on all interfaces ✅ FIXED

- **File:** `docker-compose.yml:18`
- **Risk:** `ports: - "11434:11434"` binds Docker's port mapping to `0.0.0.0`, exposing the Ollama REST API on every network interface of the host. Ollama has no built-in authentication; any host reachable on port 11434 can enumerate models, run inference, and pull model weights at the operator's compute expense.
- **Remediation applied:** Changed to `"127.0.0.1:11434:11434"` so the binding is loopback-only. ClearView's own `OLLAMA_BASE_URL=http://ollama:11434` connects through the Docker internal network (container name DNS), unaffected by this change. All 359 tests pass.

---

## Low Findings (Deferred)

### L-1 — pip / setuptools CVEs in build tooling (deferred)

- **Tool:** `pip-audit`
- **Details:**
  - `pip 24.0` — PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-196, PYSEC-2026-3721 (fix: `pip >= 26.2`)
  - `setuptools 79.0.1` — PYSEC-2026-3447 (fix: `setuptools >= 83.0.0`)
- **Risk:** These affect the build/install toolchain, not the application runtime. The container image installs packages during `docker build` using the pinned pip version. No application-layer exploit path identified.
- **Deferred rationale:** Fixing requires updating the base image or pinning pip in the Dockerfile `RUN pip install` step. Carries a small risk of breaking dependency resolution. Operator should upgrade pip in the Dockerfile (`RUN pip install --upgrade pip`) at next planned image rebuild.
- **Proposed remediation:** Add `RUN pip install --upgrade pip` before the `RUN pip install .` step in the `Dockerfile`.

### L-2 — `/metrics` endpoint unauthenticated (deferred)

- **File:** `app/main.py:2538`
- **Risk:** The Prometheus `/metrics` endpoint is open (no `_admin_auth`). It exposes `team_id` label values, request counts, cost totals, and token throughput to any caller. In a shared-network or public deployment this leaks operational intelligence about team activity patterns.
- **Deferred rationale:** Adding auth to `/metrics` is a behavior-changing fix — Prometheus scrapers require a matching `bearer_token` or `basic_auth` config update. The correct fix involves coordinating with operators' monitoring configuration. Listed here for operator awareness.
- **Proposed remediation:** Gate with `_admin_auth(request)` and update Prometheus scrape job to pass `CLEARVIEW_ADMIN_TOKEN` as a bearer token, or restrict at the reverse-proxy level.

---

## Carried-Over Deferred Items

None — first audit run.

---

## Checklist Results

| Item | Result |
|------|--------|
| Auth coverage: all `@app.get/post/delete` routes gated or documented | ✅ Pass — `/v1/models`, `/feedback`, `/health`, `/chat` login/logout intentionally open; `/metrics` flagged as L-2 |
| Injection: `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, unsafe `yaml.load` | ✅ Pass — `subprocess.create_subprocess_exec` (not shell=True) used in CLI providers |
| SQL parameterization: no f-string interpolation of user data into queries | ✅ Pass — all queries use `?` placeholders; `session`, `team`, `window` params are validated/typed before use |
| Tracked secrets: no `.env`, `.key`, `.pem` files; no live keys in tracked files | ✅ Pass |
| Token handling: comparisons use `secrets.compare_digest`; generation uses `secrets` module | ✅ Pass (after H-1 fix) |
| Container: Dockerfile has non-root USER | ✅ Pass (after M-1 fix) |
| Container: docker-compose doesn't publish unauthenticated services beyond loopback | ✅ Pass (after M-2 fix) |
| Dependencies: pip-audit | ⚠️ 7 CVEs in build tooling (pip, setuptools) — see L-1 |

---

## Positive Observations

- Team tokens (`cv_team_*`) and chat IDs are generated with `secrets.token_hex` — cryptographically sound.
- All `/admin/*` endpoints consistently call `_admin_auth`.
- All `/chat/*` data endpoints (conversations, messages, send) consistently call `_require_chat_team`.
- SQL queries across `telemetry.py`, `teams.py`, `chat.py`, `cache.py` uniformly use parameterized `?` placeholders.
- No hardcoded credentials, API keys, or `.env` files tracked in git.
- Subprocess calls in CLI providers use `asyncio.create_subprocess_exec` (not `shell=True`), preventing shell injection.
- `/chat/login` sets the session cookie `HttpOnly=True, SameSite=Lax`, appropriate defenses against XSS token theft and CSRF.

---

## Remediation Status

| Finding | Status | Commit |
|---------|--------|--------|
| H-1: Non-constant-time token comparison | Fixed | this PR |
| M-1: Dockerfile runs as root | Fixed | this PR |
| M-2: Ollama ports on all interfaces | Fixed | this PR |
| L-1: pip/setuptools CVEs | Deferred — next image rebuild | — |
| L-2: `/metrics` unauthenticated | Deferred — requires scraper config coordination | — |
