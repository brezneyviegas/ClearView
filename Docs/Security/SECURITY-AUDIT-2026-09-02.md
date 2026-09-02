# Security Audit — 2026-09-02

**Auditor:** Automated daily security audit (scheduled routine)  
**Repository:** ClearView (`brezneyviegas/clearview`)  
**Branch audited:** `main` (HEAD `e8ad2e3`)  
**Audit date:** 2026-09-02  
**Previous audit:** None (this is the first daily audit)

---

## Severity Summary

| Severity | New Findings | Fixed This Run | Deferred |
|----------|:---:|:---:|:---:|
| High     | 1   | 1   | 0   |
| Medium   | 2   | 2   | 0   |
| Low      | 2   | 0   | 2   |
| **Total**| **5** | **3** | **2** |

---

## High Findings

### H1 — Unauthenticated `/metrics` endpoint exposes sensitive telemetry
**Location:** `app/main.py` — `GET /metrics`  
**Risk:** The Prometheus-format `/metrics` endpoint exposes `clearview_requests_total` (tagged by `tier`, `provider`, `status`, and `team_id`), cumulative USD cost counters, total token counts, and savings rate. No authentication gate was present. Any network-accessible attacker could enumerate team identifiers, provider costs, and usage patterns without credentials.  
**Remediation:** Added `_admin_auth(request)` call at the top of the `metrics()` handler (mirrors all other `/admin/*` endpoints). The endpoint now requires a valid `CLEARVIEW_ADMIN_TOKEN` bearer when that env var is set.  
**Status:** ✅ Fixed in this run (`security/audit-2026-09-02`)

---

## Medium Findings

