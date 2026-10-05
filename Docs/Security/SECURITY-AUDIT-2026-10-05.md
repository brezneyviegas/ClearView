# Security Audit — 2026-10-05

**Repository:** brezneyviegas/clearview  
**Auditor:** Automated daily audit (Claude Sonnet 4.6)  
**Baseline:** No prior audit on file — first run.  
**Branch audited:** `main` @ `e8ad2e3`

---

## Severity Summary

| Severity | Count | Fixed This Run | Deferred |
|----------|-------|---------------|----------|
| High     | 0     | —             | —        |
| Medium   | 3     | 2             | 1        |
| Low      | 3     | 0             | 3        |
| Info     | 1     | —             | —        |

---

## Medium Findings

### M-1 · Non-constant-time admin token comparison — **FIXED**

**Location:** `app/main.py:299` — `_admin_auth`  
**Risk:** The admin bearer token comparison used Python's `!=` operator, which may leak timing information that could assist brute-force attacks against `CLEARVIEW_ADMIN_TOKEN`. Although timing oracle attacks are difficult over a network, this violates best practice for secret comparison.  
**Remediation applied:** Replaced `if token != expected:` with `if not secrets.compare_digest(token, expected):` and added `import secrets` to the module imports.

---

### M-2 · Dockerfile runs as root (no non-root USER) — **FIXED**

**Location:** `Dockerfile`  
**Risk:** The container image had no `USER` directive, so the application process ran as UID 0 (root). A container escape or RCE vulnerability would immediately yield root on the host kernel.  
**Remediation applied:** Added `adduser --disabled-password --gecos "" --uid 1000 clearview` and `USER clearview` to the Dockerfile. The `/data` volume directory is `chown`-ed to the new user before the `USER` switch so the database remains writable.

---

### M-3 · `/metrics` endpoint lacks authentication — **DEFERRED**

**Location:** `app/main.py:2538` — `GET /metrics`  
**Risk:** The Prometheus metrics endpoint is unauthenticated. It exposes aggregated telemetry including per-tier/provider/team_id request counts, native cost totals, output token counts, and drift percentages. On a shared network an attacker can determine which providers are active, per-team usage patterns, and overall spend rates without any credentials.  
**Proposed design (deferred):** Introduce a `CLEARVIEW_METRICS_TOKEN` environment variable. When set, `GET /metrics` requires `Authorization: Bearer <token>` (checked via `secrets.compare_digest`). When unset, the endpoint remains open for zero-config local Prometheus scraping. Guard should be opt-in rather than breaking existing scrapers.  
**Not fixed this run:** Adding optional auth to `/metrics` changes observable behavior for existing Prometheus deployments. Requires explicit operator sign-off.

---

## Low Findings

### L-1 · Non-constant-time client key comparison — **DEFERRED**

**Location:** `app/main.py:324` — `_client_key_allowed`  
**Risk:** Client API keys are checked via `cand in allowed` where `allowed` is a Python `set`. Python set membership is hash-based and not strictly constant-time. Practical timing oracle attacks on this path are unlikely, but it is a best-practice violation.  
**Proposed design:** Replace the set check with an explicit loop over `allowed` values using `secrets.compare_digest`, short-circuiting on match. Alternatively, hash all keys on startup so lookup is on digests.  
**Not fixed this run:** The practical risk is very low; a safe refactor needs design discussion to avoid false-reject edge cases.

---

### L-2 · Ollama port 11434 published to all interfaces in docker-compose — **DEFERRED**

**Location:** `docker-compose.yml` — `ollama.ports`  
**Risk:** When using the `--profile local` compose variant, Ollama's port 11434 is bound to `0.0.0.0:11434`. Ollama has no built-in authentication; any host on the local network (or internet, if deployed on a cloud VM with open security groups) can call the Ollama API directly, bypassing ClearView's auth and budget controls entirely.  
**Proposed design:** Change the Ollama port mapping to `127.0.0.1:11434:11434` so only the gateway container and the local machine can reach it. Internal compose traffic uses the `ollama` service hostname (unaffected by the publish change).  
**Not fixed this run:** Changing the port binding could break existing deployments where operators intentionally expose Ollama. Operator must confirm intent.

---

### L-3 · Known CVEs in build-time pip and setuptools — **DEFERRED**

**Location:** Build environment (`.venv`)  
**Details from `pip-audit`:**

| Package    | Version | CVE IDs                                                              | Fix Version |
|------------|---------|----------------------------------------------------------------------|-------------|
| pip        | 24.0    | PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-196, PYSEC-2026-3721 | 26.2 |
| setuptools | 79.0.1  | PYSEC-2026-3447                                                      | 83.0.0      |

**Risk:** These CVEs affect the `pip` and `setuptools` tools used during development and image builds, not ClearView's application runtime. However, a vulnerable `pip` in the Docker image's base layer could be exploited in supply-chain attack scenarios (e.g., a `pip install` of a malicious package that leverages the pip vulnerability). No application-level (`litellm`, `fastapi`, etc.) CVEs were found.  
**Proposed remediation:** Add `RUN pip install --upgrade pip setuptools` to the Dockerfile (before or after the main `pip install .`) so the image ships with a patched pip version.  
**Not fixed this run:** Low runtime risk; the fix is simple but touches the build layer and should be reviewed alongside the next Dockerfile update.

---

## Carried-Over Deferred Findings

None — this is the first audit.

---

## Positive Observations

- **Auth coverage complete** on all admin routes (`/admin/*`) and all chat routes (`/chat/*`). Every route calls `_admin_auth`, `_client_key_allowed`+`_resolve_team`, or `_require_chat_team` as required.
- **Team token generation** (`app/teams.py:117`) correctly uses `secrets.token_hex(16)` — 128 bits of entropy per token.
- **No tracked secrets** — `git ls-files` found only `.env.example` and `clearview-client.env.example`, both example files with no live key values. No `sk-`, `AKIA`, or `ghp_` patterns in any tracked file.
- **No injection patterns** — grep for `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, `yaml.load(` returned zero hits.
- **SQL is parameterized** — all SQLite queries in `admin_timeseries`, `admin_calls_detail`, `admin_routing_quality`, etc. build `WHERE` clauses with `?` placeholders and parameter tuples; no f-string value interpolation into queries.
- **`/feedback` endpoint intentionally open** — documented in the source; callers rate their own responses using the request_id as a handle (no admin gate needed).
- **`/health` intentionally open** — standard health-check pattern.
- **`/v1/models` intentionally open** — required by OpenAI client SDK compatibility; leaks only model names already derivable from any successful completion call.

---

## Remediation Status

| ID  | Finding                                   | Status  | Branch / Commit         |
|-----|-------------------------------------------|---------|-------------------------|
| M-1 | Non-constant-time admin token compare     | Fixed   | security/audit-2026-10-05 |
| M-2 | Dockerfile runs as root                   | Fixed   | security/audit-2026-10-05 |
| M-3 | `/metrics` unauthenticated                | Deferred | — |
| L-1 | Non-constant-time client key compare      | Deferred | — |
| L-2 | Ollama port exposed on all interfaces     | Deferred | — |
| L-3 | pip/setuptools CVEs in build environment  | Deferred | — |
