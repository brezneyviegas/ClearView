# Security Audit — 2026-09-17

**Auditor:** Automated daily audit (Claude Code scheduled task)
**Branch audited:** `main` @ `e8ad2e3`
**Previous audit:** None (first run — no baseline to carry over)
**Audit scope:** Full codebase sweep (no prior audit date to diff from)

---

## Severity Summary

| Severity | New | Fixed This Run | Deferred |
|----------|-----|---------------|----------|
| High     | 2   | 2             | 0        |
| Medium   | 2   | 0             | 2        |
| Low      | 1   | 0             | 1        |
| Info     | 2   | 0             | 2        |

---

## HIGH Findings

### H-1 · Non-constant-time admin token comparison — **FIXED**

**Location:** `app/main.py:300` (`_admin_auth`)
**Risk:** The original check `token != expected` uses Python's default string comparison, which short-circuits on the first differing byte. An attacker who can send many timed requests to any admin endpoint can perform a timing side-channel attack to recover the `CLEARVIEW_ADMIN_TOKEN` value character by character.
**Remediation applied:** Replaced with `secrets.compare_digest(token, expected)`, which runs in constant time regardless of where strings diverge. Added `import secrets` to the module-level imports.
**Test result:** 359/359 tests pass after fix.

---

### H-2 · Dockerfile runs container as root — **FIXED**

**Location:** `Dockerfile` (no `USER` directive)
**Risk:** If the FastAPI process is exploited, the attacker runs as `root` inside the container. Any container-escape or volume-mount misconfiguration gives full host access. CIS Docker Benchmark CIS 4.1 requires a non-root user.
**Remediation applied:**
```dockerfile
RUN adduser --disabled-password --gecos "" clearview \
    && mkdir -p /data \
    && chown clearview /data
USER clearview
```
Added before `VOLUME /data` so the data directory is pre-created with correct ownership before the mount point is declared.
**Test result:** Unit tests are process-level and not affected by the USER change.

---

## MEDIUM Findings

### M-1 · `/metrics` endpoint unauthenticated — **DEFERRED**

**Location:** `app/main.py:2538–2570` (`@app.get("/metrics")`)
**Risk:** The Prometheus metrics endpoint is publicly accessible without any bearer token. It exposes:
- Per-provider call counts, cost totals, token totals
- Tier distribution (cheap/mid/frontier/cache)
- Team ID label in `clearview_requests_total`

An external attacker learns which LLM providers are active, traffic volumes, and team identifiers. In a shared-network deployment this leaks non-trivial operational intelligence.
**Proposed design:** Gate with `_admin_auth` (same as all other `/admin/*` endpoints). Prometheus scrapers must then pass `Authorization: Bearer <CLEARVIEW_ADMIN_TOKEN>` — standard in most scrape configs (`bearer_token` field). Alternatively, expose metrics only on a separate internal port or require an allowlisted CIDR. Deferred because adding auth changes scraping configuration for existing deployments.

---

### M-2 · Ollama port published to all interfaces in docker-compose — **DEFERRED**

**Location:** `docker-compose.yml:22` (`ports: - "11434:11434"`)
**Risk:** When the `local` profile is used, the Ollama service binds `0.0.0.0:11434`, making the model server reachable from other hosts on the local network. Ollama has no authentication; any host that can reach port 11434 can query models and enumerate installed model names.
**Proposed design:** Change to `"127.0.0.1:11434:11434"` so Ollama is loopback-only. The ClearView container connects via the `OLLAMA_BASE_URL=http://ollama:11434` internal Docker network name (not the published port), so this change is safe without further modification. Deferred because it requires verifying no operator relies on direct external Ollama access.

---

## LOW Findings

### L-1 · `/v1/models` open without any auth gate — **DEFERRED**

**Location:** `app/main.py:364–376` (`@app.get("/v1/models")`)
**Risk:** Returns the list of virtual ClearView models plus the underlying provider model IDs (e.g. `anthropic/claude-sonnet-4-6`, `openai/gpt-4o`). No auth is checked. This exposes which providers and model versions are configured, which aids an attacker mapping the environment. The OpenAI `/v1/models` spec does not require this endpoint to be open; it is typically authenticated.
**Proposed design:** Add `_client_key_allowed(request)` (same gate used on `/v1/chat/completions`). Deferred because some IDE integrations call this endpoint before sending a bearer token to populate model dropdowns.

---

## Dependency CVEs

Found via `pip-audit` on the installed environment:

| Package    | Version | CVE IDs                                                    | Fix Version |
|------------|---------|-------------------------------------------------------------|-------------|
| pip        | 24.0    | PYSEC-2026-1795, -1796, -196, -2875, -2876, -3721          | 26.2.1      |
| setuptools | 79.0.1  | PYSEC-2026-3447                                             | 83.0.0      |

**Notes:**
- These vulnerabilities affect the `pip` and `setuptools` packages bundled in the Docker base image (`python:3.12-slim`). They affect the *install-time* tooling, not the running application's import surface. The application itself does not import `pip` or `setuptools` at runtime.
- Recommended remediation: add `RUN pip install --upgrade pip setuptools` to the Dockerfile after the base image pulls, so the builder layer uses patched versions. This is a low-urgency hardening item (install-time attack surface only).
- Not fixed this run: the Dockerfile change would be mechanical but the CVE scope (install-time only) puts risk below the High threshold; tracking as informational.

---

## Carried-Over Deferred Items

None — this is the first audit run.

---

## Positive Observations

- **Token generation uses `secrets` module:** `teams.py:117` uses `secrets.token_hex(16)` and `chat.py:62` uses `secrets.token_hex(8)`. No use of `random` for security-sensitive values.
- **No tracked secrets:** `git ls-files` found no `.env`, `.pem`, or key files. No live-looking API key patterns (`sk-`, `AKIA`, `ghp_`) in tracked files.
- **Subprocess usage is safe:** All subprocess calls in `app/providers/` use `asyncio.create_subprocess_exec` (not `shell=True`), so shell injection is not possible.
- **SQL is fully parameterized:** All SQLite queries in `app/main.py`, `app/telemetry.py`, and related modules use `?` placeholders. No f-string interpolation of user-controlled values into SQL.
- **Admin routes consistently gated:** All `/admin/*` routes call `_admin_auth(request)` at their first line.
- **Chat routes consistently gated:** All `/chat/*` routes requiring auth call `_require_chat_team(request)`.
- **Cookie attributes set correctly:** The `cv_session` cookie uses `HttpOnly=True, SameSite=lax`, preventing JS theft and CSRF from cross-origin posts.
- **Team token redaction on list:** `_team_to_redacted_dict` never returns the full team Bearer token on list views.

---

## Remediation Status

| Finding | Status       | PR / Branch                     |
|---------|--------------|---------------------------------|
| H-1     | Fixed        | `security/audit-2026-09-17`     |
| H-2     | Fixed        | `security/audit-2026-09-17`     |
| M-1     | Deferred     | Proposed design above           |
| M-2     | Deferred     | Proposed design above           |
| L-1     | Deferred     | Proposed design above           |
| CVEs    | Informational | Proposed Dockerfile upgrade      |
