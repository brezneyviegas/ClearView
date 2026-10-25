# Security Audit — 2026-10-25

**Auditor:** Automated daily security routine  
**Branch audited:** `main` (HEAD `e8ad2e3`)  
**Previous audit:** None (first run — no `Docs/Security/` directory existed)  
**Remediations branch:** `security/audit-2026-10-25`

---

## Severity Summary

| Severity | New | Fixed in this PR | Deferred | Carried over |
|----------|-----|-----------------|----------|--------------|
| High     | 1   | 1               | 0        | 0            |
| Medium   | 4   | 2               | 2        | 0            |
| Low      | 3   | 0               | 3        | 0            |
| **Total**| **8** | **3**         | **5**    | **0**        |

---

## High Findings

### H-01 · Dockerfile runs container as root

| Field | Value |
|-------|-------|
| **File** | `Dockerfile` |
| **Risk** | Container breakout grants full root access to the host. Any RCE in the app process (e.g. via upstream `litellm`, Jinja2, or a provider CLI) immediately runs with UID 0. |
| **Remediation** | Add `RUN useradd -u 1000 -m clearview && chown clearview:clearview /data` and `USER clearview` before `CMD`. |
| **Status** | ✅ **Fixed in this PR** |

---

## Medium Findings

### M-01 · Non-constant-time admin token comparison (timing oracle)

| Field | Value |
|-------|-------|
| **File** | `app/main.py:301` — `_admin_auth()` |
| **Risk** | `token != expected` short-circuits on the first mismatched byte, leaking how many prefix characters of a correct token a probe matched. An attacker with low-latency access (same host or LAN) can reconstruct the `CLEARVIEW_ADMIN_TOKEN` byte-by-byte in O(255 × len) requests. |
| **Remediation** | Replace `if token != expected:` with `if not secrets.compare_digest(token, expected):` — constant-time regardless of match position. |
| **Status** | ✅ **Fixed in this PR** |

### M-02 · `/metrics` endpoint lacks authentication

| Field | Value |
|-------|-------|
| **File** | `app/main.py:2538` — `GET /metrics` |
| **Risk** | The Prometheus scrape endpoint exposes team IDs, provider names, per-model call counts, and spend totals without requiring a bearer token — even when `CLEARVIEW_ADMIN_TOKEN` is set and all other admin routes are gated. An unauthenticated caller on the same network can enumerate active teams and observe routing behaviour. |
| **Remediation (proposed)** | Call `_admin_auth(request)` inside `async def metrics()`, or add a separate `CLEARVIEW_METRICS_TOKEN` env var to allow scraper-specific credentials. The latter is the standard pattern for Prometheus pull scrapers that cannot send Bearer headers natively. |
| **Status** | ⏳ **Deferred** — fixing requires a design decision (auth strategy for Prometheus scraper vs. admin UI token). No behavior-changing fixes applied without explicit sign-off. |

### M-03 · filelock CVEs (3 vulnerabilities) — transitive dependency

| Field | Value |
|-------|-------|
| **Package** | `filelock` (via `litellm`) |
| **CVEs** | `CVE-2026-112940`, `CVE-2026-112913`, `CVE-2026-112907` |
| **Fix version** | `filelock>=4.2.1` |
| **Risk** | filelock is used by litellm for cache/model-file locking. The CVEs cover improper locking behaviour that can lead to race conditions and data corruption in adversarial multi-process scenarios. |
| **Remediation** | Pin `filelock>=4.2.1` as a direct dependency floor in `pyproject.toml` to force pip to resolve the patched version. |
| **Status** | ✅ **Fixed in this PR** — `filelock>=4.2.1` added to `pyproject.toml` dependencies. |

### M-04 · Cookie `cv_session` missing `Secure` flag

| Field | Value |
|-------|-------|
| **File** | `app/main.py:2651` — `chat_login()` |
| **Risk** | The session token is set with `httponly=True, samesite="lax"` but NOT `secure=True`. If any HTTP (non-TLS) path to the server exists, a network-adjacent attacker can capture the `cv_team_*` Bearer token via a passive network tap or a MITM redirect. |
| **Remediation (proposed)** | Add `secure=True` to `resp.set_cookie(...)`. This can be made conditional on a `CLEARVIEW_SECURE_COOKIES=1` env var to preserve local dev (plain HTTP) without breaking production. |
| **Status** | ⏳ **Deferred** — the correct value of `secure=` depends on deployment topology (TLS termination, reverse proxy, localhost dev). Adding it unconditionally would break local dev. Propose adding a `CLEARVIEW_SECURE_COOKIES` toggle defaulting to `0`. |

---

## Low Findings

### L-01 · pip and setuptools CVEs in venv build tooling

| Field | Value |
|-------|-------|
| **Packages** | `pip 24.0` (6 CVEs, fix `>=26.2`), `setuptools 79.0.1` (3 CVEs, fix `>=84.1.0`) |
| **Risk** | These are build/install-time tools; `pip` and `setuptools` are not imported by the app at runtime. Vulnerabilities in them do not affect a running container unless the container re-runs `pip install`. Docker multi-stage builds that strip pip from the final image are not affected at all. |
| **Remediation** | Upgrade pip in CI/dev environments: `pip install --upgrade pip setuptools`. Low runtime priority. |
| **Status** | ⏳ **Deferred** — dev-tool only, zero runtime exposure in production containers. |

