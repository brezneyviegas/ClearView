# Security Audit — 2026-10-12

**Auditor:** Automated daily security audit (ClearView)
**Branch audited:** `main` (HEAD: e8ad2e3)
**Remediation branch:** `security/audit-2026-10-12`
**Test result:** 359/359 passed (baseline pre-fix run)

---

## Severity Summary

| Severity | New | Fixed this run | Deferred |
|----------|-----|----------------|----------|
| High     | 1   | 1              | 0        |
| Medium   | 3   | 3              | 0        |
| Low      | 2   | 0              | 2        |

---

## High Findings

### H1 — Dockerfile runs as root
**File/Location:** `Dockerfile`
**Risk:** The container image had no `USER` directive; the app process ran as UID 0. A successful app-layer exploit (e.g. path traversal, deserialization) would grant full root access inside the container, trivially escaping most namespace-only sandboxes.
**Remediation:** Added `RUN groupadd -r clearview && useradd -r -g clearview clearview && mkdir -p /data && chown clearview:clearview /data` and `USER clearview` before the `CMD`.
**Status: FIXED** (this run)

---

## Medium Findings

### M1 — Non-constant-time admin token comparison
**File/Location:** `app/main.py:301` — `_admin_auth()`
**Risk:** The check `if token != expected` compares strings character by character and short-circuits on the first mismatch, allowing a timing oracle. A local-network attacker making many requests can enumerate valid `CLEARVIEW_ADMIN_TOKEN` characters one at a time.
**Remediation:** Replaced with `secrets.compare_digest(token, expected)`, which runs in constant time regardless of match position. Added `import secrets` to the module imports.
**Status: FIXED** (this run)

### M2 — `/metrics` endpoint unauthenticated
**File/Location:** `app/main.py:2538` — `GET /metrics`
**Risk:** The Prometheus scrape endpoint returned internal telemetry (per-team costs, model usage, provider breakdown, drift percentages, token counts) to any unauthenticated caller with network access. This leaks operational and billing data.
**Remediation:** Added `_admin_auth(request)` as the first call inside `metrics()` and added `request: Request` to its signature. Protected by `CLEARVIEW_ADMIN_TOKEN` when set; open in dev mode (no token configured), matching the rest of the admin surface.
**Status: FIXED** (this run)

### M3 — docker-compose publishes port 8000 on all interfaces
**File/Location:** `docker-compose.yml:8` — `ports: "8000:8000"`
**Risk:** In any Docker host with a routable IP, `"8000:8000"` binds on `0.0.0.0`, making the gateway reachable by anyone on the same network segment without operator intent. This is particularly risky on cloud VMs where the host interface is public.
**Remediation:** Changed to `"127.0.0.1:8000:8000"` so the gateway listens only on the host loopback. Operators who need external access should place a reverse proxy (nginx, Caddy) in front and configure TLS there.
**Status: FIXED** (this run)

---

## Low Findings

### L1 — pip and setuptools CVEs in build tooling
**Tool:** `pip-audit`
**Packages affected:**
- `pip 24.0`: 10 CVEs (PYSEC-2026-1795, -1796, -196, -2875, -2876, -3721); fix → `pip ≥ 26.2.1`
- `setuptools 79.0.1`: 1 CVE (PYSEC-2026-3447); fix → `setuptools ≥ 83.0.0`

**Risk:** These packages are build/install tooling inside the dev venv, not shipped in the production image (the Dockerfile runs `pip install --no-cache-dir .` then does not copy the venv). CVE severity for pip itself is typically supply-chain (malicious packages on PyPI) rather than runtime RCE. Exploitability is low: requires the host developer to install from a malicious source.
**Remediation proposed:** Update venv tooling with `pip install --upgrade pip setuptools` in CI. Not applied this run because these are not runtime app dependencies.
**Status: DEFERRED** — update build tooling in CI; no code change needed in the app.

