# Security Audit — 2026-09-23

**Repo:** ClearView (Python/FastAPI LLM gateway)  
**Auditor:** Automated daily-security-audit routine  
**Branch audited:** `main` @ e8ad2e3  
**Prior report:** None (first audit)  
**Scope:** Full codebase sweep (no prior baseline to diff against)

---

## Severity Summary

| Severity | New | Fixed This Run | Deferred |
|----------|-----|----------------|----------|
| High     | 0   | —              | —        |
| Medium   | 2   | 2              | 1        |
| Low      | 2   | 1              | 1        |
| **Total**| **4**| **3**         | **2**    |

---

## Findings

### Medium

#### M-1 · Non-constant-time admin token comparison  ✅ FIXED
**File:** `app/main.py:300`  
**Risk:** `_admin_auth` used Python's `!=` operator (`token != expected`) to compare the incoming Bearer token against the expected value. CPython's string equality short-circuits on the first differing byte, leaking timing information. An attacker with many high-precision requests could byte-by-byte recover `CLEARVIEW_ADMIN_TOKEN` via a timing oracle, bypassing all admin-route protection.  
**Remediation:** Replaced `token != expected` with `not secrets.compare_digest(token, expected)`. Added `import secrets` to `app/main.py` (was absent).  
**Status:** Fixed in this branch.

---

#### M-2 · Dockerfile runs process as root  ✅ FIXED
**File:** `Dockerfile`  
**Risk:** The image had no `USER` directive; the FastAPI process ran as root inside the container. If an adversary can reach an unauthenticated endpoint and exploit a future bug (e.g. a path-traversal or deserialization issue), root inside the container provides full write access to the filesystem and any mounted secrets.  
**Remediation:** Added a `clearview` system user/group. `RUN addgroup --system clearview && adduser --system --ingroup clearview clearview` + `USER clearview`. The `/data` volume directory is `chown`ed to this user before the VOLUME declaration so named-volume mounts start writable. Build tools (pip, setuptools) upgraded to latest in the same layer (see L-1).  
**Status:** Fixed in this branch.

---

#### M-3 · docker-compose publishes ClearView on all interfaces  ⏸ DEFERRED
**File:** `docker-compose.yml:8`  
**Risk:** `ports: ["8000:8000"]` binds to `0.0.0.0`, exposing the gateway (including the unauthenticated `/v1/models`, `/metrics`, `/health`, `/feedback`, and the chat HTML page) on every network interface of the Docker host. In a shared-network or cloud environment this means the gateway is reachable from other machines without any network-layer restriction.  
**Remediation proposed:** Change to `"127.0.0.1:8000:8000"` for development deployments; document that production deployments must place ClearView behind a reverse proxy (nginx, Caddy, or a load balancer) that handles TLS termination and optionally adds IP allowlisting.  
**Deferred because:** Changing the default bind address could break legitimate shared-LAN or multi-container setups. This is an operator-configuration concern; the correct fix depends on the deployment topology and requires explicit sign-off.

---

### Low

#### L-1 · Outdated build tools with known CVEs in Docker image  ✅ FIXED
**Affected versions:** `pip 24.0`, `setuptools 79.0.1`  
**CVEs:**
- pip 24.0: PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-196, PYSEC-2026-3721 (fix: pip ≥ 26.2)
- setuptools 79.0.1: PYSEC-2026-3447 (fix: setuptools ≥ 83.0.0)

**Risk:** These vulnerabilities exist in the build toolchain baked into the Docker image. While they are not runtime libraries, they are present in the image layer and could be exploited during a supply-chain attack on the build process or if `pip` is invoked at runtime (e.g. to install a plugin).  
**Remediation:** Added `pip install --no-cache-dir --upgrade pip setuptools` as the first install step in the Dockerfile, ensuring the latest patched versions are used at build time.  
**Status:** Fixed in this branch as part of M-2 Dockerfile changes.

---