### L-02 · Ollama port `11434` published to all interfaces when using `--profile local`

| Field | Value |
|-------|-------|
| **File** | `docker-compose.yml` — `ollama` service |
| **Risk** | When running `docker compose --profile local up`, Ollama's inference API is published as `0.0.0.0:11434`, reachable from the host network. Any process on the same LAN or VM host can submit inference jobs directly to Ollama, bypassing ClearView's auth and budget enforcement. |
| **Remediation (proposed)** | Change Ollama port mapping to `127.0.0.1:11434:11434` to restrict to loopback. ClearView communicates with Ollama via the internal Docker network (not the host), so this change does not break the gateway. |
| **Status** | ⏳ **Deferred** — low risk on typical single-machine dev setups; included here for awareness. |

### L-03 · `GET /v1/models` and `POST /feedback` are unauthenticated

| Field | Value |
|-------|-------|
| **File** | `app/main.py:364`, `app/main.py:1693` |
| **Risk** | `/v1/models` is intentionally open — standard for OpenAI-compatible APIs (model enumeration is not sensitive). `/feedback` accepts ratings keyed by `request_id` only; callers cannot enumerate others' responses. Both endpoints follow standard LLM gateway conventions. |
| **Status** | ℹ️ **Accepted** — documented as intentionally open per design. No finding action needed. |

---

## Audit Checklist Results

| Check | Result |
|-------|--------|
| Auth coverage: every `@app.*` route in `main.py` | Pass (all admin routes gated; `/metrics` deferred M-02; `/v1/models`, `/feedback`, `/health`, `/chat/login`, `/chat/logout` intentionally open) |
| Injection: `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, `yaml.load(` | ✅ Pass — `subprocess.create_subprocess_exec` used throughout (no shell=True) |
| SQL parameterization | ✅ Pass — all queries use `?` placeholders; `WHERE` clause built from hardcoded strings only |
| Tracked secrets (`git ls-files` for env/key/pem) | ✅ Pass — only `.env.example` and `clearview-client.env.example` tracked (examples, no live values) |
| Live key patterns in tracked files (`sk-`, `AKIA`, `ghp_`) | ✅ Pass — only test fixtures in `tests/` using placeholder `sk-x` / `sk-test` |
| Token comparison uses `secrets.compare_digest` | ⚠️ Finding M-01 — fixed in this PR |
| Token generation uses `secrets` module | ✅ Pass — `teams.py:117` uses `secrets.token_hex(16)`; `chat.py:62` uses `secrets.token_hex(8)` |
| Dockerfile has non-root `USER` | ⚠️ Finding H-01 — fixed in this PR |
| docker-compose does not publish unauthenticated services beyond loopback | ⚠️ Finding L-02 — deferred |
| `pip-audit` CVEs | ⚠️ 18 issues found: filelock (M-03, fixed), pip/setuptools (L-01, deferred) |

---

## Positive Observations

- **Parameterized SQL throughout**: Every admin SQL query in `main.py` constructs `WHERE` clauses from hardcoded strings and passes user values as tuple parameters. No f-string interpolation of user-controlled values into query text.
- **Team token generation**: `cv_team_` tokens use `secrets.token_hex(16)` (128-bit entropy). Conversation IDs use `secrets.token_hex(8)` (64-bit). Both are cryptographically secure.
- **No tracked secrets**: `.env.example` and `clearview-client.env.example` contain only placeholder values. No `.env`, PEM, or key files are tracked.
- **No dangerous subprocess usage**: CLI provider adapters use `asyncio.create_subprocess_exec` (never `shell=True`), passing arguments as a list. No shell injection vector.
- **HttpOnly + SameSite on session cookie**: The `cv_session` cookie is set with `httponly=True` (XSS protection) and `samesite="lax"` (CSRF mitigation). The missing `Secure` flag is noted as M-04.
- **Admin token dev-mode awareness**: `_admin_auth` explicitly allows unauthenticated access when `CLEARVIEW_ADMIN_TOKEN` is not set — the dev-open behaviour is intentional and documented in the code.

---

## Remediation Status

| ID | Description | Status |
|----|-------------|--------|
| H-01 | Dockerfile non-root USER | ✅ Fixed (commit in this PR) |
| M-01 | `secrets.compare_digest` for admin token | ✅ Fixed (commit in this PR) |
| M-02 | `/metrics` auth gate | ⏳ Deferred — needs design decision on scraper-compatible auth |
| M-03 | filelock CVEs | ✅ Fixed — `filelock>=4.2.1` added to `pyproject.toml` |
| M-04 | Cookie `Secure` flag | ⏳ Deferred — needs `CLEARVIEW_SECURE_COOKIES` toggle design |
| L-01 | pip/setuptools CVEs in dev tooling | ⏳ Deferred — dev-tool only |
| L-02 | Ollama port all-interface publish | ⏳ Deferred — dev scenario, loopback bind recommended |
| L-03 | `/v1/models` and `/feedback` open | ℹ️ Accepted by design |
