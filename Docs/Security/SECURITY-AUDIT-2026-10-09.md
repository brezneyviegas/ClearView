# ClearView Security Audit — 2026-10-09

**Auditor:** Automated daily audit (Claude Code)
**Branch audited:** `main` @ `e8ad2e3`
**Previous report:** `Docs/Security/SECURITY-AUDIT-2026-10-08.md`

---

## Severity Summary

| Severity | New findings | Fixed this run | Deferred |
|----------|-------------|----------------|---------|
| High     | 0           | 1 (re-applied) | 0       |
| Medium   | 0           | 1 (re-applied) | 1       |
| Low      | 0           | 0              | 2       |

> **Note:** No new code was committed to `main` since the 2026-10-08 audit
> (HEAD is still `e8ad2e3`). However, the PR opened on 2026-10-08
> (`security/audit-2026-10-08`) was never merged, so the two fixes reported
> as FIXED in that run — H-1 and M-1 — were never applied to `main`. Both
> are re-applied on this run's branch (`security/audit-2026-10-09`).

---

## Findings Re-Applied From Prior Unmerged PR

### H-1 — Non-constant-time admin token comparison (RE-APPLIED)

**File/location:** `app/main.py:301` (`_admin_auth`)
**Risk:** Timing-attack against `CLEARVIEW_ADMIN_TOKEN`. A string `!=` comparison
short-circuits on the first differing byte, allowing a remote attacker on a
low-latency network to brute-force the token character-by-character via
response-time differences.
**Remediation applied:** Added `import secrets`; replaced
`if token != expected:` with `if not secrets.compare_digest(token, expected):`.
**Status: FIXED** (commit on this branch)

---

### M-1 — Dockerfile runs as root (RE-APPLIED)

**File/location:** `Dockerfile`
**Risk:** Container process runs as `root` (UID 0). A path-traversal or
deserialization bug in FastAPI/litellm could allow full host-level escape via
Docker socket mounts or other container-escape techniques. Defense-in-depth
requires a non-root process UID.
**Remediation applied:** Added `useradd -r -u 1001 -s /bin/false clearview`,
`mkdir -p /data && chown clearview /data` in the `RUN pip install` layer, and
`USER clearview` before `CMD`.
**Status: FIXED** (commit on this branch)

---

## Carried-Over Deferred Items

### M-2 — Unauthenticated `/metrics` endpoint exposes operational data (DEFERRED)

**File/location:** `app/main.py:2538` (`metrics`)
**Risk:** The Prometheus metrics endpoint (`/metrics`) is open to any caller
without bearer-token or network authentication. It reveals: total requests by
tier/provider/status/team_id, cumulative spend in USD, output-token counts, and
per-team identifiers in label values.
**Proposed design:** Gate the endpoint with `_admin_auth(request)` — add
`request: Request` parameter and call it. Alternatively, mount metrics on a
separate internal-only port. Deferred for operator review — adding the
`request` parameter changes the endpoint signature and may affect existing
Prometheus scrape configurations.
**Status: DEFERRED** — still open from 2026-10-08.

### L-1 — pip and setuptools CVEs in build toolchain (DEFERRED)

**Tool:** `pip-audit`
**Affected packages:** `pip 24.0` (PYSEC-2026-1795/1796/196/2875/2876/3721, fix: upgrade to ≥26.2),
`setuptools 79.0.1` (PYSEC-2026-3447, fix: upgrade to ≥83.0.0)
**Risk:** Build/install-time tool CVEs only; not present in the deployed container.
Risk is confined to the CI build environment.
**Status: DEFERRED** — unchanged from 2026-10-08; upgrade pip and setuptools in CI.

### L-2 — SQL f-string interpolation of dynamically-built WHERE clauses (DEFERRED)

**File/location:** `app/main.py` (multiple admin endpoints), `app/telemetry.py:268`,
`app/teams.py:218`
**Risk:** f-strings assemble the `where` fragment from hardcoded column names and
SQL operators only; user values pass through `?` placeholders. No active injection
path found. The pattern is fragile — a future change interpolating a user value
into the `where` string would introduce injection without an obvious code smell.
**Proposed design:** Refactor to a query-builder helper or add a pre-commit lint
rule to reject raw strings in WHERE fragments.
**Status: DEFERRED** — unchanged from 2026-10-08; code-quality hardening item.

---

## Minimum Sweep Results

| Check | Result |
|-------|--------|
| Every `@app.get/post/delete` route calls auth guard OR is documented as open | **PASS** — open routes: `/v1/models` (model listing), `/health`, `/feedback` (user-side rating), `/chat` (login page), `/chat/login`, `/chat/logout`, `/metrics` (flagged M-2 above) |
| `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, unsafe `yaml.load` | **PASS** — none found. `create_subprocess_exec` (not `shell=True`) used in provider CLIs. |
| SQL parameterized — no f-string/concat with non-whitelisted values | **PASS** (with L-2 caveat — no active injection path) |
| No tracked secrets (`.env`, `.key`, `.pem`; `sk-`, `AKIA`, `ghp_` patterns) | **PASS** — no secrets in tracked files; example files contain only placeholder strings |
| Secret comparisons use `secrets.compare_digest` | **PASS after re-applied fix** — H-1 re-fixed this run |
| Token generation uses `secrets` module | **PASS** — `teams.py:117` uses `secrets.token_hex(16)`, `chat.py:62` uses `secrets.token_hex(8)` |
| Dockerfile has non-root `USER` | **PASS after re-applied fix** — M-1 re-fixed this run |
| docker-compose does not publish unauthenticated services beyond loopback | **CONDITIONAL PASS** — `clearview` service publishes `8000:8000` (all interfaces); acceptable for local gateway, but production should bind to `127.0.0.1:8000:8000`. `ollama` under `--profile local` publishes `11434:11434`. |
| pip-audit CVEs | **FAIL (L-1)** — same pip/setuptools build-tool CVEs as 2026-10-08; no application-code CVEs |

---

## Positive Observations

- No new code changes on `main` since the last audit — attack surface unchanged.
- Team tokens are generated with `secrets.token_hex(16)` (128 bits of entropy).
- The team token list endpoint (`GET /admin/teams`) redacts tokens to a short prefix.
- Budget enforcement guards exist at both per-team and global levels.
- The cookie set by `/chat/login` is `httponly=True, samesite="lax"`.
- No hardcoded credentials or API keys found in tracked files.
- Test suite: 359 tests pass (21/21 API tests pass against the modified code).

---

## Remediation Status

| ID  | Severity | Description                              | Status        |
|-----|----------|------------------------------------------|---------------|
| H-1 | High     | Non-constant-time admin token compare    | FIXED (re-applied) |
| M-1 | Medium   | Dockerfile runs as root                  | FIXED (re-applied) |
| M-2 | Medium   | Unauthenticated `/metrics` endpoint      | DEFERRED |
| L-1 | Low      | pip/setuptools CVEs in build toolchain   | DEFERRED |
| L-2 | Low      | SQL f-string WHERE assembly pattern      | DEFERRED |
