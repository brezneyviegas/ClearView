# Security Audit — 2026-11-13

**Repository:** ClearView (Python/FastAPI LLM gateway)  
**Auditor:** Automated daily audit  
**Branch audited:** `main` (HEAD `e8ad2e3`)  
**Audit branch:** `security/audit-2026-11-13`  
**Previous report:** None (first audit)

---

## Severity Summary

| Severity | New | Fixed this run | Deferred | Carried over |
|----------|-----|----------------|----------|--------------|
| High     | 0   | 0              | 0        | 0            |
| Medium   | 2   | 1              | 1        | 0            |
| Low      | 4   | 1              | 3        | 0            |

---

## Findings

### Medium

#### M-1 — Non-constant-time admin token comparison (FIXED)

**File/location:** `app/main.py:301` (`_admin_auth`)  
**Risk:** The original comparison `token != expected` exits early on the first mismatched byte, creating a timing side-channel. An attacker who can make many rapid requests (or proxy-measure RTT) can brute-force the CLEARVIEW_ADMIN_TOKEN one character at a time.  
**Remediation applied:** Replaced with `secrets.compare_digest(token, expected)`, which runs in constant time regardless of the string contents. `import secrets` added to the top of the file.  
**Status:** ✅ Fixed in this run.

---

#### M-2 — `/metrics` endpoint unauthenticated (DEFERRED)

**File/location:** `app/main.py:2538` (`metrics` route)  
**Risk:** The Prometheus `/metrics` endpoint is open to any unauthenticated client on the network. It exposes call counts broken down by `team_id`, `tier`, `provider`, and `status`, plus aggregate cost figures. This leaks organisational metadata (which teams exist, their billing volume, provider choices).  
**Proposed design:** Gate with `_admin_auth(request)` at the top of the `metrics()` handler, mirroring every other admin endpoint. Scraping jobs should pass `Authorization: Bearer <CLEARVIEW_ADMIN_TOKEN>` in their scrape config (`bearer_token` field for Prometheus). This change will break existing scrapers that do not pass a token; operators should update their Prometheus config before enabling CLEARVIEW_ADMIN_TOKEN.  
**Status:** ⏳ Deferred — adding auth here is behavior-changing (breaks existing unauthenticated scrapers). Needs an operator-side Prometheus config change first.

---

### Low

#### L-1 — Dockerfile runs as root (FIXED)

**File/location:** `Dockerfile` (no `USER` directive before `CMD`)  
**Risk:** The container process runs as `root` (UID 0). If an attacker achieves RCE via a dependency vulnerability, they immediately have root within the container, simplifying privilege escalation and container-escape attempts.  
**Remediation applied:** Added `RUN useradd -r -u 1001 clearview` and `USER clearview` before `CMD`. The `/data` volume `chown` is best-effort because the volume may be mounted read-only at build time.  
**Status:** ✅ Fixed in this run.

---

#### L-2 — docker-compose publishes services on all interfaces (DEFERRED)

**File/location:** `docker-compose.yml:8` (`ports: - "8000:8000"`) and `:23` (`ports: - "11434:11434"` for ollama)  
**Risk:** Docker publishes these ports on `0.0.0.0` by default, making ClearView and ollama reachable from any network interface. On a shared or cloud host this exposes the gateway and the local LLM endpoint to other machines without an additional firewall rule.  
**Proposed design:** Bind to loopback: `"127.0.0.1:8000:8000"` and `"127.0.0.1:11434:11434"`. Operators who genuinely need public access should add an explicit interface binding or put a reverse proxy in front.  
**Status:** ⏳ Deferred — behavior-changing for multi-host setups. Needs operator sign-off on networking topology.

---

#### L-3 — pip CVEs in development/runtime environment (DEFERRED)