#### L-2 · `/metrics` endpoint unauthenticated  ⏸ DEFERRED
**File:** `app/main.py:2538` (`@app.get("/metrics")`)  
**Risk:** The Prometheus metrics endpoint is open to any caller with network access to port 8000. It exposes: aggregate cost figures (native and plan-equivalent USD), output-token volumes, drift percentages, and—importantly—`team_id` label values on per-team counters. An adversary can use this to infer business intelligence: which teams exist, how much each team is spending, which providers and tiers are used, and overall system load.  
**Remediation proposed:** Gate `/metrics` behind `_admin_auth` when `CLEARVIEW_ADMIN_TOKEN` is set, or add a separate `CLEARVIEW_METRICS_TOKEN` env var. Scrape configuration in the Prometheus operator would need to include the bearer token.  
**Deferred because:** Adding authentication to a Prometheus endpoint is a non-trivial operational change (requires updating scrape configs). This could break existing monitoring setups without explicit operator action. Documenting the exposure and providing an opt-in auth guard is the right path.

---

## Carried-Over Deferred Findings

None — this is the first audit.

---

## Audit Checklist Results

| Check | Result |
|-------|--------|
| Auth coverage: every admin route calls `_admin_auth` | ✅ All `/admin/*` routes call `_admin_auth`. `/v1/models`, `/health`, `/feedback`, `/chat` (HTML), `/chat/login`, `/chat/logout` intentionally open with documented reasons. `/metrics` open — see L-2. |
| Auth coverage: chat routes call `_require_chat_team` | ✅ All `/chat/*` data routes require cookie/token auth. |
| Auth coverage: API routes call `_client_key_allowed` + `_resolve_team` | ✅ `/v1/chat/completions`, `/v1/messages`, `/v1/responses`, Gemini shim routes all pass through `_handle_chat_completions` which calls both. |
| Injection: `shell=True`, `os.system`, `eval(`, `exec(`, `pickle` | ✅ None found. Subprocess calls use `asyncio.create_subprocess_exec` (arg list, no shell). |
| Injection: unsafe `yaml.load(` | ✅ All YAML loading uses `yaml.safe_load`. |
| SQL parameterization | ✅ All queries use `?` placeholders. f-strings only construct clause structure from hardcoded column name lists — never from user-controlled values. |
| Tracked secrets (env/key/pem files, live-looking key strings) | ✅ Only `.env.example` and `clearview-client.env.example` tracked; both are templates with no real values. No `sk-`, `AKIA`, or `ghp_` patterns in tracked files. |
| Token comparison uses `secrets.compare_digest` | ✅ Fixed (M-1). |
| Token generation uses `secrets` module | ✅ `cv_team_` tokens: `secrets.token_hex(16)`. `cv_chat_` IDs: `secrets.token_hex(8)`. |
| `random` module not used for security decisions | ✅ `random` used only for shadow-call sampling rate (non-security). |
| Dockerfile: non-root `USER` | ✅ Fixed (M-2). |
| docker-compose: ports | ⚠️ Port 8000 published on all interfaces — see M-3 (deferred). |
| pip-audit: runtime CVEs | ✅ No CVEs in application runtime dependencies. Build tools (pip, setuptools) upgraded — see L-1. |

---

## Positive Observations

- **Consistent admin auth**: every `/admin/*` endpoint correctly gates on `_admin_auth`; the pattern is hard to miss when adding new admin routes.
- **Team token design**: team tokens (`cv_team_`) are redacted to a short prefix in list views (`_team_to_redacted_dict`), preventing token leakage through the admin UI.
- **Budget enforcement**: per-team daily and monthly caps are enforced before routing, with explicit 429 responses naming the cap scope.
- **No shell injection surface**: all external process calls use `asyncio.create_subprocess_exec` with argument lists.
- **YAML safety**: `yaml.safe_load` used throughout — no deserialisation gadget path.
- **parameterized SQL**: dynamic WHERE clauses are built from hardcoded clause fragments; user-controlled values only appear as `?` parameters.

---

## Remediation Status

| ID  | Finding                                    | Status   | Commit |
|-----|--------------------------------------------|----------|--------|
| M-1 | Non-constant-time admin token comparison   | ✅ Fixed | this PR |
| M-2 | Dockerfile runs as root                    | ✅ Fixed | this PR |
| M-3 | docker-compose all-interface bind          | ⏸ Deferred | — |
| L-1 | Outdated pip/setuptools CVEs in image      | ✅ Fixed | this PR |
| L-2 | `/metrics` endpoint unauthenticated        | ⏸ Deferred | — |
