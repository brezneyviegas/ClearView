# ClearView Security Audit — 2026-09-21

**Auditor:** Automated daily audit (Claude Code scheduled task)  
**Branch audited:** `main` @ `e8ad2e3`  
**Remediation branch:** `security/audit-2026-09-21`  
**Previous report:** `Docs/Security/SECURITY-AUDIT-2026-09-20.md` (on `security/audit-2026-09-20`)

---

## ⚠️ Meta-Finding: 20 Open Security Audit PRs (Unreviewed)

PRs from audits 2026-09-01 through 2026-09-20 remain open and unreviewed. None have been merged to `main`. As a result:

- Fixes reported as "done" in every prior audit (M1 — constant-time compare; L1 — Dockerfile non-root; M4 — pip/setuptools CVEs) have **not landed in `main`** and are re-applied in this PR.
- The finding backlog has been stable for 20 days (M1–M3, L1–L2, I1), all on `main` unfixed.
- Each daily audit is re-fixing the same issues on a branch that never merges.

**Recommended action:** Review and merge the oldest pending audit PR, or configure auto-merge for the security audit branch. Until PRs are merged, daily audits will continue to report the same open findings.

---

## Severity Summary

| Severity | New this run | Fixed this run | Deferred |
|----------|-------------|---------------|---------|
| High     | 0           | —             | —       |
| Medium   | 0           | 3 (M1, M4 re-fix) | 2 (M2, M3) |
| Low      | 0           | 1 (L1 re-fix) | 1 (L2)  |
| Info     | 0           | 0             | 1 (I1)  |

No new findings this run. The codebase has not changed since the 2026-09-20 audit (`git log --since=2026-09-20` returns nothing). All fixes are re-applications of prior-cycle work that remains unmerged.

---

## New Findings

None.

---

## Re-Applied Fixes (Pending Merge Since 2026-09-01)

These findings were first reported and fixed on 2026-09-01 (or later) but those PRs were never merged to `main`. They are re-applied here identically.

### Medium (Re-applied)

#### M1 — Non-constant-time admin token comparison ✅ FIXED (re-applied)

**Location:** `app/main.py:301` (`_admin_auth`)  
**Risk:** `token != expected` short-circuits on the first differing byte, enabling timing side-channel attacks to enumerate `CLEARVIEW_ADMIN_TOKEN`.  
**Remediation re-applied:** `import secrets` added to imports; comparison changed to `not secrets.compare_digest(token, expected)`.  
**Status:** Fixed in this PR; tests pass.

---

#### M4 — pip and setuptools CVEs in container image ✅ FIXED (re-applied)

**Location:** `Dockerfile` (base `python:3.12-slim` ships `pip 24.0`, `setuptools 79.0.1`)  
**Risk:** 14 known vulnerabilities in build-tool packages that remain in the container runtime image.

| Package     | Version | CVE IDs                                                          | Fix version |
|-------------|---------|------------------------------------------------------------------|-------------|
| pip         | 24.0    | PYSEC-2026-1795, 1796, 196, 2875, 2876, 3721 (×2 each)          | 26.2.1      |
| setuptools  | 79.0.1  | PYSEC-2026-3447                                                  | 83.0.0      |

**Remediation re-applied:** Added `RUN pip install --no-cache-dir --upgrade "pip>=26.2.1" "setuptools>=83.0.0"` before the project install step in the Dockerfile.  
**Status:** Fixed in this PR; tests pass.

---

### Low (Re-applied)

#### L1 — Dockerfile runs as root ✅ FIXED (re-applied)

**Location:** `Dockerfile` (no `USER` directive)  
**Risk:** Container process runs as UID 0 (root). A container escape or app-level RCE grants root access inside the container, increasing blast radius.  
**Remediation re-applied:** Added `RUN useradd -r -s /bin/false clearview && mkdir -p /data && chown clearview /data` and `USER clearview` at the bottom of the Dockerfile.  
**Status:** Fixed in this PR; tests pass.

---

## Carried-Over Deferred Findings

No change in status from 2026-09-20.

### Medium (Deferred)

#### M2 — `/metrics` endpoint is unauthenticated ⚠️ DEFERRED

**Location:** `app/main.py:2538` (`metrics`)  
**Risk:** Prometheus `/metrics` exposes per-team IDs, USD spend, token counts, and routing breakdowns without authentication. Discloses business-sensitive multi-tenant data.  
**Proposed fix:** Gate behind `_admin_auth(request)`. Prometheus scrapers must send `Authorization: Bearer <CLEARVIEW_ADMIN_TOKEN>` (supported via `bearer_token` in `prometheus.yml`). Alternatively, add `CLEARVIEW_METRICS_TOKEN` for independent scoping.  
**Still deferred:** Requires scraper reconfiguration and a migration window. Needs explicit sign-off.

---

#### M3 — Session cookie missing `Secure` flag ⚠️ DEFERRED

