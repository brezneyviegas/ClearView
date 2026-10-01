# Security Audit — 2026-10-01

**Repository:** ClearView (Python/FastAPI LLM gateway)
**Auditor:** Automated daily security audit
**Previous audit:** None (first run)
**Deferred items carried over:** None

---

## Severity Summary

| Severity | New | Fixed This Run | Deferred |
|----------|-----|---------------|----------|
| High     | 2   | 2             | 0        |
| Medium   | 3   | 0             | 3        |
| Low      | 3   | 0             | 3        |

---

## High Findings

### H-1 — Non-constant-time admin token comparison  ✅ FIXED
**File:** `app/main.py:300` (`_admin_auth`)  
**Risk:** The check `if token != expected` is Python string equality, which short-circuits on the first differing byte. An attacker with many requests to `/admin/*` endpoints could use a remote timing oracle to enumerate the `CLEARVIEW_ADMIN_TOKEN` value one byte at a time, bypassing bearer auth without knowing the token.  
**Remediation applied:** Replaced with `secrets.compare_digest(token, expected)`, which runs in constant time regardless of where strings differ.

### H-2 — Dockerfile runs container as root  ✅ FIXED
**File:** `Dockerfile`  
**Risk:** No `USER` directive meant the process ran as UID 0 inside the container. A container escape or a code-execution bug grants immediate root on the host namespace (when privileged) or maximum damage inside the container.  
**Remediation applied:** Added `RUN useradd -r … clearview` and `USER clearview` before the `CMD`. The `/data` volume directory is pre-created and `chown`-ed so the non-root process can write the SQLite database.

---

## Medium Findings

### M-1 — Non-constant-time client-key comparison  ✅ FIXED
**File:** `app/main.py:325` (`_client_key_allowed`)  
**Risk:** `if cand in allowed` is a set lookup backed by `__hash__`+`__eq__`. Python's string `__eq__` is not constant-time, so a timing attack against `CLEARVIEW_CLIENT_KEYS`-protected deployments is theoretically possible. Severity is lower than H-1 because client keys are intended to be shared (not secret) dummy values in the described use-case, but operators who use them as real secrets are exposed.  
**Remediation applied:** Replaced with `any(secrets.compare_digest(cand, k) for k in allowed)`.

### M-2 — `/metrics` endpoint is unauthenticated  ⚠️ DEFERRED
**File:** `app/main.py:2538`  
**Risk:** The Prometheus `/metrics` endpoint returns request counts broken down by tier, provider, team_id, and status, plus native cost totals and token counts. An unauthenticated caller can enumerate which AI providers and tiers are active, approximate traffic volume, and infer team identifiers. No admin token gate is applied.  
**Note:** Prometheus convention is to scrape metrics without a bearer token; the fix (adding an optional `CLEARVIEW_METRICS_TOKEN` env-var gate) is a non-trivial behaviour change and should be discussed with the operator.  
**Proposed design:** Add an optional env var `CLEARVIEW_METRICS_TOKEN`; when set, apply the same `_admin_auth`-style check on `/metrics`. Document in README.

### M-3 — Vulnerable pip / setuptools in build environment  ⚠️ DEFERRED
**Tool:** `pip-audit` (run against installed packages)  
**Findings:**
| Package    | Version | CVE IDs                                                     | Fix Version |
|------------|---------|-------------------------------------------------------------|-------------|
| pip        | 24.0    | PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-196, PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-3721 | ≥ 26.2      |
| setuptools | 79.0.1  | PYSEC-2026-3447                                             | ≥ 83.0.0   |

**Risk:** `pip` and `setuptools` are build-time tools bundled in the `python:3.12-slim` base image. The CVEs affect package installation operations (not the running gateway server). In a CI/CD pipeline that re-builds the image, a malicious package index response could exploit these. The deployed runtime is unaffected unless the container runs `pip install` at startup.  
**Proposed design:** Pin `pip>=26.2` and `setuptools>=83.0.0` in the Dockerfile via `RUN pip install --upgrade pip setuptools` after copying deps; or switch to `python:3.12-slim` with a pinned digest that ships a patched pip.

---

## Low Findings

