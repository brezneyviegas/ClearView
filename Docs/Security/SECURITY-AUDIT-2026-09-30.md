# ClearView Security Audit — 2026-09-30

**Auditor:** Automated daily audit  
**Branch audited:** `main` @ `e8ad2e3`  
**Audit date:** 2026-09-30  
**Previous report:** None (first audit)

---

## Severity Summary

| Severity | New | Fixed this run | Deferred |
|----------|-----|---------------|----------|
| High     | 0   | 0             | 0        |
| Medium   | 3   | 3             | 0        |
| Low      | 3   | 0             | 3        |

---

## Medium Findings

### M-01 — Non-constant-time admin token comparison (FIXED)
**File:** `app/main.py:300` (`_admin_auth`)  
**Risk:** Python's `!=` operator short-circuits on first differing byte, enabling a remote timing oracle that can brute-force `CLEARVIEW_ADMIN_TOKEN` one character at a time. In a production deployment this gives an attacker a path to admin endpoints (`/admin/teams`, `/admin/tune`, `/admin/explorer`, etc.) without a valid token.  
**Remediation:** Replaced `token != expected` with `secrets.compare_digest(token, expected)` in `_admin_auth`.  
**Status:** Fixed in this run. All 359 tests pass.

---

### M-02 — `/metrics` Prometheus endpoint unauthenticated (FIXED)
**File:** `app/main.py:2538` (`metrics`)  
**Risk:** The `/metrics` endpoint returned Prometheus counters including `tier`, `provider`, `status`, and `team_id` labels for every request. An unauthenticated caller could enumerate active team IDs and observe per-provider traffic volumes and error rates, aiding targeted attacks or competitive intelligence. The endpoint is also reachable without `CLEARVIEW_ADMIN_TOKEN` set.  
**Remediation:** Added `_admin_auth(request)` call at the top of the `metrics` handler; the endpoint now obeys the same admin-token gate as all other `/admin/*` routes.  
**Status:** Fixed in this run. No tests cover this endpoint directly; the handler change is structural and correct by inspection.

---

### M-03 — Dockerfile runs container as root (FIXED)
**File:** `Dockerfile`  
**Risk:** No `USER` directive meant the Python process inside the container ran as UID 0 (root). If the application or any dependency is exploited, the attacker has root in the container, which facilitates container-escape and privilege escalation in misconfigured environments.  
**Remediation:** Added `RUN adduser --disabled-password --gecos "" clearview` followed by `USER clearview` before `CMD`. The `/data` volume mount receives a `chown` in the same `RUN` layer.  
**Status:** Fixed in this run. All 359 tests pass.

---

## Low Findings (Deferred)

### L-01 — `/v1/models` endpoint unauthenticated
**File:** `app/main.py:364`  
**Risk:** Lists all configured virtual and underlying model identifiers to any caller. This is standard OpenAI-compatible API behavior and unlikely to be abused on its own, but leaks provider/tier topology. When `CLEARVIEW_CLIENT_KEYS` is set the chat endpoints require a key; `/v1/models` does not.  
**Proposed design:** Guard behind `_client_key_allowed` when `CLEARVIEW_CLIENT_KEYS` is set (same pattern as `_handle_chat_completions`). Behavior-changing: requires IDE clients to authenticate model discovery; defer to operator decision.  
**Status:** Deferred.

---

### L-02 — Ollama publishes port 11434 to all interfaces in docker-compose
**File:** `docker-compose.yml`  
**Risk:** When the `local` profile is active, `ollama` binds `11434:11434` on `0.0.0.0`. An external caller can submit arbitrary inference requests directly to Ollama, bypassing ClearView's auth, budget, and routing controls. Profile-gated, so only affects users who explicitly run `docker compose --profile local up`.  
**Proposed design:** Change to `"127.0.0.1:11434:11434"` to restrict Ollama to loopback. Behavior-changing for users who rely on remote Ollama access; defer.  
**Status:** Deferred.

---

### L-03 — pip-audit: CVEs in build-environment tools
**Packages:** `pip 24.0` (PYSEC-2026-1795/1796/2875/2876/196/3721), `setuptools 79.0.1` (PYSEC-2026-3447)  
**Risk:** These packages are build tools installed in the container image's system Python. They are not imported or executed at runtime. Exploitability requires an attacker to run `pip install` inside the container (which requires prior shell access).  
**Proposed design:** Pin `RUN pip install --upgrade pip setuptools` in the Dockerfile before the application install, or switch to a newer base image. Low urgency; defer to normal dependency-update cycle.  
**Status:** Deferred.

---

## Carried-Over Deferred Items

None (first audit).

---

## Minimum Sweep Results

| Check | Result |
|-------|--------|
| Every `@app.get/post/delete` route calls `_admin_auth`, `_client_key_allowed`, `_resolve_team`, or `_require_chat_team`, or is documented open | Pass (see M-02 for one fixed gap; `/v1/models`, `/feedback`, `/health`, `/chat`, `/chat/login`, `/chat/logout` are intentionally open) |
| `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, `yaml.load(` | None found |
| SQL parameterization — no f-string interpolation of user-controlled values | Pass (f-strings in `teams.py:218` and `telemetry.py:268,275` interpolate only hardcoded column-name fragments; all values are `?`-parameterized) |
| No tracked secrets (env/key/pem files, live key patterns `sk-`, `AKIA`, `ghp_`) | Pass |
| Secret comparisons use `secrets.compare_digest` | Pass (fixed M-01) |
| Token generation uses `secrets` module | Pass (`secrets.token_hex` used in `app/teams.py:117` and `app/chat.py:62`) |
| Dockerfile has non-root USER | Pass (fixed M-03) |
| docker-compose does not publish unauthenticated services beyond loopback (when no profile) | Pass for default profile; L-02 deferred for `local` profile |
| pip-audit | 14 CVEs in build tools only; no runtime CVEs |

---

## Positive Observations

- All `/admin/*` routes consistently gate on `_admin_auth`; the `/metrics` gap was the only exception.
- Team token generation uses `secrets.token_hex(16)` (32 hex chars, 128 bits of entropy) — strong.
- Chat conversation IDs use `secrets.token_hex(8)` — appropriate.
- subprocess usage (`asyncio.create_subprocess_exec`) is shell-injection-safe — no `shell=True`.
- SQL queries throughout use parameterized statements; f-string interpolation is limited to whitelisted column names.
- `CLEARVIEW_ADMIN_TOKEN` mechanism gracefully degrades to open dev mode when unset, clearly documented.
- Team tokens are redacted in listing responses (`_team_to_redacted_dict`) to prevent shoulder-surfing.

---

## Remediation Status

| Finding | Action | Commit |
|---------|--------|--------|
| M-01 Non-constant-time token compare | Fixed | this branch |
| M-02 `/metrics` unauthenticated | Fixed | this branch |
| M-03 Dockerfile runs as root | Fixed | this branch |
| L-01 `/v1/models` unauthenticated | Deferred | — |
| L-02 Ollama port exposed | Deferred | — |
| L-03 pip CVEs in build tools | Deferred | — |