**Location:** `app/main.py:2651` (`chat_login`, `set_cookie`)  
**Risk:** `cv_session` cookie set without `secure=True`. Browser sends cookie over plain HTTP, exposing the team Bearer token if any HTTP request is made before HTTPS upgrade.  
**Proposed fix:** Add `secure=True` conditioned on `CLEARVIEW_HTTPS=1` env var, so local HTTP-only dev isn't broken.  
**Still deferred:** Needs config switch to avoid breaking local HTTP development.

---

### Low (Deferred)

#### L2 — docker-compose binds to all interfaces ⚠️ DEFERRED

**Location:** `docker-compose.yml:8` (`ports: "8000:8000"`)  
**Risk:** Listens on `0.0.0.0`, exposing the gateway on every host network interface.  
**Proposed fix:** Change to `"127.0.0.1:8000:8000"` with documented override for intentional external binding.  
**Still deferred:** Some users legitimately want LAN-accessible ClearView. Needs docs + user communication.

---

### Info / Informational (Deferred)

#### I1 — `/v1/models` and Gemini compat endpoints open without auth

**Location:** `app/main.py:364`  
**Risk:** Enumerates virtual tier names and real underlying model IDs without authentication. Low risk for most deployments since OpenAI-compatible clients depend on `/v1/models` being open.  
**Status:** No action; noted for awareness.

---

## Audit Checklist Results

| Check | Result |
|-------|--------|
| Auth coverage — all `@app.get/post/delete` routes have auth or documented open reason | ✅ Pass — all compat shims (`/v1/messages`, `/v1/responses`, Gemini endpoints) delegate to `_handle_chat_completions` which calls `_client_key_allowed` + `_resolve_team`; `/health`, `/feedback`, `/chat`, `/chat/login`, `/chat/logout`, `/v1/models` all have documented open posture; all `/admin/*` routes call `_admin_auth` |
| Injection — `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, `yaml.load` | ✅ None found — `create_subprocess_exec` (not `shell=True`) used safely in CLI providers |
| SQL parameterization — no f-string interpolation of user-controlled values | ✅ Pass — dynamic `WHERE` clauses in `app/teams.py` and `app/telemetry.py` use only hardcoded column names with all values passed as `?` parameters |
| Tracked secrets — `.env`/`key`/`pem` files, live-looking API keys in tracked files | ✅ `.env` gitignored; `sk-x` / `sk-test` patterns in test files are `monkeypatch.setenv` test fixtures, not live credentials |
| Token handling — comparisons via `secrets.compare_digest`, generation via `secrets` module | ✅ Fixed in this PR (M1); team tokens already use `secrets.token_hex(16)` |
| Dockerfile non-root `USER` | ✅ Fixed in this PR (L1 re-applied) |
| docker-compose loopback binding | ⚠️ Deferred (L2) |
| pip-audit CVE scan | ⚠️ Fixed in this PR — Dockerfile now upgrades pip + setuptools before install |

---

## Deferred Findings Summary

| ID | Title | Proposed design | First reported |
|----|-------|----------------|---------------|
| M2 | `/metrics` unauthenticated | Gate behind `_admin_auth`; scraper migration required | 2026-09-01 |
| M3 | Session cookie missing `Secure` flag | `secure=True` conditioned on `CLEARVIEW_HTTPS` env var | 2026-09-01 |
| L2 | docker-compose binds to all interfaces | Change to `127.0.0.1:…:…` with documented override | 2026-09-01 |
| I1 | `/v1/models` open (informational) | Document intentional posture or gate if models are sensitive | 2026-09-01 |

---

## Positive Observations

- Team tokens minted with `secrets.token_hex(16)` — cryptographically strong PRNG throughout.
- `cv_session` cookie is `HttpOnly` and `SameSite=Lax` — prevents XSS token theft and CSRF.
- `.env` and `*.db` files are gitignored; no live credentials in tracked files.
- All admin endpoints (`/admin/*`, team management) consistently guarded by `_admin_auth`.
- No use of `shell=True`, `eval`, `exec`, `pickle`, or unsafe `yaml.load` anywhere in the codebase.
- SQL queries use parameterized `?` bindings throughout; dynamic SQL only assembles column names from hardcoded strings.
- CLI provider subprocess calls use `asyncio.create_subprocess_exec` (not `shell=True`) with prompt delivered via stdin — no shell injection surface.
- Codebase stable: no commits since 2026-09-20 audit.

---

## Remediation Status

| Finding | Status |
|---------|--------|
| M1 — Non-constant-time admin token compare | ✅ Fixed in this PR (`secrets.compare_digest`, re-applied) |
| M2 — `/metrics` unauthenticated | ⚠️ Deferred — needs scraper migration plan |
| M3 — Cookie missing `Secure` flag | ⚠️ Deferred — needs `CLEARVIEW_HTTPS` flag |
| M4 — pip/setuptools CVEs in container | ✅ Fixed in this PR (Dockerfile upgrade, re-applied) |
| L1 — Dockerfile runs as root | ✅ Fixed in this PR (`USER clearview`, re-applied) |
| L2 — docker-compose all-interfaces binding | ⚠️ Deferred — intentional for dev; needs docs |
| I1 — `/v1/models` open | ℹ️ Noted — likely intentional for compat |
