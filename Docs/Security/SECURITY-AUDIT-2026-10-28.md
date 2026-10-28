# Security Audit — 2026-10-28

**Repository:** ClearView (Python/FastAPI LLM gateway)
**Auditor:** Automated daily audit routine
**Branch audited:** main @ e8ad2e3
**Previous audit:** None (first run)

---

## Severity Summary

| Severity | Count | Fixed this run | Deferred |
|----------|-------|---------------|----------|
| High     | 0     | —             | —        |
| Medium   | 2     | 2             | 0        |
| Low      | 4     | 3             | 1        |
| Info     | 2     | —             | —        |

---

## Medium Findings

### M-1 · Non-constant-time admin token comparison — **FIXED**

- **File:** `app/main.py:299` (`_admin_auth`)
- **Risk:** The original comparison `token != expected` is a standard Python string equality check and is not constant-time. An attacker who can make many rapid requests to an admin endpoint could exploit timing differences to brute-force the admin token one character at a time (timing oracle). Although a long token makes this extremely costly in practice, it violates constant-time comparison requirements for secrets.
- **Remediation applied:** Replaced `token != expected` with `not secrets.compare_digest(token, expected)`. Added `import secrets` to the module imports.

---

### M-2 · `/metrics` endpoint unauthenticated — **FIXED**

- **File:** `app/main.py:2538` (`metrics`)
- **Risk:** The Prometheus `/metrics` route had no authentication. It exposed per-team spend, total costs, token counts, model/tier usage patterns, and drift percentages to any unauthenticated caller. In a shared-network deployment (e.g. the default `docker-compose` setup, which publishes port 8000 on all interfaces), this constitutes an information-disclosure risk — competitors or other tenants on the same network could observe billing data and infer business-sensitive routing patterns.
- **Remediation applied:** Added `_admin_auth(request)` call to the `metrics` handler (consistent with all other admin endpoints). The handler signature was updated to accept `request: Request`.
- **Note:** If you run a Prometheus scraper, update its scrape config to include `Authorization: Bearer <CLEARVIEW_ADMIN_TOKEN>`. When `CLEARVIEW_ADMIN_TOKEN` is unset (dev mode), the endpoint remains open.

---

## Low Findings

### L-1 · Dockerfile runs as root — **FIXED**

- **File:** `Dockerfile`
- **Risk:** The Dockerfile had no `USER` directive, causing the container process to run as UID 0 (root). A container escape or application-level RCE would immediately yield root privileges on the host (in non-rootless runtimes).
- **Remediation applied:** Added `RUN useradd --system --no-create-home clearview` and `USER clearview` before the `VOLUME` and `CMD` lines.

---

### L-2 · Ollama bound to all host interfaces — **FIXED**

- **File:** `docker-compose.yml`
- **Risk:** When using the `local` profile, the ollama container mapped port 11434 as `"11434:11434"`, binding on `0.0.0.0`. This exposes the unauthenticated Ollama API (which accepts arbitrary model pulls, completions, and admin operations) to any host on the same network segment.
- **Remediation applied:** Changed the port binding to `"127.0.0.1:11434:11434"` so the service is accessible only from localhost.

---

### L-3 · Vulnerable transitive dependencies (pip-audit) — **DEFERRED**

- **Packages affected:**
  - `filelock==3.32.7` → CVE-2026-112940, CVE-2026-112913, CVE-2026-112907 (fix: 4.2.1)
    - ReadWriteLock/AsyncReadWriteLock (SQLite-backed) and Windows SoftFileLock race conditions.
    - `filelock` is pulled in transitively via `litellm`. **Linux-hosted deployments are not affected by the Windows-specific CVEs (CVE-2026-112913, CVE-2026-112907).** CVE-2026-112940 applies to the SQLite-backed lock on any OS — risk is data corruption / denial of service inside filelock's own locking logic, not a remote code execution path in ClearView.
  - `pip==24.0` → PYSEC-2026-1795, 1796, 2875, 2876, 196, 3721 (fix: 26.2.2+)
    - Tar archive symlink traversal, malicious wheel extraction, ZIP/tar concatenation bypass, console_scripts path handling, doubly-encoded URL attacks. These affect the `pip` build tool, not the running application. Risk is limited to CI and developer machines performing `pip install`.
  - `setuptools==79.0.1` → PYSEC-2026-3447, GHSA-xmj3-9gf7-h52w (fix: 84.1.0)
    - Source-distribution `.dist-info/name` path traversal (Windows). Affects the build/packaging workflow, not the runtime.
- **Proposed remediation:** Pin `litellm` to a version that pulls `filelock>=4.2.1`, or add an explicit `filelock>=4.2.1` constraint to `pyproject.toml`. Upgrade `pip` and `setuptools` in CI base images. These are not auto-applied because the `litellm` compatibility with `filelock 4.x` has not been verified and a version bump could alter routing behavior.

---

### L-4 · `random.random()` used for shadow-sampling — **INFO (not a finding)**

- **File:** `app/main.py:1648`, `app/main.py:1667`
- `random.random()` is used to sample auto-shadow and provider-shadow routing decisions. This is not a security-sensitive decision (it controls which monitoring calls fire, not which tokens are accepted). No remediation required; noted for completeness.

---

## Informational (open by design)

### I-1 · `/v1/models` unauthenticated

`GET /v1/models` returns ClearView's virtual model list without authentication. This matches the OpenAI API contract and is intentional — IDE plugins enumerate models on startup without a team token. Acceptable.

### I-2 · `/feedback` unauthenticated

`POST /feedback` accepts thumbs-up/down ratings keyed by `request_id` without admin auth. The endpoint is correctly documented as open by design ("callers rate their own responses"). Acceptable.

---

## Positive Observations

- All `/admin/*` routes correctly gate on `_admin_auth`.
- All `/chat/*` routes (except the public `/chat` page, login, and logout) gate on `_require_chat_team`.
- SQL throughout uses parameterized `?` placeholders; WHERE clauses are built from hard-coded predicate strings, not user input — no SQL injection surface found.
- No `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, or unsafe `yaml.load(` calls found.
- No tracked secrets: `git ls-files` returned no `.env`, `.key`, `.pem`, or `.secret` files; grep found no `sk-*`, `AKIA*`, `ghp_*`, or `*_KEY=` / `*_TOKEN=` assignments in tracked files.
- Team tokens are minted with `secrets.token_hex(16)` (32 hex chars of entropy).
- Chat session cookie is set `httponly=True, samesite="lax"` — correct.
- Team token list view redacts to the first 18 characters, preventing full token leakage via the admin explorer.
- `asyncio.create_subprocess_exec` (not `shell=True`) used in all CLI provider adapters.

---

## Remediation Status

| Finding | Status | Commit |
|---------|--------|--------|
| M-1 Non-constant-time admin token | Fixed | security/audit-2026-10-28 |
| M-2 `/metrics` unauthenticated | Fixed | security/audit-2026-10-28 |
| L-1 Dockerfile runs as root | Fixed | security/audit-2026-10-28 |
| L-2 Ollama on 0.0.0.0 | Fixed | security/audit-2026-10-28 |
| L-3 Vulnerable dependencies | Deferred — verify litellm/filelock 4.x compat | — |

---

*Report generated by automated daily security routine. No `.env` contents or database token values are included.*
