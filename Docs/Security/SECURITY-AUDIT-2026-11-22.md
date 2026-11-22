# Security Audit — 2026-11-22

**Auditor:** Automated daily audit (Claude Code)  
**Repo:** brezneyviegas/clearview  
**Branch audited:** main (HEAD e8ad2e3)  
**Audit branch:** security/audit-2026-11-22  
**Previous audit:** None (first run)

---

## Severity Summary

| Severity | New | Fixed this run | Deferred |
|----------|-----|----------------|----------|
| High     | 0   | 0              | 0        |
| Medium   | 3   | 3              | 0        |
| Low      | 3   | 0              | 3        |

---

## Medium Findings

### M-1 — Non-constant-time admin token comparison

**File/location:** `app/main.py:300` (`_admin_auth`)  
**Risk:** Timing side-channel. `token != expected` short-circuits on the first mismatched byte, allowing an attacker with high-precision latency measurements to enumerate the correct `CLEARVIEW_ADMIN_TOKEN` one byte at a time against a network-local or cloud-colocated adversary.  
**Remediation (applied):** Replaced `token != expected` with `not secrets.compare_digest(token, expected)`. Added `import secrets` to the module's stdlib imports.  
**Status:** Fixed in this run.

---

### M-2 — `/metrics` endpoint exposed without authentication

**File/location:** `app/main.py:2538` (`metrics` route)  
**Risk:** The Prometheus `/metrics` endpoint returned aggregate telemetry including team IDs, provider names, cumulative cost counters, and total tokens served — with no authentication gate. Anyone who can reach the service could enumerate team identifiers and infer usage patterns. All other `/admin/*` routes check `_admin_auth`; `/metrics` did not.  
**Remediation (applied):** Added `_admin_auth(request)` as the first statement of the `metrics` handler. Added `request: Request` parameter to the handler signature.  
**Status:** Fixed in this run.

---

### M-3 — Dockerfile runs as root

**File/location:** `Dockerfile`  
**Risk:** The Docker image ran the application process as `root` (UID 0). If the application is compromised via an upstream vulnerability or a path-traversal attack, the attacker gains root access inside the container, making container-escape or volume-pivot trivially easier.  
**Remediation (applied):** Added a `clearview` system user, chowned `/data` to that user, and added `USER clearview` before `EXPOSE`/`CMD`.  
**Status:** Fixed in this run.

---

## Low Findings (Deferred)

### L-1 — docker-compose.yml binds port 8000 to all interfaces

**File/location:** `docker-compose.yml:8` (`ports: - "8000:8000"`)  
**Risk:** When `CLEARVIEW_ADMIN_TOKEN` is unset (the default dev configuration), the gateway and all admin endpoints are unauthenticated and reachable from the LAN/internet on any host that runs `docker compose up`. Even with the token set, the attack surface is wider than necessary.  
**Proposed design:** Change to `"127.0.0.1:8000:8000"` for the default profile. Add a commented-out `"0.0.0.0:8000:8000"` variant for intentional public exposure. For production: use a reverse proxy (nginx/Caddy) in the compose file and bind clearview to `127.0.0.1` only.  
**Status:** Deferred — behavior-changing for existing multi-host deployments.

---

### L-2 — Session cookie missing `Secure` flag

**File/location:** `app/main.py:2651` (`chat_login`)  
**Risk:** The `cv_session` cookie is set with `httponly=True, samesite="lax"` but no `secure=True`. Without the Secure flag, the cookie is transmitted in plaintext over HTTP requests, exposing the team bearer token to network eavesdroppers when the service is accessed over plain HTTP.  
**Proposed design:** Add `secure=True` to the `set_cookie` call. Gate this behind a config option (`CLEARVIEW_HTTPS_COOKIE=1`) so local HTTP dev isn't broken. Alternatively, enforce HTTPS at the reverse proxy layer.  
**Status:** Deferred — would break local dev (localhost HTTP). Needs config option design.

---

### L-3 — pip 24.0 and setuptools 79.0.1 CVEs (build/CI tooling)

**Tool:** pip-audit  
**Findings:**  
- `pip 24.0` — PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-196, PYSEC-2026-3721. Fix versions: pip ≥ 26.2.  
- `setuptools 79.0.1` — PYSEC-2026-3447, GHSA-xmj3-9gf7-h52w. Fix version: setuptools ≥ 84.1.0.  
**Risk:** These affect the build/install toolchain, not the runtime Docker image (the Dockerfile uses `pip install .` during build only). The application itself imports none of these packages at runtime. Risk is primarily in CI environments and developer machines where `pip install` is run.  
**Proposed design:** Pin `pip>=26.2` and `setuptools>=84.1.0` in a `[project.optional-dependencies]` dev group or in a `requirements-dev.txt`; upgrade the base image in the Dockerfile from `python:3.12-slim` (which bundles pip 24) to a newer tag, or add `RUN pip install --upgrade pip setuptools` to the Dockerfile after installation.  
**Status:** Deferred — Dockerfile build toolchain upgrade; no runtime impact.

---

## Positive Observations

- **Token generation is correct:** `cv_team_` tokens minted with `secrets.token_hex(16)` (32 hex chars → 128 bits entropy). No weak RNG.
- **All SQL is parameterized:** Every query across `telemetry.py`, `teams.py`, `cache.py`, and the inline sqlite3 blocks in `main.py` uses `?` placeholders with bound params. No f-string or concatenation SQL injection risk.
- **No dangerous patterns:** `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, and `yaml.load` (unsafe) are absent from all `app/` files. `config.py` uses `yaml.safe_load`.
- **No tracked secrets:** `git ls-files` reveals no `.env`, `.pem`, or key files. Test files contain only synthetic monkeypatched values (`sk-x`, `sk-test`) that are not live credentials.
- **Admin routes are gated:** All `/admin/*` routes call `_admin_auth`; `/chat/*` routes requiring auth call `_require_chat_team`.
- **Team tokens are redacted in list responses:** `_team_to_redacted_dict` truncates the full bearer token to a short prefix; only the create endpoint returns the full token.

---

## Remediation Status

| Finding | Location | Status |
|---------|----------|--------|
| M-1: Timing-unsafe token compare | `app/main.py:300` | Fixed |
| M-2: `/metrics` unauthenticated | `app/main.py:2538` | Fixed |
| M-3: Dockerfile root user | `Dockerfile` | Fixed |
| L-1: docker-compose all-interfaces | `docker-compose.yml:8` | Deferred |
| L-2: Cookie missing Secure flag | `app/main.py:2651` | Deferred |
| L-3: pip/setuptools CVEs | build toolchain | Deferred |
