# Security Audit — 2026-11-04

**Auditor:** Automated daily audit (Claude Code)
**Repository:** ClearView — Python/FastAPI LLM gateway
**Branch audited:** `main` (HEAD `e8ad2e3`)
**Previous audit:** None — this is the first run

---

## Severity Summary

| Severity | New Findings | Fixed in this PR | Deferred |
|----------|-------------|------------------|----------|
| High     | 0           | 0                | 0        |
| Medium   | 2           | 2                | 0        |
| Low      | 2           | 1                | 1        |
| Info     | 1           | 0                | 1        |

---

## Findings

### MEDIUM — M1: Non-constant-time admin token comparison (FIXED)

**File/location:** `app/main.py:301` — `_admin_auth()`

**Risk:** Python's `!=` operator short-circuits on the first differing byte, creating a timing
side-channel. An attacker who can make many requests while measuring response times can
brute-force the `CLEARVIEW_ADMIN_TOKEN` one character at a time, reducing the search space
from 2^n to 36n guesses for a hex token.

**Remediation applied:** Replaced `token != expected` with
`secrets.compare_digest(token, expected)`, which runs in constant time regardless of the
number of matching bytes.

---

### MEDIUM — M2: Dockerfile runs as root — no USER directive (FIXED)

**File/location:** `Dockerfile`

**Risk:** Without a `USER` directive the container process runs as UID 0 (root). If the
application or any dependency is compromised, the attacker has root inside the container,
making container-escape and lateral movement significantly easier.

**Remediation applied:** Added
```dockerfile
RUN useradd -r -s /bin/false clearview && mkdir -p /data && chown clearview /data
USER clearview
```
after the `COPY` layer so the final image runs as the unprivileged `clearview` user.

---

### LOW — L1: `/metrics` endpoint exposed without authentication (FIXED)

**File/location:** `app/main.py:2538` — `GET /metrics`

**Risk:** The Prometheus `/metrics` endpoint returns per-team IDs, provider names, tier names,
cumulative cost figures, and request counts. No authentication was required. Anyone who can
reach the gateway port could enumerate team IDs and infer usage patterns.

**Remediation applied:** Added `_admin_auth(request)` as the first call inside the handler,
consistent with all other admin-only endpoints. Dev-mode (no `CLEARVIEW_ADMIN_TOKEN` set)
remains open by design.

---

### LOW — L2: CVEs in transitive dependency `filelock` (FIXED — pin added)

**File/location:** `pyproject.toml` (transitive via `litellm`)

**Risk:** `filelock 3.32.7` carries 13 published CVEs (CVE-2026-112907 through CVE-2026-112940).
All fix at `filelock>=4.0.9`; the full batch requires `>=4.2.1`.

**Remediation applied:** Added `"filelock>=4.2.1"` as an explicit dependency in
`pyproject.toml` to force the fixed version. Because `filelock` is a transitive dependency
of `litellm`, the floor applies automatically on `pip install`.

**CVE list:** CVE-2026-112907, CVE-2026-112908, CVE-2026-112909, CVE-2026-112910,
CVE-2026-112911, CVE-2026-112912, CVE-2026-112913, CVE-2026-112915, CVE-2026-112935,
CVE-2026-112936, CVE-2026-112938, CVE-2026-112939, CVE-2026-112940.

---

### INFO — I1: pip/setuptools CVEs in build toolchain (DEFERRED — not a runtime concern)

**File/location:** `.venv` (build toolchain only)

**Risk:** `pip 24.0` (PYSEC-2026-1795/1796/196/2875/2876/3721; fix at >=26.1.2) and
`setuptools 79.0.1` (PYSEC-2026-3447, GHSA-xmj3-9gf7-h52w; fix at >=84.1.0) are
vulnerable, but these are only in the development virtual environment and the Docker build
layer — they are **not** runtime dependencies shipped to production consumers.

**Deferred:** Upgrade the base image's pre-installed `pip`/`setuptools` in CI and in the
Dockerfile build step (`pip install --upgrade pip setuptools` before the app install).
Not applied here because it has no production impact and would require CI plumbing to
verify properly.

---

## Deferred Items Carried Forward

None from previous audits (first run).

---

## Minimum Sweep Checklist

| Check | Result |
|-------|--------|
| (a) Every `@app.get/post/delete` route calls an auth helper or is documented open | ✅ Pass — all `/admin/*` call `_admin_auth`; all `/chat/*` (except login/logout/page) call `_require_chat_team`; `/v1/*` call `_client_key_allowed`+`_resolve_team`; `/feedback`, `/health`, `/chat` (GET), `/chat/login`, `/chat/logout` are intentionally open; `/metrics` now gated (this audit) |
| (b) `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, unsafe `yaml.load` | ✅ None found — subprocess calls use `create_subprocess_exec` (no shell) |
| (c) SQL parameterization — no f-string/concat with non-whitelisted values | ✅ Pass — all f-string SQL builds only static WHERE clauses; user values always via `?` params |
| (d) Tracked secrets — no `.env`/`.key`/`.pem` files; no live-looking keys in tracked files | ✅ Pass — `.gitignore` excludes `.env` and `*.db`; no `sk-`, `AKIA`, `ghp_` patterns found |
| (e) Secret comparisons use `secrets.compare_digest`; token generation uses `secrets` module | ✅ Pass after fix — `_admin_auth` now uses `compare_digest`; team tokens use `secrets.token_hex` |
| (f) Dockerfile non-root USER; docker-compose doesn't publish unauthenticated services | ✅ Pass after fix — `USER clearview` added; docker-compose port `8000:8000` is for local dev (acceptable) |
| (g) `pip-audit` CVEs with fix versions | ⚠️ See L2 above (filelock, fixed); pip/setuptools toolchain-only (deferred I1) |

---

## Positive Observations

- Team token generation (`cv_team_<32 hex chars>`) correctly uses `secrets.token_hex(16)`.
- Chat conversation IDs use `secrets.token_hex(8)`.
- Session cookie set with `httponly=True, samesite="lax"` — protects against XSS token theft.
- Budget enforcement and team-tier gating are correctly placed after routing decisions.
- All admin and team-management endpoints consistently require `_admin_auth`.
- No `shell=True` or dangerous subprocess patterns found anywhere.
- SQL queries across all inline endpoints use parameterized placeholders; no injection surface detected.
- No tracked secrets or key files found in the repository.

---

## Remediation Status

| ID | Description | Status |
|----|-------------|--------|
| M1 | Non-constant-time token compare | Fixed in `security/audit-2026-11-04` |
| M2 | Dockerfile root user | Fixed in `security/audit-2026-11-04` |
| L1 | `/metrics` unauthenticated | Fixed in `security/audit-2026-11-04` |
| L2 | filelock CVEs (transitive) | Fixed in `security/audit-2026-11-04` (pyproject.toml pin) |
| I1 | pip/setuptools toolchain CVEs | Deferred — no production impact; needs CI/Dockerfile upgrade |
