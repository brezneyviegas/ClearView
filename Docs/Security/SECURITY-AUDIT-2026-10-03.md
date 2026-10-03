# ClearView Security Audit — 2026-10-03

**Auditor:** Automated daily audit  
**Audit branch baseline:** `main` (HEAD: `e8ad2e3`)  
**First audit** — no previous report to carry over deferred items from.

---

## Severity Summary

| Severity | Count | Fixed | Deferred |
|----------|-------|-------|----------|
| High     | 1     | 1     | 0        |
| Medium   | 4     | 2     | 2        |
| Low      | 1     | 0     | 1        |
| **Total**| **6** | **3** | **3**    |

---

## High Findings

### H1 — Dockerfile runs as root (no USER directive) ✅ FIXED

**Location:** `Dockerfile`  
**Risk:** If any vulnerability in the application or its dependencies allows code execution, the attacker gains root inside the container. Root inside a container with a mounted volume (`/data`) can overwrite database files and potentially escalate depending on host configuration.  
**Remediation applied:** Added `useradd -r -u 1001 appuser` and `USER appuser` directive before `CMD`. The `/data` volume directory ownership is set to `appuser` so the database remains writable.

---

## Medium Findings

### M1 — `_admin_auth` uses non-constant-time token comparison ✅ FIXED

**Location:** `app/main.py:302` (`_admin_auth`)  
**Risk:** `token != expected` is a standard Python string comparison that short-circuits on the first differing byte. A remote attacker able to send many requests and measure response latency can statistically determine the correct `CLEARVIEW_ADMIN_TOKEN` value one byte at a time (timing oracle). Exploitability is low-moderate in practice (network jitter dominates), but the correct fix is cheap.  
**Remediation applied:** Changed comparison to `secrets.compare_digest(token, expected)` and added `import secrets` to the module imports. Constant-time regardless of shared prefix length.

### M2 — `/metrics` endpoint unauthenticated ⏳ DEFERRED

**Location:** `app/main.py:2538` (`metrics`)  
**Risk:** The Prometheus `/metrics` endpoint exposes per-tier, per-provider, per-status, and per-`team_id` call counters without any authentication. An attacker on the same network can enumerate team IDs, infer usage patterns, and learn which providers are active — useful reconnaissance for targeted attacks.  
**Proposed fix:** Add `request: Request` parameter and call `_admin_auth(request)` at the top of `metrics()`. Guard behind `CLEARVIEW_METRICS_TOKEN` env var (separate from `CLEARVIEW_ADMIN_TOKEN`) so Prometheus scraping can use a dedicated read-only credential without sharing the full admin token. Deferred because adding auth to a previously open endpoint breaks any existing Prometheus scrape configs — operator opt-in required.

### M3 — pip 24.0 CVEs in Docker build toolchain ✅ FIXED

**Location:** `Dockerfile` (base image pip)  
**CVEs:** PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-196, PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-3721  
**Fix version:** pip ≥ 26.2.0  
**Risk:** Vulnerabilities in pip during `docker build` could be exploited if a malicious package is resolved from PyPI (e.g., supply-chain attack, typosquatting). Not runtime risk.  
**Remediation applied:** Added `pip install --upgrade pip==26.2.1` as the first step in the `RUN` layer before installing project dependencies.

### M4 — setuptools 79.0.1 CVE in Docker build toolchain ✅ FIXED

**Location:** `Dockerfile` (base image setuptools)  
**CVE:** PYSEC-2026-3447  
**Fix version:** setuptools ≥ 83.0.0  
**Risk:** Same class as M3 — build-time toolchain vulnerability.  
**Remediation applied:** Added `setuptools==83.0.0` to the same upgrade step as M3.

---

## Low Findings

### L1 — docker-compose binds port 8000 to all interfaces ⏳ DEFERRED

**Location:** `docker-compose.yml:9` (`ports: - "8000:8000"`)  
**Risk:** With `CLEARVIEW_ADMIN_TOKEN` unset (dev-mode default), all `/admin/*` endpoints are open to anyone who can reach port 8000. On a shared network or cloud VM with public IP, this exposes telemetry, team tokens (via `/admin/teams` create), and the policy tuner.  
**Proposed fix:** Change to `"127.0.0.1:8000:8000"` in docker-compose for local dev. Production deployments should set `CLEARVIEW_ADMIN_TOKEN` and put ClearView behind a reverse proxy. Deferred because changing the compose default may break setups that rely on network access from other containers or the Docker host.

---

## Audit Checklist — Detailed Results

| Check | Result |
|-------|--------|
| All `@app.get/post/delete` routes call an auth guard or have documented reason to be open | ✅ Pass (see note below) |
| `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, `yaml.load(` (non-safe) | ✅ Clean — subprocess uses `create_subprocess_exec` (no shell) |
| SQL parameterized (no f-string interpolation of non-whitelisted values) | ✅ Clean — dynamic WHERE clauses use hardcoded column names + `?` placeholders only |
| Tracked secrets: `.env`, `.key`, `.pem` files, `sk-`/`AKIA`/`ghp_` patterns | ✅ Clean — only `.env.example` tracked, no live keys |
| Token comparison uses `secrets.compare_digest` | ⚠️ Fixed (M1) |
| Token generation uses `secrets` module | ✅ Clean — `secrets.token_hex(16)` and `secrets.token_hex(8)` |
| Dockerfile: non-root USER | ⚠️ Fixed (H1) |
| docker-compose: no unauthenticated services beyond loopback | ⚠️ Deferred (L1) |
| pip-audit: no runtime dependency CVEs | ✅ Pass — all CVEs in build toolchain (pip/setuptools), not runtime packages |

**Auth coverage note:** All `/admin/*` routes call `_admin_auth`. All `/chat/*` data routes call `_require_chat_team`. Routes intentionally open: `/v1/models` (model discovery for IDE clients), `/v1/chat/completions` (gated by `_client_key_allowed`+`_resolve_team`), `/feedback` (documented: callers rate their own responses by `request_id`), `/health`, `/chat` (page renders login form). `/metrics` is the one unauthenticated endpoint that leaks operational data (M2).

---

## Positive Observations

- **Cookie security:** `/chat/login` sets `HttpOnly=True`, `SameSite=lax` — no JS-readable session cookie.
- **Team token redaction:** `/admin/teams` list view redacts the full bearer token to an 18-char prefix. Full token returned only at creation time.
- **Input clamping:** All `window` / `limit` query parameters on admin endpoints are clamped with `max(1, min(...))` before use in DB queries.
- **No shell injection:** All external processes (`claude_cli`, `codex_cli`, `gemini_cli`) use `asyncio.create_subprocess_exec` with argument arrays — no shell interpolation.
- **Parameterized SQL throughout:** Dynamic filter building in all telemetry queries uses placeholder-list pattern, never f-string-interpolated user values.
- **Budget soft-cap documentation:** The known race condition on budget enforcement is explicitly documented in-code (`KNOWN LIMITATION`) with a proposed design.

---

## Remediation Status

| ID | Finding | Branch action |
|----|---------|--------------|
| H1 | Dockerfile non-root USER | Fixed in `Dockerfile` |
| M1 | `_admin_auth` timing | Fixed in `app/main.py` |
| M2 | `/metrics` no auth | Deferred — behavior change |
| M3 | pip CVEs in build | Fixed in `Dockerfile` |
| M4 | setuptools CVE in build | Fixed in `Dockerfile` |
| L1 | docker-compose all-interface bind | Deferred — compose default |