### M1 — Non-constant-time bearer token comparison in `_admin_auth`
**Location:** `app/main.py:301` — `_admin_auth()`  
**Risk:** The original code compared the incoming bearer token to the expected value with `token != expected` (Python's `!=` operator). String comparison short-circuits on the first differing byte, creating a timing oracle that allows an attacker with many requests and precise timing to recover the admin token character by character. The risk is lower over a wide-area network due to jitter, but is exploitable on low-latency links or in co-hosted environments.  
**Remediation:** Replaced `token != expected` with `not secrets.compare_digest(token, expected)`. Added `import secrets` to the module imports.  
**Status:** ✅ Fixed in this run (`security/audit-2026-09-02`)

### M2 — Dockerfile runs container process as root (no `USER` directive)
**Location:** `Dockerfile`  
**Risk:** Without a `USER` directive, the Python process in the container runs as UID 0 (root). A container escape vulnerability or path traversal in the application code would grant an attacker full root privileges inside the container, increasing blast radius.  
**Remediation:** Added a `RUN useradd --system --no-create-home --uid 1001 clearview` step, `chown`ed the `/data` volume mount point, and inserted `USER clearview` before the `EXPOSE`/`CMD` directives.  
**Status:** ✅ Fixed in this run (`security/audit-2026-09-02`)

---

## Low Findings

### L1 — Ollama service publishes port 11434 to all interfaces in `docker-compose.yml`
**Location:** `docker-compose.yml` — `ollama` service  
**Risk:** When running with `--profile local`, the Ollama API is bound to `0.0.0.0:11434`, exposing it to every interface on the host without authentication. Any host on the local network (or beyond, if the host is internet-facing) can submit arbitrary prompts to the local model. The service is hidden behind the `local` profile and is disabled by default, reducing exposure, but the binding is still unsafe when used.  
**Remediation proposed:** Bind the port to loopback only: `"127.0.0.1:11434:11434"`. Alternatively, remove the port mapping entirely if the Ollama API is only needed by the `clearview` container (they share a Docker network and can communicate by service name).  
**Status:** ⏸ Deferred — requires a design decision about whether local-network access to Ollama is intentional for the `local` profile.

### L2 — Build tool CVEs in venv (`pip` 24.0, `setuptools` 79.0.1)
**Location:** Python venv / build environment  
**Risk (pip):** `pip` 24.0 has multiple known vulnerabilities (PYSEC-2026-196, PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-3721). Fix version: 26.2+.  
**Risk (setuptools):** `setuptools` 79.0.1 has a known vulnerability (PYSEC-2026-3447). Fix version: 83.0.0.  
**Note:** These are build/packaging tools, not runtime dependencies of ClearView itself. They are not loaded when the application serves requests. The Dockerfile uses `RUN pip install --no-cache-dir .` and does not retain pip in the final image layers beyond the pip binary itself.  
**Remediation proposed:** Upgrade pip and setuptools in the Dockerfile base layer (`RUN pip install --upgrade pip setuptools`), and pin them in CI to avoid re-introduction.  
**Status:** ⏸ Deferred — build-tool only; no runtime exposure. Recommended for next scheduled maintenance window.

---

## Checklist Results

| Check | Result |
|---|---|
| Auth coverage: all `@app.{get,post,delete}` routes gated | ✅ Pass (after H1 fix) |
| Injection: `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, `yaml.load` | ✅ Clean — only `asyncio.create_subprocess_exec` (safe, no shell) |
| SQL parameterization | ✅ Clean — f-string WHERE clauses use hardcoded column names; all user values are parameterized with `?` |
| Tracked secrets: `.env`, key/pem files | ✅ Clean — only `.env.example` and `clearview-client.env.example` tracked (no real values) |
| Live keys in tracked files (`sk-`, `AKIA`, `ghp_`, long hex) | ✅ Clean |
| Secret comparisons: `secrets.compare_digest` | ✅ Pass (after M1 fix) |
| Token generation: `secrets` module | ✅ Pass — team IDs use `secrets.token_hex`, chat IDs use `secrets.token_hex` |
| Dockerfile `USER` (non-root) | ✅ Pass (after M2 fix) |
| docker-compose loopback binding | ⚠️ Partial — `clearview` port 8000 is `0.0.0.0` (intentional for gateway use); `ollama` 11434 is `0.0.0.0` (L1 — deferred) |
| Dependency CVEs (pip-audit) | ⚠️ Build tools only (L2 — deferred) |

---

## Positive Observations

- **Auth architecture is solid**: all `/admin/*` endpoints call `_admin_auth`; all `/chat/*` data endpoints call `_require_chat_team`; LLM proxy endpoints call `_client_key_allowed`. The function-based guard pattern is consistent and easy to audit.
- **Token generation uses `secrets`**: team tokens (`cv_team_` prefix + `secrets.token_hex(16)`) and chat IDs (`cv_chat_` + `secrets.token_hex(8)`) are both generated with the cryptographically secure `secrets` module.
- **No `shell=True` subprocess usage**: CLI provider integrations use `asyncio.create_subprocess_exec` (safe argument list), not shell string expansion.
- **SQL is parameterized correctly**: dynamic WHERE clauses in `telemetry.py` and `teams.py` use f-strings only for hardcoded column names; all user-supplied values go through SQLite's `?` placeholder binding.
- **No tracked secrets**: only example env files are tracked; `.env` and real key files are gitignored.
- **Redaction on team token listing**: `GET /admin/teams` returns truncated `id_short` (first 18 chars + `...`) rather than the full bearer token, preventing token leakage in UI logs.

---

## Carried-Over Deferred Findings

None — this is the first audit.

---

## Remediation Status

| Finding | Fix | Branch |
|---|---|---|
| H1 — `/metrics` no auth | `_admin_auth(request)` added | `security/audit-2026-09-02` |
| M1 — non-constant-time compare | `secrets.compare_digest` | `security/audit-2026-09-02` |
| M2 — Dockerfile runs as root | `USER clearview` added | `security/audit-2026-09-02` |
| L1 — Ollama port exposure | Deferred (design decision) | — |
| L2 — Build tool CVEs | Deferred (no runtime exposure) | — |
