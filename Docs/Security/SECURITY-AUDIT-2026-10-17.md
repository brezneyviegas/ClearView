# ClearView Security Audit — 2026-10-17

**Auditor:** Automated daily routine  
**Branch audited:** `main` (HEAD `e8ad2e3`)  
**Audit branch:** `security/audit-2026-10-17`  
**Baseline:** First audit — no prior report to carry findings from.

---

## Severity Summary

| Severity | New | Fixed this run | Deferred | Carried over |
|----------|-----|----------------|----------|--------------|
| High     | 0   | 0              | 0        | 0            |
| Medium   | 1   | 1              | 0        | 0            |
| Low      | 3   | 0              | 3        | 0            |
| Info     | 1   | 0              | 1        | 0            |

---

## Medium Findings

### M-1 — Non-constant-time admin token comparison **[FIXED]**

**Location:** `app/main.py:301` (`_admin_auth`)  
**Risk:** Python's `!=` string comparison short-circuits on the first differing byte, creating a timing oracle that an attacker on a reliable low-latency link could exploit to brute-force the `CLEARVIEW_ADMIN_TOKEN` character by character. All admin endpoints (`/admin/*`) rely on this check when `CLEARVIEW_ADMIN_TOKEN` is set.  
**Remediation:** Replace `if token != expected:` with `if not secrets.compare_digest(token, expected):`. Added `import secrets` to the module imports.  
**Status:** Fixed in this run. All tests pass.

---

## Low Findings (Deferred)

### L-1 — Dockerfile runs as root

**Location:** `Dockerfile` (no `USER` directive)  
**Risk:** The container process runs as UID 0. If any dependency or CLI subprocess is compromised, the attacker has root inside the container, which eases further privilege escalation and data exfiltration from the `/data` volume.  
**Remediation proposed:** Add a non-root user in the Dockerfile, e.g.:
```dockerfile
RUN addgroup --system clearview && adduser --system --ingroup clearview clearview
RUN mkdir -p /data && chown clearview:clearview /data
USER clearview
```
**Deferred reason:** Volume ownership (`/data`) requires corresponding `RUN chown` steps; needs integration test to verify the SQLite DB path and file creation work correctly under the new UID.

---

### L-2 — pip/setuptools CVEs in build environment

