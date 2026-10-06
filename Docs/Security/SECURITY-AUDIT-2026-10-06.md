# Security Audit — 2026-10-06

**Repository:** ClearView (Python/FastAPI LLM gateway)  
**Auditor:** Automated daily audit  
**Branch audited:** `main` @ `e8ad2e3`  
**Audit branch:** `security/audit-2026-10-06`

---

## Severity Summary

| Severity | New | Fixed this run | Deferred | Carried over |
|----------|-----|----------------|----------|--------------|
| High     | 0   | 0              | 0        | 0            |
| Medium   | 1   | 1              | 0        | 0            |
| Low      | 3   | 1              | 2        | 0            |
| Info     | 2   | 0              | 2        | 0            |

---

## Findings

### Medium

#### M-1 — Timing-unsafe admin token comparison ✅ Fixed

**File:** `app/main.py:301` (`_admin_auth`)  
**Risk:** The original comparison `token != expected` is not constant-time. An attacker able to make many requests and measure response latency can determine the correct admin bearer token one character at a time (timing side-channel). This is exploitable if `CLEARVIEW_ADMIN_TOKEN` is set and the service is network-accessible.  
**Remediation (applied):** Replaced with `secrets.compare_digest(token, expected)` which runs in constant time regardless of match position. Added `import secrets` to main.py imports.

---

### Low

#### L-1 — Dockerfile runs as root ✅ Fixed

**File:** `Dockerfile`  
**Risk:** The container ran as root (uid 0) because no `USER` directive was set. A process escape or path traversal in the app would yield a root shell inside the container, significantly lowering the cost of escaping to the host.  
**Remediation (applied):** Added `RUN useradd -m -u 1000 clearview && mkdir -p /data && chown clearview /data` before the `VOLUME` declaration, then `USER clearview`. The `VOLUME` directive is placed after `USER` so the named volume mount inherits the ownership set during build.

#### L-2 — `/metrics` endpoint unauthenticated (deferred)

**File:** `app/main.py:2538`  
**Risk:** The Prometheus `/metrics` endpoint exposes per-tier, per-provider, and per-`team_id` request counters and cost totals without any authentication. In a multi-tenant deployment this leaks operational metadata (which teams are active, which providers are used, aggregate spend) to any network-reachable caller.  
**Proposed design:** Add an optional `CLEARVIEW_METRICS_TOKEN` env var, checked by a thin `_metrics_auth` guard mirroring `_admin_auth`. When unset the endpoint remains open for scraper convenience in local deployments. Deferred because this is a behavior-changing addition and needs agreement on the token-passing convention for Prometheus scrapers.

#### L-3 — pip 24.0 / setuptools 79.0.1 CVEs in build environment (deferred)

**Source:** `pip-audit` output  
**Packages:** `pip==24.0` (PYSEC-2026-1795, -1796, -196, -2875, -2876, -3721; fix: ≥26.2.1), `setuptools==79.0.1` (PYSEC-2026-3447; fix: ≥83.0.0)  
**Risk:** These affect the build/install tooling, not the application's runtime dependencies. Exploitation requires a malicious package on PyPI or a compromised dependency index — the application's own dependencies (FastAPI, litellm, etc.) showed no CVEs. Risk is low for production containers but non-zero in CI pipelines that run `pip install`.  
**Proposed design:** Pin `pip>=26.2.1` and `setuptools>=83.0.0` in the Dockerfile's build stage (`RUN pip install --upgrade pip setuptools`), or switch to a newer base image that ships a patched pip. Deferred as a build-environment hardening task with no urgency for deployed containers.

---

### Informational

#### I-1 — Admin endpoints open by default (no `CLEARVIEW_ADMIN_TOKEN`)