### L-1 — `cv_session` cookie missing `Secure` flag  ⚠️ DEFERRED
**File:** `app/main.py:2651` (`chat_login`)  
**Risk:** The session cookie is set with `httponly=True, samesite="lax"` but no `secure=True`. Without `Secure`, browsers will send the cookie over plain HTTP connections. On a network where HTTP and HTTPS are both available, this enables session-token interception.  
**Proposed design:** Add `secure=True` when an env var `CLEARVIEW_HTTPS=1` (or `CLEARVIEW_HOST` starts with `https`) is set. Leave unset for local HTTP development. A simpler option: always set `Secure` and document that HTTPS is required for the `/chat` UI in production.

### L-2 — `docker-compose.yml` binds port to all network interfaces  ⚠️ DEFERRED
**File:** `docker-compose.yml:8`  
**Risk:** `"8000:8000"` binds to `0.0.0.0`, exposing the gateway on every interface of the Docker host. On a multi-tenant server, other users or network peers can reach the admin endpoints (when `CLEARVIEW_ADMIN_TOKEN` is unset) or the LLM API.  
**Proposed design:** Change to `"127.0.0.1:8000:8000"` in the Compose file, or document that operators must set `CLEARVIEW_ADMIN_TOKEN` and `CLEARVIEW_CLIENT_KEYS` before removing the loopback bind.

### L-3 — `/v1/models` endpoint is unauthenticated  ⚠️ DEFERRED
**File:** `app/main.py:364`  
**Risk:** Returns the full list of configured model IDs and their tiers without any auth check. An external caller can enumerate which AI providers are connected. Low impact on its own, but helps with reconnaissance.  
**Proposed design:** Add an optional `_client_key_allowed(request)` call, matching the `/v1/chat/completions` auth policy.

---

## Positive Observations

- **Parameterized SQL throughout:** All SQLite queries in `telemetry.py`, `teams.py`, and `main.py` use `?` placeholders; no f-string user-value interpolation in SQL column data positions found. The `teams.py:218` f-string builds only the column-name part of an UPDATE from a validated internal list, not from user input.
- **Secure token generation:** Team tokens are minted with `secrets.token_hex(16)` (128-bit entropy) — appropriate for bearer credentials.
- **No shell=True:** All subprocess calls in `claude_cli.py`, `codex_cli.py`, and `gemini_cli.py` use `create_subprocess_exec` / `subprocess.run` with a list, eliminating shell injection.
- **No dangerous builtins:** No `eval()`, `exec()`, `pickle.loads`, or unsafe `yaml.load` calls found anywhere in `app/`.
- **No tracked secrets:** `git ls-files` shows no `.env`, `.pem`, or `.key` files committed. No live API keys (sk-, AKIA-, ghp_) detected in tracked files.
- **Admin endpoints consistently gated:** All `/admin/*` routes call `_admin_auth(request)` as the first statement.
- **Chat endpoints gated:** All `/chat/*` routes call `_require_chat_team(request)`, which delegates to `_resolve_team`.
- **Token format prefix guards:** Team token lookup is only attempted when the bearer starts with `cv_team_`, preventing the team DB query from running on arbitrary strings.
- **HttpOnly session cookie:** The `cv_session` cookie sets `httponly=True`, preventing JavaScript access.
- **Error messages don't distinguish cases:** `"unknown or disabled team"` merges both failure paths to avoid oracle attacks on team existence.

---

## Remediation Status

| ID  | Finding                                       | Status   | Commit |
|-----|-----------------------------------------------|----------|--------|
| H-1 | Non-constant-time admin token compare         | Fixed    | this PR |
| H-2 | Dockerfile runs as root                       | Fixed    | this PR |
| M-1 | Non-constant-time client-key compare          | Fixed    | this PR |
| M-2 | Unauthenticated /metrics endpoint             | Deferred | needs design |
| M-3 | Vulnerable pip/setuptools in build image      | Deferred | Dockerfile pin |
| L-1 | cv_session cookie missing Secure flag         | Deferred | needs HTTPS env var design |
| L-2 | docker-compose binds to all interfaces        | Deferred | operator doc + change |
| L-3 | /v1/models unauthenticated                    | Deferred | optional gate |
