# ClearView Security Audit — 2026-09-29

**Auditor:** Automated daily audit (Claude Sonnet 4.6)  
**Branch audited:** `main` (HEAD: e8ad2e3)  
**Audit branch:** `security/audit-2026-09-29`  
**Prior audit:** None (first audit)

---

## Severity Summary

| Severity | New | Fixed this run | Deferred |
|----------|-----|----------------|---------|
| High     | 0   | 0              | 0       |
| Medium   | 2   | 2              | 0       |
| Low      | 3   | 1              | 2       |

---

## High Findings

None.

---

## Medium Findings

### M-1 — Non-constant-time admin token comparison ✅ FIXED

**File/Location:** `app/main.py:301` (`_admin_auth`)  
**Risk:** `_admin_auth` compared the bearer token with `token != expected`, a plain Python string comparison. Python's `!=` short-circuits on the first mismatching byte, creating a timing oracle. An attacker making repeated requests from a co-located machine could brute-force the `CLEARVIEW_ADMIN_TOKEN` character by character, bypassing all admin endpoints (`/admin/stats`, `/admin/teams`, `/admin/tune`, etc.).  
**Remediation:** Replaced with `secrets.compare_digest(token, expected)`, which runs in constant time regardless of where the strings diverge. Applied in this run.

---

### M-2 — Dockerfile runs container as root ✅ FIXED

**File/Location:** `Dockerfile`  
**Risk:** No `USER` directive; the application ran as UID 0 inside the container. If an attacker achieved remote code execution through the LLM gateway, they would have root inside the container, significantly simplifying container-escape or lateral movement to the host or sibling containers.  
**Remediation:** Added `adduser clearview` (no shell, no home) and `USER clearview` before `CMD`, plus explicit `chown clearview:clearview /data`. Applied in this run.  
Also added `pip install --upgrade pip setuptools` to the build step to resolve CVEs M-3 below during image builds.

---

## Low Findings

### L-1 — pip / setuptools build-tool CVEs ✅ FIXED (at build time)

**File/Location:** `Dockerfile`, Python build environment  
**CVEs:**
- `pip 24.0`: PYSEC-2026-1795, -1796, -2875, -2876, -196, -3721 (fix: pip ≥ 26.2)
- `setuptools 79.0.1`: PYSEC-2026-3447 (fix: setuptools ≥ 83.0.0)

**Risk:** These are build-time tools, not runtime dependencies. The CVEs relate to pip's dependency-resolution and network-fetch paths, exploitable during `pip install` if a malicious package is substituted. Risk is elevated when building in untrusted package registries or with unverified PyPI indexes.  
**Remediation:** Added `pip install --upgrade pip setuptools` to the `RUN` layer in `Dockerfile` so every image build upgrades to the latest versions before installing app deps. Applied in this run.

---

### L-2 — `/metrics` endpoint unauthenticated ⚠️ DEFERRED

**File/Location:** `app/main.py:2538` (`GET /metrics`)  
**Risk:** The Prometheus metrics endpoint is publicly accessible without authentication. It exposes aggregated call counts by tier, provider, status, and team ID, plus total spend, token counts, and savings percentages. While no raw prompts are exposed, the data reveals routing behaviour, provider mix, and cost to any unauthenticated party who can reach port 8000.  
**Proposed design:** Add an optional `CLEARVIEW_METRICS_TOKEN` env var; when set, require `Authorization: Bearer <token>` on `/metrics` (using `secrets.compare_digest`). When unset, remain open for zero-config Prometheus scraper compatibility. This avoids breaking existing scrapers while giving operators a hardening option.  
**Not fixed this run:** behaviour-changing (would break any existing Prometheus scrape job).

---

### L-3 — `/chat/login` cookie missing `Secure` flag ⚠️ DEFERRED

**File/Location:** `app/main.py:2651-2655` (`chat_login`)  
**Risk:** The 30-day `cv_session` cookie is set with `httponly=True, samesite="lax"` but no `secure=True`. If a client connects over plain HTTP (local dev, or a misconfigured reverse proxy), the browser will transmit the team bearer token in cleartext, enabling session hijacking on the local network.  
**Proposed design:** Detect HTTPS via `request.headers.get("x-forwarded-proto") == "https"` or an env var `CLEARVIEW_SECURE_COOKIE=1`, and conditionally pass `secure=True`. Default to `False` for local dev, `True` in any production-facing deployment.  
**Not fixed this run:** behaviour-changing for existing HTTPS deployments where `secure` is not yet set and cookies must be re-issued; also requires a decision on the detection heuristic.

---

## Carried-Over Deferred Items

None (first audit).

---

## Positive Observations

- Token generation uses `secrets.token_hex(16)` (cryptographically secure).
- All subprocess calls use `asyncio.create_subprocess_exec` — no `shell=True`, no shell-injection surface.
- No tracked `.env`, `.key`, or `.pem` files in the repository.
- No hardcoded live API keys (no `sk-`, `AKIA`, or `ghp_` patterns in tracked files).
- No `eval()`, `exec()`, `pickle`, or unsafe `yaml.load` calls in application code.
- SQL queries use parameterized `?` placeholders throughout; f-string interpolation is limited to hardcoded column-name fragments, never user-supplied values.
- Every `/admin/*` route calls `_admin_auth`.
- Every authenticated `/chat/*` route calls `_require_chat_team`.
- Team token lookup enforces `enabled` check before granting access.
- Budget enforcement applies both team-level (daily + monthly) and global daily caps before dispatching upstream calls.

---

## Minimum-Sweep Checklist

| Check | Result |
|-------|--------|
| Every `/admin/*` route calls `_admin_auth`, `_client_key_allowed`, `_resolve_team`, or `_require_chat_team`, or has a documented open reason | ✅ All admin routes call `_admin_auth` |
| `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, unsafe `yaml.load` | ✅ None found (subprocess calls use `exec` variant) |
| SQL parameterization — no f-string/concat with non-whitelisted user values | ✅ Only static column-name fragments in f-strings; all values via `?` |
| No tracked secrets (`.env`/`.key`/`.pem`, live key patterns) | ✅ None found |
| Secret comparisons use `secrets.compare_digest` | ✅ After M-1 fix |
| Token generation uses `secrets` module | ✅ `secrets.token_hex(16)` |
| Dockerfile has non-root `USER` | ✅ After M-2 fix |
| docker-compose does not publish unauthenticated services beyond loopback | ⚠️ Port 8000 published to all interfaces (see L-3 proxy notes) |
| pip-audit: no CVEs in runtime deps | ✅ CVEs only in `pip`/`setuptools` build tools; addressed in Dockerfile |

---

## Test Results

183 tests passed across `test_setup.py`, `test_telemetry.py`, `test_auto_shadow.py`, `test_teams.py`, `test_router.py`, `test_pricing.py`, `test_scoring.py`, `test_gemini_cli.py`, `test_embeddings.py` after applying fixes. Integration tests in `test_api.py`, `test_chat.py::TestChatSend`, and `test_chat.py::TestChatConversations` (part) hang waiting for real LLM endpoints — pre-existing behaviour unrelated to this audit's changes.
