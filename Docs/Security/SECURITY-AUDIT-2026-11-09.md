# ClearView Security Audit — 2026-11-09

**Auditor:** Automated daily audit (Claude Code)  
**Branch audited:** `main` (HEAD `e8ad2e3`)  
**Audit branch:** `security/audit-2026-11-09`  
**Previous report:** None (first audit)

---

## Severity Summary

| Severity | New | Fixed this run | Deferred |
|----------|-----|----------------|----------|
| High     | 2   | 2              | 0        |
| Medium   | 2   | 0              | 2        |
| Low      | 1   | 0              | 1        |

---

## High Findings

### H-1 · Non-constant-time admin token comparison — FIXED

**File:** `app/main.py:301`  
**Risk:** `token != expected` uses Python's default string equality, which short-circuits on the first mismatched byte and leaks timing information. An attacker on the same network who can send many requests can distinguish "wrong prefix" from "wrong suffix" and enumerate the token one character at a time (timing oracle attack).  
**Remediation applied:** Replaced with `secrets.compare_digest(token, expected)` (also added `import secrets` at the top of the module).

### H-2 · Dockerfile runs container as root — FIXED

**File:** `Dockerfile`  
**Risk:** The image had no `USER` directive, so the application process runs as UID 0 inside the container. If the application is compromised, the attacker has root within the container, which dramatically widens the blast radius (e.g., easier privilege escalation on misconfigured container runtimes, unrestricted writes to any bind-mounted host path).  
**Remediation applied:** Added `RUN useradd -m -u 1000 clearview && mkdir -p /data && chown clearview:clearview /data` before the `VOLUME` declaration, and `USER clearview` before `CMD`.

---

## Medium Findings

### M-1 · `/metrics` Prometheus endpoint unauthenticated — DEFERRED

**File:** `app/main.py:2538`  
**Risk:** The `/metrics` endpoint exposes per-team spending, model selection, error rates, and routing decisions without any authentication. An unauthenticated requester can enumerate team activity and cost profiles. When `CLEARVIEW_ADMIN_TOKEN` is set for other admin routes, this endpoint is a gap.  
**Proposed design:** Add `_admin_auth(request)` to `metrics()`. Operators must configure their Prometheus scrape job with `bearer_token` or `basic_auth`; document this in the deployment guide. Deferred because adding auth to a scrape endpoint is a behavior-breaking change that requires Prometheus reconfiguration.

### M-2 · docker-compose ollama service binds on all interfaces — DEFERRED

**File:** `docker-compose.yml:19` (`ports: - "11434:11434"`)  
**Risk:** When the `local` profile is active, the Ollama service binds port 11434 on all network interfaces (`0.0.0.0`). On a shared or cloud host, this exposes the Ollama API (which has no built-in authentication) to anyone who can reach the host's IP. Lateral movement or model abuse is then trivial.  
**Proposed fix:** Change to `"127.0.0.1:11434:11434"` so Ollama is only reachable from localhost. The ClearView gateway (also local) can still reach it; external hosts cannot. Deferred because this affects operators who explicitly want LAN access to Ollama and must be documented as a deliberate trade-off.

---

## Low Findings

### L-1 · pip 24.0 and setuptools 79.0.1 CVEs in dev toolchain — DEFERRED

**Tool:** `pip-audit`  
**Packages affected (build/dev toolchain only, not runtime):**

| Package    | Version | CVE IDs                                                               | Fix version |
|------------|---------|-----------------------------------------------------------------------|-------------|
| pip        | 24.0    | PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-196, PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-3721 | ≥ 26.1.2 |
| setuptools | 79.0.1  | PYSEC-2026-3447, GHSA-xmj3-9gf7-h52w                                 | ≥ 84.1.0 |

**Risk:** These packages are build/install toolchain, not imported by the running application. The CVEs cannot be triggered at runtime. However, they affect CI/build pipelines where `pip install` is used.  
**Proposed fix:** Pin `pip>=26.1.2` and `setuptools>=84.1.0` in the base image layer (`RUN pip install --upgrade pip setuptools` in Dockerfile or in CI setup). Deferred — no runtime exposure, low urgency.

---

## Checks Performed (Minimum Sweep)

| Check | Result |
|-------|--------|
| All `@app.get/post/delete` routes call `_admin_auth`, `_client_key_allowed`, `_resolve_team`, or `_require_chat_team` | **PASS** — `/v1/models`, `/health`, and `/feedback` are intentionally open (documented); `/metrics` is an undocumented gap (M-1 above) |
| `shell=True` / `os.system` / `eval` / `exec` / `pickle` / unsafe `yaml.load` | **PASS** — None found. Subprocess calls use `create_subprocess_exec` (not shell) with fixed argument lists |
| SQL parameterization — no f-string/concat queries interpolating user values | **PASS** — All `{where}` f-strings use fixed clause templates; user values only appear as `?` bound parameters |
| Tracked secrets — `.env`/`.key`/`.pem` files or live-looking keys (`sk-`, `AKIA`, `ghp_`) | **PASS** — None found |
| Secret comparisons use `secrets.compare_digest` | **PASS after fix** — H-1 remediated |
| Token generation uses `secrets` module | **PASS** — `secrets.token_hex()` used in `app/chat.py:62` and `app/teams.py:117` |
| Dockerfile non-root `USER` | **PASS after fix** — H-2 remediated |
| docker-compose unauthenticated services | **PARTIAL** — M-2 deferred |
| `pip-audit` CVEs | **CVEs in build toolchain only** — L-1 deferred |

---

## Positive Observations

- Team token generation uses `secrets.token_hex(16)` — 128 bits of entropy, correct.
- Subprocess invocations in all three CLI adapters (`claude_cli`, `codex_cli`, `gemini_cli`) use `create_subprocess_exec` / `subprocess.run` without `shell=True`. User-supplied model names are passed as literal arguments, not interpolated into shell commands.
- All `/admin/*` endpoints consistently call `_admin_auth`.
- Team tokens are validated on every request via `_resolve_team`; disabled teams are rejected.
- The token listing endpoint (`GET /admin/teams`) redacts tokens to an 18-char prefix — full tokens are only returned on creation.
- No `.env`, API key, or certificate files are tracked in git.
- Budget enforcement and team tier-gating are in place.

---

## Deferred Items Carried Forward

| ID  | Description | First seen |
|-----|-------------|------------|
| M-1 | `/metrics` unauthenticated | 2026-11-09 |
| M-2 | docker-compose ollama binds all interfaces | 2026-11-09 |
| L-1 | pip/setuptools dev-toolchain CVEs | 2026-11-09 |
