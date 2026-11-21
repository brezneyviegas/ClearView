# Security Audit — 2026-11-21

**Auditor:** Automated daily audit (Claude Code)
**Branch audited:** `main` (HEAD `e8ad2e3`)
**Previous report:** None (first audit)

---

## Severity Summary

| Severity | New | Fixed this run | Deferred |
|----------|-----|---------------|----------|
| High     | 1   | 1             | 0        |
| Medium   | 4   | 1             | 3        |
| Low      | 2   | 0             | 2        |

---

## High Findings

### H1 — Non-constant-time admin token comparison [FIXED]

**File:** `app/main.py:301`
**Risk:** Timing side-channel. Using `token != expected` in Python string comparison leaks partial token information through micro-timing differences, allowing an attacker to brute-force `CLEARVIEW_ADMIN_TOKEN` one character at a time when the gateway is network-accessible.
**Remediation:** Replaced with `secrets.compare_digest(token, expected)`. Added `import secrets` to module imports.
**Status:** Fixed in this run on branch `security/audit-2026-11-21`.

---

## Medium Findings

### M1 — Dockerfile runs container as root [FIXED]

**File:** `Dockerfile`
**Risk:** All processes in the container run as `root`. A container escape or RCE vulnerability gives the attacker root-level access to the host (or other containers) rather than a constrained user.
**Remediation:** Added a `clearview` system user and `USER clearview` directive before `CMD`. Data volume and app directory ownership set to this user.
**Status:** Fixed in this run on branch `security/audit-2026-11-21`.

### M2 — `cv_session` cookie missing `secure` flag [DEFERRED]

**File:** `app/main.py:2651-2655`
**Risk:** The 30-day session cookie carrying the team Bearer token (`cv_team_*`) is set without `secure=True`. Over an HTTP connection the token is transmitted in cleartext, allowing interception by a network-local attacker (man-in-the-middle, shared network).
**Remediation proposal:** Add `secure=True` to `resp.set_cookie(...)`. Gate it on a `CLEARVIEW_COOKIE_SECURE` env var (default `True` in production, `False` for local HTTP dev) to avoid breaking developer workflows.
**Deferred:** Requires a config env var or deployment-environment check to avoid breaking local HTTP dev. Not safe to apply mechanically without that design.

### M3 — `/metrics` endpoint unauthenticated [DEFERRED]

**File:** `app/main.py:2538-2570`
**Risk:** The Prometheus `/metrics` endpoint is open with no auth. It exposes `team_id` as a label on `clearview_requests_total`, leaking internal team identifiers to anyone who can reach the gateway. When `CLEARVIEW_ADMIN_TOKEN` is set, all other admin endpoints are gated, making `/metrics` an outlier.
**Remediation proposal:** Add `_admin_auth(request)` to `metrics()`. Supply a separate `CLEARVIEW_METRICS_TOKEN` for Prometheus scrapers so operators can gate metrics without sharing the admin token.
**Deferred:** Would break existing Prometheus scrapers on upgrade. Needs a migration plan or optional env var guard.

### M4 — `pip` 24.0 and `setuptools` 79.0.1 CVEs in build/dev environment

**Packages:** `pip` 24.0, `setuptools` 79.0.1
**CVEs:**
- `pip`: PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-196, PYSEC-2026-3721 → fix: `pip>=26.2`
- `setuptools`: PYSEC-2026-3447, GHSA-xmj3-9gf7-h52w → fix: `setuptools>=84.1.0`

**Risk:** These CVEs affect the build toolchain (`pip install`) and development environments. The pip CVEs relate to URL parsing and malicious package installation; the setuptools CVE relates to arbitrary code execution during editable installs. Runtime impact is limited — the application does not expose pip or setuptools at runtime — but CI/CD pipelines and developer machines are exposed.
**Remediation:** Upgrade pip and setuptools in `pyproject.toml` dev dependencies and in the Dockerfile `RUN pip install` layer.
**Deferred:** Requires testing dependency resolution with updated versions. Recommend tracking in next sprint.

---

## Low Findings

### L1 — `/v1/models` endpoint unauthenticated

**File:** `app/main.py:364-376`
**Risk:** Exposes the internal model/tier configuration (all tier names and underlying model IDs) to unauthenticated callers. This is likely intentional for OpenAI-compatible IDE clients, but leaks internal provider topology on a shared network.
**Note:** Intentionally open by design to support IDE clients that probe `/v1/models` without credentials.
**Deferred:** Document the intentional openness; consider whether to gate it behind `CLEARVIEW_CLIENT_KEYS` in a future hardening pass.

### L2 — Ollama published on all interfaces in `docker-compose.yml`

**File:** `docker-compose.yml` (ollama service, `11434:11434`)
**Risk:** Ollama is bound to `0.0.0.0:11434` (the default when Docker maps ports). On a multi-tenant or cloud host this exposes the Ollama API to the network with no authentication.
**Deferred:** Change to `127.0.0.1:11434:11434` for loopback-only binding. Noted for the next configuration hardening pass.

---

## Minimum Sweep Results

| Check | Result |
|-------|--------|
| All `@app.get/post/delete` routes call `_admin_auth`, `_client_key_allowed`, `_resolve_team`, or `_require_chat_team` — or have documented reason to be open | **Pass** — `/v1/models`, `/health`, `/feedback`, `/metrics`, `/chat` (page), `/chat/login`, `/chat/logout`, and all Gemini compat shims are intentionally open; all others are gated. |
| `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, unsafe `yaml.load` | **Pass** — none found |
| SQL parameterized (no f-string/concat non-whitelisted values) | **Pass** — all user values use `?` placeholders; `{where}` f-strings interpolate only static clause structures built from `?` literals |
| No tracked secrets (`.env`/`.key`/`.pem` files, live `sk-`/`AKIA`/`ghp_` keys) | **Pass** — no tracked secret files; no live API keys found in tracked files |
| Token generation uses `secrets` module | **Pass** — `secrets.token_hex` used for team IDs and conversation IDs |
| Secret comparisons use `secrets.compare_digest` | **Fixed** — was `token != expected`; corrected in this run |
| Dockerfile non-root `USER` | **Fixed** — was running as root; corrected in this run |
| `docker-compose` does not publish unauthenticated services beyond loopback | **Partial** — Ollama exposed on all interfaces (Low finding L2) |

---

## Positive Observations

- Team token generation (`cv_team_*`) correctly uses `secrets.token_hex(16)` (32-hex-char entropy).
- Conversation ID generation uses `secrets.token_hex(8)`.
- All admin endpoints (stats, teams CRUD, explorer, tune, ticker) are consistently gated by `_admin_auth`.
- Budget enforcement uses separate team-daily/team-monthly/global-daily checks.
- Team tokens redacted to prefix in list responses (`_team_to_redacted_dict`).
- SQL queries across all modules use parameterized `?` placeholders exclusively.
- No `shell=True`, `eval`, `exec`, `pickle`, or `yaml.load` calls found in application code.

---

## Deferred Items Carried Forward

| ID | Description | First seen |
|----|-------------|-----------|
| M2 | `cv_session` cookie missing `secure` flag | 2026-11-21 |
| M3 | `/metrics` endpoint unauthenticated | 2026-11-21 |
| M4 | pip/setuptools CVEs in build/dev env | 2026-11-21 |
| L1 | `/v1/models` unauthenticated (intentional) | 2026-11-21 |
| L2 | Ollama published on all interfaces | 2026-11-21 |

---

*Report generated by automated daily audit. No `.env` contents or token values included.*
