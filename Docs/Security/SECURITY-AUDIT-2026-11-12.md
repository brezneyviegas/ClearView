# Security Audit — 2026-11-12

**Repository:** ClearView (Python/FastAPI LLM gateway)
**Auditor:** Automated daily audit (Claude Code scheduled routine)
**Branch audited:** main @ e8ad2e3
**Audit branch:** security/audit-2026-11-12
**Previous audit:** None (first audit)

---

## Severity Summary

| Severity | New | Fixed this run | Deferred |
|----------|-----|---------------|----------|
| High     | 0   | 0             | 0        |
| Medium   | 3   | 2             | 1        |
| Low      | 1   | 0             | 1        |

---

## High Findings

None.

---

## Medium Findings

### M-1 — Non-constant-time admin token comparison [FIXED]

**File/Location:** `app/main.py:301` (`_admin_auth`)
**Risk:** Python's `!=` on strings short-circuits on the first differing byte. An attacker who can make many requests and measure response latency precisely could recover the `CLEARVIEW_ADMIN_TOKEN` value one character at a time (timing side-channel). In a local-network or same-host scenario the latency signal is measurable.
**Remediation:** Replaced `token != expected` with `not secrets.compare_digest(token, expected)` and added `import secrets` to the module imports. `secrets.compare_digest` runs in constant time regardless of where strings differ.
**Status:** ✅ Fixed in this run.

---

### M-2 — Dockerfile runs container as root [FIXED]

**File/Location:** `Dockerfile` (no `USER` directive)
**Risk:** The application process runs as `root` inside the container. If the app is compromised (e.g. via a provider-side RCE relayed through the LLM response), the attacker obtains root privileges within the container, making container escape or volume tampering easier.
**Remediation:** Added `RUN groupadd -r clearview && useradd -r -g clearview -d /app clearview` and `USER clearview` before `CMD`. The `/data` volume is `chown`-ed to the new user so the SQLite database remains writable.
**Status:** ✅ Fixed in this run.

---

### M-3 — `/metrics` Prometheus endpoint has no authentication [DEFERRED]

**File/Location:** `app/main.py:2538` (`metrics`)
**Risk:** The endpoint is unauthenticated. It exposes: total request counts broken down by `tier`, `provider`, `status`, and `team_id` label; total cost in USD; total output tokens. In a multi-tenant deployment, adversaries on the same network can enumerate team IDs from metric labels and infer usage patterns.
**Remediation proposal:** Add `_admin_auth(request)` to the metrics handler, or gate it behind a separate `CLEARVIEW_METRICS_TOKEN` env var. Prometheus scrapers can be configured with a `Bearer` header via `authorization` in the scrape config. Alternatively, serve metrics only on a separate internal port (requires uvicorn multi-port or a sidecar).
**Status:** ⏳ Deferred — adding auth to the metrics endpoint requires Prometheus scraper reconfiguration; marking as a behavior-changing fix outside this run's scope. Proposed design: add `CLEARVIEW_METRICS_TOKEN` env var checked in the handler before the `telemetry.metrics_snapshot()` call, defaulting to open when unset (preserving current behavior for local/dev installs).

---

## Low Findings

### L-1 — pip and setuptools CVEs in build environment [DEFERRED]

**Tool:** pip-audit
**Packages:**
- `pip 24.0` — PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-196, PYSEC-2026-3721 (fix: pip ≥ 26.3)
- `setuptools 79.0.1` — PYSEC-2026-3447, GHSA-xmj3-9gf7-h52w (fix: setuptools ≥ 84.1.0)

**Risk:** These CVEs are in the build-time toolchain, not in production runtime packages. Exploiting them requires an attacker to influence what `pip install` fetches at build time (e.g. a malicious package in the dependency tree or a network-level MITM). No app runtime dependency is affected.
**Remediation:** Pin pip and setuptools to fixed versions in the Dockerfile: `RUN pip install --upgrade pip setuptools` or pin exact versions. For CI, run `pip-audit` on the production requirements only (excluding dev tooling) to get a cleaner signal.
**Status:** ⏳ Deferred — build-time tool upgrade; no immediate runtime risk.

---

## Deferred Items Carried Over

None (first audit).

---

## Minimum Sweep Results

| Check | Result |
|-------|--------|
| All `@app.get/post/delete` admin routes call `_admin_auth` | ✅ Pass — all 20 `/admin/*` routes verified |
| All `/chat/*` routes call `_require_chat_team` or are intentionally open | ✅ Pass — 10 chat routes verified; `/chat/login`, `/chat/logout`, `/chat` (page) are intentionally open |
| `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, unsafe `yaml.load` | ✅ None found (subprocess.exec calls use `asyncio.create_subprocess_exec` without shell=True) |
| SQL parameterization — no f-string/concat with user values | ✅ Pass — f-string SQL in `teams.py:218` and `telemetry.py` uses hardcoded column names; all user values passed via `?` placeholders |
| Tracked secrets (`.env`, key files, live-looking tokens) | ✅ None found |
| Secret comparisons use `secrets.compare_digest` | ✅ Fixed this run (M-1) |
| Token generation uses `secrets` module | ✅ Pass — `secrets.token_hex(16)` in `teams.py:117`, `secrets.token_hex(8)` in `chat.py:62` |
| Dockerfile non-root `USER` | ✅ Fixed this run (M-2) |
| docker-compose: no unauthenticated services beyond loopback | ⚠️ Port 8000 bound to `0.0.0.0`; acceptable for a gateway but operator should ensure firewall rules when deploying on a shared network |
| pip-audit CVEs | ⚠️ 2 packages with CVEs — both build-time only; deferred (L-1) |

---

## Positive Observations

- All 20 `/admin/*` routes consistently gate on `_admin_auth`, which now uses constant-time comparison.
- Team token generation uses `secrets.token_hex(16)` (128-bit entropy), appropriate for bearer tokens.
- No `shell=True` or command-injection-prone subprocess calls anywhere in the codebase.
- SQL is fully parameterized; the f-string pattern used to build dynamic `SET` clauses uses only hardcoded column names.
- The `.gitignore` excludes `.env` and common secret file extensions; no live API keys found in tracked files.
- `yaml.safe_load` is used throughout; no unsafe `yaml.load` calls.
- The `/feedback` endpoint's decision to be open is documented inline — reduces surface for undocumented open endpoints.

---

## Remediation Status

| Finding | Severity | Status |
|---------|----------|--------|
| Non-constant-time admin token comparison | Medium | ✅ Fixed |
| Dockerfile runs as root | Medium | ✅ Fixed |
| `/metrics` no authentication | Medium | ⏳ Deferred |
| pip/setuptools CVEs (build-time) | Low | ⏳ Deferred |
