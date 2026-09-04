# ClearView Security Audit — 2026-09-04

**Auditor:** Automated daily audit (Claude Code scheduled task)
**Branch audited:** `main` @ HEAD (`e8ad2e3`)
**Audit branch:** `security/audit-2026-09-04`
**Previous audit:** None (first audit)

---

## Severity Summary

| Severity | New | Fixed this run | Deferred |
|----------|-----|---------------|----------|
| High     | 1   | 1             | 0        |
| Medium   | 2   | 1             | 1        |
| Low      | 2   | 0             | 2        |
| Info     | 1   | 0             | 1        |

---

## Findings

### HIGH

#### H-1 — Dockerfile runs container as root [FIXED]

**File:** `Dockerfile`
**Risk:** The image had no `USER` directive, causing the process to run as UID 0 inside the container. A container escape or path traversal would grant root access to the host kernel surface. Best-practice and most base-image policies require a non-root user.
**Remediation applied:** Added `USER nobody` before `CMD` in `Dockerfile`.

---

### MEDIUM

#### M-1 — Non-constant-time admin token comparison (timing oracle) [FIXED]

**File:** `app/main.py:301`
**Risk:** `_admin_auth` compared the bearer token with `token != expected`. Python's string `!=` short-circuits on the first differing byte, leaking timing information that allows an attacker on the same network to enumerate a weak `CLEARVIEW_ADMIN_TOKEN` character-by-character (timing side-channel). The token is the sole access gate to all `/admin/*` endpoints including team management and policy tuning.
**Remediation applied:** Replaced `token != expected` with `not secrets.compare_digest(token, expected)` in `app/main.py`. Added `import secrets` to the module imports.

#### M-2 — `/metrics` endpoint unauthenticated [DEFERRED]

**File:** `app/main.py:2538`
**Risk:** The Prometheus `/metrics` endpoint returns aggregate request counts, cost data, provider names, team IDs (hashed), and drift percentages to any unauthenticated caller. This is operational intelligence that could assist an attacker profiling the deployment (identifying providers, traffic patterns, teams).
**Why deferred:** Adding `_admin_auth` to `/metrics` is a behaviour-changing fix — it would break existing Prometheus scrape configs. The correct design is a dedicated, separately configured scrape token (e.g. `CLEARVIEW_METRICS_TOKEN`) or network-level access restriction (firewall / reverse-proxy allow-list). Flagged for operator action.
**Proposed design:** Add a `CLEARVIEW_METRICS_TOKEN` env var; if set, `/metrics` requires `Authorization: Bearer <token>` (constant-time compared). Scrape configs receive this token. If unset, `/metrics` remains open (preserving today's default). Implementation is one if-block in `metrics()`, mirroring `_admin_auth`.

---

### LOW

#### L-1 — `/v1/models` unauthenticated model-catalog disclosure [DEFERRED]

**File:** `app/main.py:364`
**Risk:** Any caller can enumerate all configured provider model IDs (e.g. `anthropic/claude-opus-4-7`, `openai/gpt-4o`) without authentication. This leaks which LLM providers are in use and which models are licensed. Low severity because no cost is incurred and the model IDs are not secret in most deployments.
**Why deferred:** This endpoint must be open by the OpenAI `/v1/models` spec so that IDE clients (Continue, Cursor, etc.) can discover models without a key. Restricting it would break compatibility. Operator decision: gate with `CLEARVIEW_CLIENT_KEYS` if needed.

#### L-2 — docker-compose publishes port on all interfaces [DEFERRED]

**File:** `docker-compose.yml:8`
**Risk:** `ports: "8000:8000"` binds to `0.0.0.0`, exposing the gateway to all network interfaces on the Docker host. On a cloud VM with a public IP and no external firewall, this means ClearView is reachable from the internet without authentication (unless `CLEARVIEW_ADMIN_TOKEN` / `CLEARVIEW_CLIENT_KEYS` are set). In dev mode (no tokens set), all admin endpoints are open.
**Why deferred:** Changing to `"127.0.0.1:8000:8000"` is a behaviour-changing default that breaks legitimate multi-host deployments and Docker Swarm patterns. Operator guidance: add a firewall rule restricting port 8000, or place ClearView behind a reverse proxy (nginx/caddy) that handles TLS and IP allow-listing.

---

### INFO / CVE NOTE

#### I-1 — pip and setuptools CVEs in venv (build-time tooling only) [DEFERRED]

**Source:** `pip-audit` run on project venv
**Affected packages (runtime deps):** None. All CVEs are in `pip 24.0` (PYSEC-2026-196, -1795, -1796, -2875, -2876, -3721 → fix: `pip>=26.1.2`) and `setuptools 79.0.1` (PYSEC-2026-3447 → fix: `setuptools>=83.0.0`). These packages are not runtime dependencies of ClearView; they are only present in the development/CI venv.
**Risk to ClearView:** Low in isolation — pip/setuptools are not shipped in the Docker image (`pip install .` runs at build time and is not included in the final image layer). Risk applies to CI/CD pipelines that use a compromised pip version with malicious packages.
**Recommended action:** Pin `pip>=26.1.2` and `setuptools>=83.0.0` in CI environment setup scripts. No code change needed for the runtime image.

---

## Carried-over Deferred Items

None (first audit).

---

## Positive Observations

- **Token generation is secure:** Team tokens use `secrets.token_hex(16)` (cryptographically random, 128-bit entropy). Conversation IDs use `secrets.token_hex(8)`. Good.
- **SQL injection: no risk found.** All user-supplied values (`session`, `team`, `team_id`, filter params) flow through parameterized `?` placeholders. F-string interpolated parts of query strings (`{where}`, `{where_session}`, `{', '.join(sets)}`) are built from whitelisted hardcoded column names, not user input. SQLite is used consistently.
- **No dangerous subprocess patterns.** `shell=True`, `os.system`, `eval()`, `exec()`, `pickle`, and unsafe `yaml.load` are absent from all app and provider source files. CLI providers use `asyncio.create_subprocess_exec` (argument-list form, no shell injection surface).
- **No tracked secrets.** `git ls-files` finds no `.env`, `.key`, `.pem`, or credential files. No live-looking API keys (`sk-`, `AKIA`, `ghp_`) appear in any tracked file.
- **Admin routes uniformly gated.** Every `/admin/*` route calls `_admin_auth(request)`. `/chat/*` routes use `_require_chat_team`. The `/feedback` endpoint is intentionally open (per docstring: callers rate their own responses by request_id).
- **Cookie security:** The `/chat/login` session cookie is set with `httponly=True` and `samesite="lax"`, mitigating XSS-based session theft and CSRF.
- **Team token redaction:** The team list endpoint (`GET /admin/teams`) returns only a truncated `id_short` prefix, never the full bearer token. Full token is returned only at creation time.

---

## Remediation Status

| Finding | Status | Commit |
|---------|--------|--------|
| H-1 Dockerfile root USER | Fixed | this PR |
| M-1 Non-constant-time comparison | Fixed | this PR |
| M-2 /metrics unauthenticated | Deferred (proposed design above) | — |
| L-1 /v1/models disclosure | Deferred (by-design per OpenAI spec) | — |
| L-2 docker-compose all-interface bind | Deferred (operator config action) | — |
| I-1 pip/setuptools CVEs (tooling only) | Deferred (CI upgrade action) | — |
