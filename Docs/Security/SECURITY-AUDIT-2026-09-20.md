# ClearView Security Audit — 2026-09-20

**Auditor:** Automated daily audit (Claude Code scheduled task)  
**Branch audited:** `main` @ `e8ad2e3`  
**Remediation branch:** `security/audit-2026-09-20`  
**Previous report:** `Docs/Security/SECURITY-AUDIT-2026-09-19.md`  
**pip-audit result:** 14 CVEs in `pip 24.0` (fix: 26.2.1) and `setuptools 79.0.1` (fix: 83.0.0) — see M4 below

---

## ⚠️ Meta-Finding: 19 Open Security Audit PRs (Unreviewed)

PRs #11 through #20 (audits from 2026-09-01 through 2026-09-19) are all open and unreviewed. None have been merged to `main`. As a result:

- Fixes reported as "done" in previous audits (M1 — constant-time compare; L1 — Dockerfile non-root) have **not landed in `main`** and are re-applied in this PR.
- The finding backlog has grown to 6 items (M1–M3, L1–L2, I1) still open on the production branch.
- Each daily audit is re-fixing the same issues on a branch that never merges.

**Recommended action:** Review and merge the oldest pending audit PR (#11 or #20 which consolidates them all), or configure auto-merge for the security audit branch. Until PRs are merged, daily audits will continue to report the same open findings.

---

## Severity Summary

| Severity | New this run | Fixed this run | Deferred |
|----------|-------------|---------------|---------|
| High     | 0           | —             | —       |
| Medium   | 1 (M4 CVEs) | 3 (M1, M4, re-fix L1) | 2 (M2, M3) |
| Low      | 0           | 1 (L1 re-fix) | 1 (L2)  |
| Info     | 0           | 0             | 1 (I1)  |

---

## New Findings

### Medium

#### M4 — pip and setuptools CVEs in container image ✅ FIXED

**Location:** `Dockerfile` (base `python:3.12-slim` ships `pip 24.0`, `setuptools 79.0.1`)  
**Risk:** 14 known vulnerabilities in build-tool packages that remain in the container runtime image:

| Package     | Version | CVE IDs                                              | Fix version |
|-------------|---------|------------------------------------------------------|-------------|
| pip         | 24.0    | PYSEC-2026-1795, 1796, 196, 2875, 2876, 3721 (×2 each) | 26.2.1      |
| setuptools  | 79.0.1  | PYSEC-2026-3447                                      | 83.0.0      |

Because `pip install` is run during the Docker build (and pip remains in the image layer), these vulnerabilities are present in the built container. pip CVEs in this range typically involve path traversal or arbitrary code execution during wheel installation — relevant if ClearView ever installs packages at runtime or if the image is used as a base for further builds.  
**Remediation applied:** Added `RUN pip install --no-cache-dir --upgrade "pip>=26.2.1" "setuptools>=83.0.0"` before the project install step in the Dockerfile.  
**Status:** Fixed in this PR; tests pass.

---

## Re-Applied Fixes (Pending Merge Since 2026-09-19)

These findings were first reported and "fixed" in the 2026-09-19 audit but that PR (#20) was never merged to `main`. They are re-applied here.

### Medium (Re-applied)

#### M1 — Non-constant-time admin token comparison ✅ FIXED (re-applied)

**Location:** `app/main.py:301` (`_admin_auth`)  
**Risk:** `token != expected` short-circuits on the first differing byte, enabling timing side-channel attacks to enumerate `CLEARVIEW_ADMIN_TOKEN`.  
**Remediation re-applied:** `import secrets` added to imports (line 11); comparison changed to `not secrets.compare_digest(token, expected)`.  
**Status:** Fixed in this PR; tests pass.

---

### Low (Re-applied)

#### L1 — Dockerfile runs as root ✅ FIXED (re-applied)

**Location:** `Dockerfile` (no `USER` directive)  
**Risk:** Container process runs as UID 0 (root). A container escape or app-level RCE grants root access inside the container, increasing blast radius.  
**Remediation re-applied:** Added `RUN useradd -r -s /bin/false clearview && mkdir -p /data && chown clearview /data` and `USER clearview` directive at the bottom of the Dockerfile.  
**Status:** Fixed in this PR; tests pass.

---

## Carried-Over Deferred Findings

These were deferred in prior audits and remain open. No change in status.

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

**Location:** `docker-compose.yml:8` (`ports: "8000:8000"`) and `:20` (`"11434:11434"`)  
**Risk:** Listens on `0.0.0.0`, exposing the gateway on every host network interface. On a multi-homed machine or cloud VM with a public IP, this is reachable without operator intent.  
**Proposed fix:** Change to `"127.0.0.1:8000:8000"` and `"127.0.0.1:11434:11434"` with documented override for intentional external binding.  
**Still deferred:** Some users legitimately want LAN-accessible ClearView. Needs docs + user communication.

---

### Info / Informational (Deferred)

#### I1 — `/v1/models` and `/v1/models/{model}:generateContent` open without auth

**Location:** `app/main.py:364`  
**Risk:** Enumerates virtual tier names and real underlying model IDs without authentication. Low risk for most deployments since OpenAI-compatible clients depend on `/v1/models` being open.  
**Status:** No action; noted for awareness.

---

## Audit Checklist Results

| Check | Result |
|-------|--------|
| Auth coverage — all `@app.get/post/delete` routes have auth or documented open reason | ✅ Pass — all compat shims delegate to `_handle_chat_completions` which calls `_client_key_allowed` + `_resolve_team`; `/health`, `/feedback`, `/chat`, `/chat/login`, `/chat/logout`, `/v1/models` all have documented open posture |
| Injection — `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, `yaml.load` | ✅ None found |
| SQL parameterization — no f-string interpolation of user-controlled values | ✅ Pass — dynamic `WHERE` clauses use only hardcoded column names; values always passed as `?` parameters |
| Tracked secrets — `.env`/`key`/`pem` files, live-looking API keys in tracked files | ✅ `.env` gitignored; no `sk-`, `AKIA`, `ghp_` patterns found in tracked files |
| Token handling — comparisons via `secrets.compare_digest`, generation via `secrets` module | ✅ Fixed (admin token in this PR); team tokens already use `secrets.token_hex(16)` |
| Dockerfile non-root `USER` | ✅ Fixed in this PR (re-applied) |
| docker-compose loopback binding | ⚠️ Deferred (L2) |
| pip-audit CVE scan | ⚠️ Fixed in this PR (Dockerfile now upgrades pip + setuptools) |

---

## Deferred Findings Summary

| ID | Title | Proposed design | First reported |
|----|-------|----------------|---------------|
| M2 | `/metrics` unauthenticated | Gate behind `_admin_auth`; scraper migration required | 2026-09-19 |
| M3 | Session cookie missing `Secure` flag | `secure=True` conditioned on `CLEARVIEW_HTTPS` env var | 2026-09-19 |
| L2 | docker-compose binds to all interfaces | Change to `127.0.0.1:…:…` with documented override | 2026-09-19 |
| I1 | `/v1/models` open (informational) | Document intentional posture or gate if models are sensitive | 2026-09-19 |

---

## Positive Observations

- Team tokens minted with `secrets.token_hex(16)` — cryptographically strong PRNG throughout.
- `cv_session` cookie is `HttpOnly` and `SameSite=Lax` — prevents XSS token theft and CSRF (Secure flag still pending, M3).
- `.env` and `*.db` files are gitignored; no live credentials in tracked files.
- All admin endpoints (`/admin/*`, team management) consistently guarded by `_admin_auth`.
- No use of `shell=True`, `eval`, `exec`, `pickle`, or unsafe `yaml.load` anywhere in the codebase.
- SQL queries use parameterized `?` bindings throughout; dynamic SQL only assembles column names from hardcoded strings.
- No code changes to main since the 2026-09-19 audit — stable codebase, no new attack surface.

---

## Remediation Status

| Finding | Status |
|---------|--------|
| M1 — Non-constant-time admin token compare | ✅ Fixed in this PR (`secrets.compare_digest`, re-applied) |
| M2 — `/metrics` unauthenticated | ⚠️ Deferred — needs scraper migration plan |
| M3 — Cookie missing `Secure` flag | ⚠️ Deferred — needs `CLEARVIEW_HTTPS` flag |
| M4 — pip/setuptools CVEs in container | ✅ Fixed in this PR (Dockerfile upgrade) |
| L1 — Dockerfile runs as root | ✅ Fixed in this PR (`USER clearview`, re-applied) |
| L2 — docker-compose all-interfaces binding | ⚠️ Deferred — intentional for dev; needs docs |
| I1 — `/v1/models` open | ℹ️ Noted — likely intentional for compat |