**File:** `app/main.py:294-296` (`_admin_auth`)  
**Note:** When `CLEARVIEW_ADMIN_TOKEN` is not set, all `/admin/*` routes are open to unauthenticated callers. The code documents this as "dev mode: open" and it is an intentional UX choice for local single-user deployments. Operators deploying ClearView on a shared network must set this token. No change required; document prominently in README / deployment guide.

#### I-2 — docker-compose publishes port 8000 on all interfaces

**File:** `docker-compose.yml:8`  
**Note:** `ports: "8000:8000"` binds to `0.0.0.0:8000` by default, making the gateway reachable from any host on the machine's network. For a local developer tool this is acceptable, but operators on shared machines should change this to `"127.0.0.1:8000:8000"` if they do not intend to expose the service to the LAN. No change required in this audit; add a note to the deployment docs.

---

## Minimum Sweep Results

| Check | Result |
|-------|--------|
| All `@app.*` routes in `main.py` call `_admin_auth`, `_client_key_allowed`, `_resolve_team`, or `_require_chat_team`, or have a documented reason to be open | ✅ Pass — `/v1/models`, `/health`, `/metrics`, `/feedback`, and `/chat` (login page) are intentionally open; all `/admin/*` routes consistently call `_admin_auth`; `/v1/chat/completions` and compat shims call `_client_key_allowed` + `_resolve_team` |
| No `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, unsafe `yaml.load` | ✅ Pass — CLI providers use `asyncio.create_subprocess_exec` (not `shell=True`); no other dangerous patterns found |
| SQL is parameterized | ✅ Pass — f-string WHERE clauses in `telemetry.py` and `teams.py` use only hardcoded column names; all user-supplied values are bound as `?` parameters |
| No tracked secrets (env/key/pem files or live-looking keys) | ✅ Pass — `git ls-files` shows no `.env`, `.pem`, or `.key` files; grep for `sk-`, `AKIA`, `ghp_` found only `doctor.py` and test files with placeholder/example strings |
| Secret comparisons use `secrets.compare_digest` | ✅ Pass (after this run's fix) — `_admin_auth` now uses `secrets.compare_digest`; token generation uses `secrets.token_hex` |
| Dockerfile has non-root `USER` | ✅ Pass (after this run's fix) — `USER clearview` added |
| docker-compose does not publish unauthenticated services beyond loopback | ⚠️ See I-2 — port 8000 is published on all interfaces; acceptable for local dev |
| `pip-audit` CVEs | ⚠️ See L-3 — pip and setuptools in build tooling have CVEs; no runtime app dependency CVEs |

---

## Carried-Over Deferred Items

*None — this is the first audit.*

---

## Positive Observations

- Token generation for team IDs (`cv_team_` prefix + `secrets.token_hex(16)`) and chat conversation IDs use the `secrets` module — cryptographically strong.
- All CLI provider subprocess calls use `asyncio.create_subprocess_exec` with arguments as a list, avoiding shell injection entirely.
- Admin route coverage is consistent — every `/admin/*` endpoint explicitly calls `_admin_auth`.
- The team token is redacted in list responses (`id_short` prefix only), preventing admin shoulder-surfing from leaking full bearer tokens.
- Budget enforcement, team isolation, and tier-gating are implemented with defense-in-depth (team daily → team monthly → global daily).
- No secrets found in tracked files.

---

## Remediation Status

| Finding | Status |
|---------|--------|
| M-1 Timing-unsafe admin token compare | ✅ Fixed — `secrets.compare_digest` in this commit |
| L-1 Dockerfile runs as root | ✅ Fixed — `USER clearview` added in this commit |
| L-2 `/metrics` unauthenticated | ⏳ Deferred — needs design decision on Prometheus scraper token convention |
| L-3 pip/setuptools CVEs in build tooling | ⏳ Deferred — build-environment hardening, low urgency |
| I-1 Admin open by default | ⏳ Deferred — documented intentional dev-mode behavior |
| I-2 docker-compose all-interface binding | ⏳ Deferred — acceptable for local dev; recommend docs note |
