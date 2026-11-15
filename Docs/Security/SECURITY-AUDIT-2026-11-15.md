# Security Audit — 2026-11-15

**Repository:** ClearView (Python/FastAPI LLM gateway)
**Auditor:** Automated daily audit (Claude Code scheduled routine)
**Branch audited:** `main` @ e8ad2e3
**Previous report:** None (first audit)

---

## Severity Summary

| Severity | New | Fixed this run | Deferred |
|----------|-----|---------------|----------|
| High     | 1   | 1             | 0        |
| Medium   | 3   | 2             | 1        |
| Low      | 2   | 0             | 2        |
| **Total**| **6** | **3**       | **3**    |

---

## High Findings

### H-1 — Non-constant-time admin token comparison [FIXED]

**File/location:** `app/main.py:300` (`_admin_auth`)
**Risk:** Python's `!=` operator on strings short-circuits at the first differing byte. An attacker who can make many requests with microsecond timing precision can determine the admin token character-by-character (timing side-channel attack), eventually reconstructing the secret without brute-force.
**Remediation:** Replaced `token != expected` with `not secrets.compare_digest(token, expected)`. Fixed in this run.

---

## Medium Findings

### M-1 — Non-constant-time client key comparison [FIXED]

**File/location:** `app/main.py:318` (`_client_key_allowed`)
**Risk:** `cand in allowed` performs a hash-set lookup, which may reveal key membership via cache-timing. Same class of attack as H-1 for client keys.
**Remediation:** Replaced `cand in allowed` with `any(secrets.compare_digest(cand, k) for k in allowed)`. Fixed in this run.

### M-2 — Dockerfile runs as root [FIXED]

**File/location:** `Dockerfile` (no `USER` directive)
**Risk:** If any dependency or the app itself has a RCE vulnerability, the attacker gains root access to the container filesystem. Violates least-privilege principle.
**Remediation:** Added `useradd --system --no-create-home appuser`, pre-chowned `/data` volume mount point, and `USER appuser` before `CMD`. Fixed in this run.

### M-3 — `/metrics` endpoint is unauthenticated [DEFERRED]

**File/location:** `app/main.py:2538` (`metrics` route)
**Risk:** The Prometheus `/metrics` endpoint exposes team IDs (in label sets), aggregate request counts, cumulative cost data, and drift percentages to any unauthenticated caller on the network. This leaks operational metadata.
**Proposed design:** Add `_admin_auth(request)` gating, plus a note in `README.md` that Prometheus scrape jobs must configure a `Bearer` token header when `CLEARVIEW_ADMIN_TOKEN` is set. Alternatively, expose `/metrics` only on a separate internal-only port (a Starlette `Mount` on a second `uvicorn` worker bound to `127.0.0.1`). Deferred — requires Prometheus configuration coordination with operators.

---

## Low Findings

### L-1 — pip/setuptools CVEs in runtime container [DEFERRED]

**Tool:** `pip-audit`
**Packages affected:**
- `pip 24.0` → 15 CVEs (fix: 25.3 / 26.0 / 26.1 / 26.2 depending on CVE); PYSEC-2026-1795, 1796, 196, 2875, 2876, 3721
- `setuptools 79.0.1` → 2 CVEs (fix: 83.0.0 / 84.1.0); PYSEC-2026-3447, GHSA-xmj3-9gf7-h52w
**Risk:** These are tooling packages inside the runtime Docker image (from `python:3.12-slim`). Exploitability depends on whether `pip install` is ever run at container runtime; in the current Dockerfile it is not (all installs happen at build time). Impact is Low for this deployment model but should be tracked for base-image upgrades.
**Proposed fix:** Upgrade base image to `python:3.12` latest (or pin a newer slim tag) and add `pip install --upgrade pip setuptools` during build. Deferred to a base-image upgrade PR.

### L-2 — docker-compose publishes port 8000 to all interfaces [DEFERRED]

**File/location:** `docker-compose.yml:8` (`ports: "8000:8000"`)
**Risk:** Without `CLEARVIEW_ADMIN_TOKEN` set (which the compose file doesn't enforce), the admin and gateway surfaces are reachable from any host on the Docker network's external interface. On a developer workstation this is acceptable; on a shared or cloud host it is not.
**Proposed fix:** Change port binding to `"127.0.0.1:8000:8000"` for the default `clearview` service, or document that `CLEARVIEW_ADMIN_TOKEN` must be set before exposing to a network. Deferred — behavior-changing for existing deployments.

---

## Deferred Items Carried Forward

| ID  | Summary                                      | First seen  |
|-----|----------------------------------------------|-------------|
| M-3 | `/metrics` unauthenticated                   | 2026-11-15  |
| L-1 | pip/setuptools CVEs in runtime container     | 2026-11-15  |
| L-2 | docker-compose publishes to all interfaces   | 2026-11-15  |

---

## Positive Observations

- **All admin routes** (`/admin/*`, `/admin/teams`) are gated by `_admin_auth`; no admin route found without it.
- **All chat routes** (`/chat/conversations/*`, `/chat/me`) are gated by `_require_chat_team` or `_resolve_team`.
- **Intentionally open routes** are documented: `/v1/models` (OpenAI model listing compatibility), `/health` (liveness probe), `/feedback` (client-facing rating).
- **SQL queries are fully parameterized**: WHERE clauses in all inline SQLite queries use `?` placeholders; column/table names are code-literals not user input. No f-string or string-concatenation SQL injection path found.
- **Token generation uses `secrets.token_hex`**: `teams.py:117` and `chat.py:62` both use `secrets.token_hex`, correct for cryptographic token generation.
- **No tracked secrets**: `git ls-files` returns no `.env`, `.key`, or `.pem` files. Grep for `sk-`, `AKIA`, `ghp_` patterns found nothing in tracked files.
- **`asyncio.create_subprocess_exec` used in CLI adapters** (not `shell=True`): `claude_cli.py:199,332`, `codex_cli.py:191`, `gemini_cli.py:209` all use `create_subprocess_exec` (argument list, no shell), eliminating shell-injection risk.
- **No `eval(`, `exec(`, `pickle`, or unsafe `yaml.load`** found in `app/`.

---

## Remediation Status

| Finding | Status         | Branch                      |
|---------|----------------|-----------------------------|
| H-1     | Fixed          | `security/audit-2026-11-15` |
| M-1     | Fixed          | `security/audit-2026-11-15` |
| M-2     | Fixed          | `security/audit-2026-11-15` |
| M-3     | Deferred       | —                           |
| L-1     | Deferred       | —                           |
| L-2     | Deferred       | —                           |

---

*Report generated by automated daily security audit. Never includes .env contents or live credential values.*