### L2 — Ollama exposes port 11434 without authentication in docker-compose (profile=local)
**File/Location:** `docker-compose.yml:24` — `ports: "11434:11434"` on the `ollama` service
**Risk:** When operators run `docker compose --profile local up`, the Ollama API (no built-in auth) is bound on `0.0.0.0:11434` of the host. Anyone on the same network can send arbitrary generation requests or enumerate installed models.
**Remediation proposed:** Change to `"127.0.0.1:11434:11434"` for the ollama service, or document that `--profile local` is for single-machine development only and must not be run on shared-network hosts.
**Proposed design:** Add a note in `docker-compose.yml` above the ollama ports line and change to loopback binding. Deferring so operators can review the change (some may intentionally expose Ollama for remote clients).
**Status: DEFERRED** — proposed fix in L2 above; tracked for next run.

---

## Minimum Sweep Checklist

| Check | Result |
|-------|--------|
| All admin routes call `_admin_auth`, `_client_key_allowed`, `_resolve_team`, or `_require_chat_team`, or are documented open | Pass (after M2 fix — `/metrics` now gated) |
| `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, `unsafe yaml.load` | None found |
| SQL parameterized (no f-string / concat with user input) | Pass — all f-string `WHERE` clauses use hardcoded column names; values via `?` params |
| No tracked secrets (`.env`, `.key`, `.pem`, live `sk-`/`AKIA`/`ghp_` keys) | Pass — none found |
| Secret comparisons use `secrets.compare_digest` | Pass (after M1 fix) |
| Token generation uses `secrets` module | Pass — `teams.py:117` and `chat.py:62` use `secrets.token_hex()` |
| Dockerfile has non-root `USER` | Pass (after H1 fix) |
| docker-compose does not publish unauthenticated services beyond loopback | Pass for main `clearview` service (after M3 fix); Ollama deferred as L2 |

---

## Route Authentication Audit

All `@app.get/post/delete` routes verified:

| Route | Auth mechanism | Notes |
|-------|---------------|-------|
| `GET /v1/models` | open | intentional; standard OpenAI discovery endpoint |
| `POST /v1/chat/completions` | `_client_key_allowed` + `_resolve_team` | gateway lock + team identity |
| `POST /v1/messages` | via `_handle_chat_completions` | same as above |
| `POST /v1/responses` | via `_handle_chat_completions` | same as above |
| `POST /v1beta/models/{m}:generateContent` | via `_handle_chat_completions` | same as above |
| `POST /v1/models/{m}:generateContent` | via `_handle_chat_completions` | same as above |
| `POST /v1beta/models/{m}:streamGenerateContent` | via `_handle_chat_completions` | same as above |
| `POST /v1/models/{m}:streamGenerateContent` | via `_handle_chat_completions` | same as above |
| `POST /feedback` | open | intentional; clients rate their own requests by request_id |
| `GET /health` | open | intentional; liveness probe |
| `GET /metrics` | `_admin_auth` | **fixed this run (M2)** |
| All `/admin/*` routes | `_admin_auth` | ✓ |
| `GET /chat` | resolves team but not required | intentional; serves login page to anonymous users |
| `POST /chat/login` | open | intentional; this IS the login endpoint |
| `POST /chat/logout` | open | intentional |
| All `/chat/*` data routes | `_require_chat_team` | ✓ |

---

## Positive Observations

- Team token generation uses `secrets.token_hex(16)` (128 bits entropy) — strong.
- Conversation IDs use `secrets.token_hex(8)` — adequate for non-guessable IDs.
- Cookie-based auth is `HttpOnly=True, SameSite=Lax` — correctly mitigates XSS cookie theft.
- SQL throughout uses parameterized queries; the dynamic `WHERE`/`SET` clause builders use only hardcoded column name strings, never user-supplied values.
- No `shell=True` subprocess calls found; CLI providers use `asyncio.create_subprocess_exec` with argument arrays.
- No tracked `.env`, `.key`, or `.pem` files; no live API keys found in tracked source.
- Budget enforcement logic uses floating-point comparison with 5s TTL caching — soft cap with documented limitation in source comments.

---

*Report generated automatically. No `.env` contents or database token values are included.*
