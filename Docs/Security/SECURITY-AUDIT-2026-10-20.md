# ClearView Security Audit — 2026-10-20

**Auditor:** Claude Code (automated daily run)
**Branch audited:** `main` (HEAD `e8ad2e3`)
**Report branch:** `security/audit-2026-10-20`
**Prior reports:** none (first audit)
**Test result:** 359 passed, 0 failed

---

## Severity Summary

| Severity | New | Fixed in this run | Deferred |
|----------|-----|-------------------|----------|
| High     | 1   | 1                 | 0        |
| Medium   | 3   | 2                 | 1        |
| Low      | 2   | 0                 | 2        |

---

## Findings

### HIGH

#### H-1 — Dockerfile runs container as root (FIXED)
**File:** `Dockerfile`
**Risk:** The image had no `USER` directive, so the application process ran as root (uid 0) inside the container. A container escape or RCE vulnerability in a dependency would grant an attacker full root access to the container filesystem and, depending on host configuration, potential host breakout.
**Remediation applied:** Added `RUN mkdir -p /data && chown nobody:nogroup /data` then `USER nobody` before `CMD`. The `/data` volume mount is pre-created with correct ownership so SQLite writes succeed under the `nobody` user.

---

### MEDIUM

#### M-1 — Non-constant-time admin token comparison (FIXED)
**File:** `app/main.py:302` (`_admin_auth`)
**Risk:** `token != expected` uses Python's built-in string equality, which can leak timing information about the token. An attacker who can make many requests to an admin endpoint (e.g. `/admin/stats`) and measure response latencies could, in principle, recover the admin token character-by-character via a timing oracle attack. Practical difficulty is high over a network but non-zero on a LAN or cloud co-location.
**Remediation applied:** Replaced `if token != expected` with `if not secrets.compare_digest(token, expected)`. Added `import secrets` to the module imports.

#### M-2 — Session cookie missing Secure flag (FIXED)
**File:** `app/main.py:2651` (`chat_login`)
**Risk:** The `cv_session` cookie carrying the team bearer token was set with `httponly=True, samesite="lax"` but without `secure=True`. If the server is ever accessed over plain HTTP (e.g. behind a misconfigured reverse proxy, or during local testing over a network), the browser would transmit the team token in cleartext.
**Remediation applied:** The `secure` flag is now controlled by the `CLEARVIEW_SECURE_COOKIES=1` environment variable (default off to preserve local-dev HTTP behaviour). Operators deploying behind HTTPS should set this variable.

#### M-3 — Build-toolchain CVEs in pip and setuptools (DEFERRED — design decision)
**Tool:** `pip-audit` output
**Details:**
- `pip 24.0`: PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-196, PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-3721 → fix: upgrade to `pip >= 26.2.1`
- `setuptools 79.0.1`: PYSEC-2026-3447 → fix: upgrade to `setuptools >= 83.0.0`
**Risk:** These affect the Python package installer used at image build time (`pip install` in the Dockerfile). Exploitation vectors include maliciously crafted packages during install. Not runtime vulnerabilities — the application itself does not call `pip` at runtime.
**Proposed design:** Pin `pip` and `setuptools` to safe versions in the Dockerfile (`pip install --upgrade pip==26.2.1 setuptools==83.0.0` before the project install step). Defer to operator: no auto-fix applied since this may require vetting the upgraded toolchain against the CI matrix.

---

### LOW

#### L-1 — `/metrics` endpoint open without authentication
**File:** `app/main.py:2538`
**Risk:** The Prometheus `/metrics` endpoint is unauthenticated. It exposes per-tier call counts, cost totals, token counts, and (via label cardinality) team_id values — not secrets themselves, but operational intelligence a competitor or attacker could use to infer traffic patterns, team activity, and billing scale.
**Note:** Public `/metrics` is conventional for Prometheus scraping (scraper auth is typically handled at the network layer). Flagged for operator awareness; no code change applied.
**Recommended mitigation (deferred):** Optionally gate via `_admin_auth` behind a feature flag, or restrict at the ingress/network layer.

#### L-2 — `/v1/models` exposes configured provider list without authentication
**File:** `app/main.py:364`
**Risk:** `GET /v1/models` is intentionally open (required for OpenAI-compatible clients to enumerate models). It reveals which LLM providers and model tiers are configured. Low impact in practice — this is standard OpenAI API behaviour — but worth documenting.
**Note:** Deliberately open; no change applied.

---

## Minimum Sweep Checklist

| Check | Result |
|-------|--------|
| Every `@app.{get,post,delete}` route calls `_admin_auth`, `_client_key_allowed`, `_resolve_team`, or `_require_chat_team`, or has a documented reason to be open | PASS — all admin routes call `_admin_auth`; API routes call `_client_key_allowed` + `_resolve_team`; chat routes call `_require_chat_team`; `/v1/models`, `/health`, `/feedback`, `/metrics` are intentionally open |
| `shell=True` / `os.system` / `eval(` / `exec(` / `pickle` / unsafe `yaml.load` | PASS — none found. Subprocess calls use `create_subprocess_exec` (no shell) |
| SQL parameterization — no f-string interpolation of user values | PASS — all WHERE clause values use `?` placeholders; f-strings construct only the static clause skeleton from whitelisted column names |
| No tracked secrets (`.env`, `.key`, `.pem` files; live `sk-`, `AKIA`, `ghp_` keys) | PASS — no such files or key patterns found in tracked files |
| Secret comparisons use `secrets.compare_digest` | FIXED — `_admin_auth` now uses `secrets.compare_digest`; team token lookup uses exact DB match (not a comparison) |
| Token generation uses `secrets` module | PASS — `teams.py` uses `secrets.token_hex(16)`, `chat.py` uses `secrets.token_hex(8)` |
| Dockerfile has non-root `USER` | FIXED — `USER nobody` added |
| `docker-compose` does not publish unauthenticated services beyond loopback | NOTE — port 8000 is published to all interfaces (`8000:8000`). This is intentional for a gateway product; auth is enforced at the application layer. The ollama service is in a `local` profile and only exposes port 11434 locally. |
| `pip-audit` CVEs | See M-3 above — only build-toolchain packages affected |

---

## Positive Observations

- All admin API routes consistently call `_admin_auth` before performing any work.
- Team bearer tokens are generated with `secrets.token_hex(16)` (128 bits of entropy), exceeding NIST SP 800-63B minimums.
- The admin team listing (`/admin/teams`) correctly redacts the full token to an 18-char prefix, preventing token leakage in logs.
- SQL queries throughout use parameterized placeholders; no string concatenation of untrusted values was found.
- The Gemini/Anthropic/OpenAI compatibility shims all delegate to `_handle_chat_completions`, ensuring auth and budget enforcement apply uniformly regardless of client dialect.
- No hardcoded credentials or live API keys found in any tracked file.

---

## Remediation Status

| Finding | Status |
|---------|--------|
| H-1 — Container runs as root | **FIXED** — `Dockerfile` |
| M-1 — Non-constant-time token compare | **FIXED** — `app/main.py` |
| M-2 — Session cookie missing Secure flag | **FIXED** — `app/main.py` (opt-in via env var) |
| M-3 — pip/setuptools CVEs | **DEFERRED** — operator to pin toolchain |
| L-1 — `/metrics` open | **DEFERRED** — conventional; restrict at network layer |
| L-2 — `/v1/models` open | **DEFERRED** — intentional OpenAI compatibility |

---

*This report was generated automatically. It does not substitute for a manual penetration test.*