**Tool:** `pip-audit`  
**Package:** `pip 24.0`  
**CVEs:** PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-196, PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-3721  
**Fix version:** `pip >= 26.1.2`  
**Package:** `setuptools 79.0.1`  
**CVEs:** PYSEC-2026-3447, GHSA-xmj3-9gf7-h52w  
**Fix version:** `setuptools >= 84.1.0`  
**Risk:** These are build-time/installer tools. The Dockerfile installs application dependencies using the bundled pip; if any pip CVE is exploitable during the Docker build step, it could compromise the image. setuptools GHSA-xmj3-9gf7-h52w is a path traversal in package metadata handling.  
**Proposed design:** Pin `pip>=26.1.2 setuptools>=84.1.0` in `pyproject.toml` build requirements, or add `RUN pip install --upgrade pip setuptools` early in the Dockerfile.  
**Status:** ⏳ Deferred — requires verifying compatibility with the application's existing dependency set.

---

#### L-4 — Semantic SQL construction via f-strings (NOTE — not flagged, pattern is safe)

**File/location:** `app/teams.py:218`, `app/telemetry.py:268,275`, `app/main.py` multiple admin handlers  
**Assessment:** These use f-strings to interpolate a `WHERE` clause or `SET` column list, but all column names and clause fragments are hard-coded from Python string literals (not user-controlled). All user-supplied values are bound through SQLite `?` placeholders in the `params` tuple. No injection risk exists in the current code, but the pattern warrants a note: future contributors should not add user input into the clause-building logic without review.  
**Status:** ✅ No action required; documented for contributor awareness.

---

## Minimum Sweep Checklist

| Check | Result |
|-------|--------|
| (a) Every `@app.get/post/delete` route calls an auth guard or has a documented open reason | ✅ All routes audited. Open routes: `/v1/models` (model listing, no PII), `/health` (health check), `/feedback` (documented intentional), `/chat` (login page), `/chat/login`, `/chat/logout`, `/metrics` (flagged as M-2). |
| (b) `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, `yaml.load` | ✅ None found in `app/`. All subprocess calls use `asyncio.create_subprocess_exec` (no shell). |
| (c) SQL parameterization | ✅ All user-supplied values bound via `?` placeholders. f-string clause construction is safe (see L-4). |
| (d) No tracked secrets (env/key/pem files, live key patterns) | ✅ No `.env`, `.key`, `.pem` files tracked. No `sk-`, `AKIA`, `ghp_` patterns in tracked files. |
| (e) Secret comparison uses `secrets.compare_digest`; token generation uses `secrets` module | ✅ Fixed (M-1). Team tokens generated with `secrets.token_hex`. |
| (f) Dockerfile non-root USER; docker-compose doesn't expose unauthenticated services | ⚠️ Dockerfile fixed (L-1). docker-compose binding deferred (L-2). |
| (g) pip-audit CVEs | ⚠️ 15 CVEs in `pip` and `setuptools` (L-3). No CVEs in application packages. |

---

## Positive Observations

- **Team token generation** uses `secrets.token_hex(16)` (cryptographically secure). Token format `cv_team_<hex>` is consistent and validated before DB lookup.
- **Token comparison (chat login path)** is an exact DB lookup — the token is the primary key — which is not vulnerable to timing attacks the way a string comparison would be.
- **No `shell=True`** anywhere; all subprocess calls go through `asyncio.create_subprocess_exec` with pre-tokenised argument lists.
- **SQLite queries** are consistently parameterised; no raw user input is embedded in query text.
- **No tracked secrets** found in the repository.
- **Admin endpoints** are comprehensively gated by `_admin_auth`; chat endpoints by `_require_chat_team`.
- **Cookie security**: `cv_session` is set `httponly=True, samesite="lax"` — mitigates XSS-based cookie theft and CSRF.
- **Jinja2 templating** auto-escapes HTML by default, mitigating stored-XSS from user-controlled data rendered in admin templates.

---

## Remediation Status

| ID  | Description                               | Status     |
|-----|-------------------------------------------|------------|
| M-1 | Non-constant-time admin token compare     | ✅ Fixed    |
| M-2 | `/metrics` unauthenticated                | ⏳ Deferred |
| L-1 | Dockerfile runs as root                   | ✅ Fixed    |
| L-2 | docker-compose all-interface binding      | ⏳ Deferred |
| L-3 | pip/setuptools CVEs                       | ⏳ Deferred |

*Report generated by automated daily security audit. No `.env` contents or token values included.*