**Location:** `.venv/bin/pip` (pip 24.0), setuptools 79.0.1  
**Risk:** pip 24.0 has 5 distinct CVEs (PYSEC-2026-1795, -1796, -196, -2875, -2876, -3721) fixed in pip ≥26.2. setuptools 79.0.1 has CVE PYSEC-2026-3447 fixed in ≥83.0.0. The CVEs affect pip itself during package installation; the primary risk is supply-chain attacks at build time when fetching packages from PyPI.  
**Remediation proposed:**  
- In `Dockerfile`: add `RUN pip install --upgrade pip setuptools` after the base image is set.  
- Pin to `pip>=26.2` and `setuptools>=83.0.0` in `pyproject.toml` dev dependencies.  
**Deferred reason:** Upgrading pip/setuptools at build time can alter dependency resolution and should be validated with a full image build and CI run before merging. Not a runtime exposure in the production container (pip isn't exercised after image build).

---

### L-3 — `/metrics` Prometheus endpoint unauthenticated

**Location:** `app/main.py:2538` (`GET /metrics`)  
**Risk:** The endpoint exposes routing statistics including tier usage, provider names, team IDs (as label values in counters), total token output, and cost totals without any authentication. An external observer can infer usage patterns, active providers, team count, and spend trends.  
**Remediation proposed:** Gate `/metrics` behind `_admin_auth` (or a dedicated `CLEARVIEW_METRICS_TOKEN`), or restrict at the network layer (reverse proxy IP allowlist for Prometheus scraper).  
**Deferred reason:** Adding auth to `/metrics` is a behavior change — it would break existing Prometheus scraper setups. Operator must choose approach (code auth or network restriction). Flagged for operator awareness.

---

## Info / Accepted Design Decisions

### I-1 — docker-compose publishes port 8000 on all interfaces

**Location:** `docker-compose.yml:8` (`ports: - "8000:8000"`)  
**Risk:** The service binds to `0.0.0.0:8000` (all interfaces), not just loopback. When `CLEARVIEW_ADMIN_TOKEN` and `CLEARVIEW_CLIENT_KEYS` are unset (the default dev mode), all admin and inference endpoints are accessible to anyone on the local network.  
**Status:** Accepted design for local development. Operators deploying on shared or cloud networks should set `CLEARVIEW_ADMIN_TOKEN` and `CLEARVIEW_CLIENT_KEYS`, or add `127.0.0.1:8000:8000` to the ports entry. No code fix needed; documentation note only.

---

## Auth Coverage Review

All `@app.get/post/delete` routes were checked against the four auth guards:

| Endpoint group | Guard | Notes |
|---|---|---|
| All `/admin/*` | `_admin_auth` | Optional (no-op when env var unset) |
| `/v1/chat/completions`, `/v1/messages`, `/v1/responses`, Gemini shims | `_client_key_allowed` + `_resolve_team` | Optional; documented single-tenant dev mode |
| `/chat/me`, `/chat/conversations*`, `/chat/send*` | `_require_chat_team` | Mandatory cookie/bearer auth |
| `/v1/models`, `/health`, `/feedback` | None | Intentionally open (OpenAI compat, health probe, user-rate-own-response) |
| `/metrics` | None | See L-3 above |
| `/chat`, `/chat/login`, `/chat/logout` | None | Login surface; intentionally open |

No admin route is missing its `_admin_auth` call. No findings under this category.

---

## Injection & Code Execution

- `shell=True` / `os.system`: **not found** — subprocess calls in `app/providers/` use `asyncio.create_subprocess_exec` (safe, no shell interpolation).
- `eval(` / `exec(`: **not found** in application code.
- `pickle` / `yaml.load(`: **not found**.
- SQL queries: all use parameterized queries (`?` placeholders). `f`-string SQL construction in `admin_timeseries`, `admin_calls_detail`, `admin_routing_quality`, `admin_rule_hits`, `admin_shadow_verdicts`, `admin_shadow_compare`, and `admin_ticker` concatenates only constant WHERE/column strings — no user-controlled values are interpolated into the SQL text itself; filter values are passed as tuple params. **No SQL injection risk identified.**

---

## Secrets in Tracked Files

- `git ls-files | grep -iE '\.(env|pem|key|p12|pfx|crt|cer)'`: **no matches**.
- Grep for `sk-`, `AKIA`, `ghp_` in tracked files: **no matches**.
- `.env.example` and `clearview-client.env.example` contain placeholder comments only; confirmed no live values.

---

## Token Handling

- Team token generation (`app/teams.py:117`): uses `secrets.token_hex(16)` — **correct**.
- Admin token comparison: was using `!=`; **fixed in this run** to `secrets.compare_digest`.
- Client key comparison in `_client_key_allowed` (`app/main.py:323`): uses Python `in` set membership, which is hash-based and not constant-time. Risk is lower than the admin path because client keys are high-entropy random values (not guessable character-by-character in practice), but noted for a future hardening pass.

---

## Positive Observations

- No tracked secret files; `.gitignore` covers `.env`.
- Team tokens are generated with `secrets.token_hex`, not `random`.
- All subprocess calls use `create_subprocess_exec` (no shell injection surface).
- SQL is fully parameterized; no f-string user-value interpolation into queries.
- Team token in listing responses is redacted to a short prefix (`_team_to_redacted_dict`).
- Auth guards are consistent across all admin endpoints.
- Cookie auth uses `httponly=True, samesite="lax"` — correct settings.

---

## Remediation Status

| Finding | Severity | Fixed | Deferred |
|---------|----------|-------|---------|
| M-1: Non-constant-time admin token compare | Medium | ✅ | — |
| L-1: Dockerfile runs as root | Low | — | ✅ |
| L-2: pip/setuptools CVEs | Low | — | ✅ |
| L-3: /metrics unauthenticated | Low | — | ✅ |
| I-1: compose publishes on all interfaces | Info | — | ✅ (accepted) |
