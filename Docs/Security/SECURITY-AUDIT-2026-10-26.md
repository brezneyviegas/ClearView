# ClearView Security Audit — 2026-10-26

**Auditor:** Automated daily audit (Claude Sonnet 4.6)
**Branch audited:** main (HEAD e8ad2e3)
**Scope:** Full codebase sweep — routes auth-gate check, dangerous-pattern grep,
SQL parameterization, secret scan, token generation/comparison, Docker config,
pip-audit CVE scan.

---

## Severity Summary

| Severity | Count | Status |
|----------|-------|--------|
| High     | 1     | **Fixed in this PR** |
| Medium   | 3     | 2 Fixed / 1 Deferred |
| Low      | 1     | Deferred |

---

## Findings

### HIGH — H1: Non-constant-time admin token comparison

**File:** `app/main.py:301`
**Risk:** `_admin_auth` compares the bearer token to `CLEARVIEW_ADMIN_TOKEN` with
`if token != expected:`. Python's string `!=` is a short-circuit comparison —
it returns early on the first differing byte, leaking timing information that
an attacker on a co-hosted or local network can measure to recover the token
one character at a time.
**Remediation:** Replace `!=` with `secrets.compare_digest(token, expected)`.
**Status: Fixed** (see commit in this PR).

---

### MEDIUM — M1: Dockerfile runs as root

**File:** `Dockerfile`
**Risk:** No `USER` directive — the application process runs as root inside the
container. If any path-traversal or remote-code-execution vulnerability is
exploited, the attacker gets a root shell with write access to `/` inside the
container, and may escape to the host depending on the runtime configuration.
**Remediation:** Add `RUN useradd -r -u 1001 clearview` and `USER clearview`
after the `RUN pip install` step.
**Status: Fixed** (see commit in this PR).

---

### MEDIUM — M2: `cv_session` cookie missing `Secure` flag

**File:** `app/main.py:2651`
**Risk:** The `cv_session` cookie is set with `httponly=True, samesite="lax"` but
`secure` is not set. If a user visits the app over HTTP (e.g. a misconfigured
proxy, or a development machine accessed via HTTP before upgrading to HTTPS),
the session token is transmitted in cleartext and can be captured in transit.
**Remediation:** Add `secure=True` to `resp.set_cookie(...)`.
**Status: Fixed** (see commit in this PR).

---

### MEDIUM — M3: `/metrics` endpoint unauthenticated (Deferred)

**File:** `app/main.py:2538`
**Risk:** The Prometheus `/metrics` endpoint is open with no auth gate. It emits
per-provider, per-team, per-tier call counts and cost totals. On a shared
network this leaks operational cost and usage patterns to anyone who can reach
the port.
**Proposed design:** Gate it with `_admin_auth(request)` — a single-line
addition identical to all other `/admin/*` routes. For Prometheus scraping,
set `CLEARVIEW_ADMIN_TOKEN` and configure the scraper's `Authorization: Bearer`
header. This is a behavioral change (breaks unauthenticated scraper configs)
so it is deferred for operator review.
**Status: Deferred.**

---

### LOW — L1: filelock CVE (pip-audit)

**Package:** `filelock 3.32.7` (transitive dependency via litellm)
**CVEs:** CVE-2026-112940, CVE-2026-112913, CVE-2026-112907 (fixed in 4.2.1)
**Risk:** filelock is used for filesystem locking; the CVEs relate to lock file
handling under race conditions. ClearView's use of filelock is indirect (via
litellm) and the vectors require a local adversary on the same filesystem — low
practical impact for a containerised gateway.
**Remediation:** Pin `filelock>=4.2.1` in `pyproject.toml` or wait for litellm
to update its dependency.
**Status: Deferred** — dependency update; low exploitability in container context.

---

## Checks with no findings

| Check | Result |
|-------|--------|
| Route auth gate sweep (all `@app.get/post/delete`) | All admin/chat routes gated. Open routes (`/v1/models`, `/health`, `/feedback`, `/v1/chat/completions`, `/v1/messages`, `/v1/responses`, Gemini shims, `/chat` GET, `/chat/login`, `/chat/logout`) are documented as intentionally open. |
| `shell=True` / `os.system` / `eval` / `exec` / `pickle` / unsafe `yaml.load` | None found. All subprocess calls use `create_subprocess_exec` (no shell). |
| SQL injection (f-string/concat queries) | All f-string WHERE clauses build the clause from whitelisted column names; user-supplied values are always bound as `?` parameters. No injection path found. |
| Tracked secrets (`.env`, `.key`, `.pem`, API key patterns) | No tracked secret files. No live-looking keys found (`sk-`, `AKIA`, `ghp_`). |
| Token generation | `secrets.token_hex(16)` used for `cv_team_` tokens — cryptographically strong. |
| pip/setuptools build-env CVEs | Affect the build environment only; not deployed to the runtime image. Low risk. |

---

## Positive Observations

- All `/admin/*` routes consistently call `_admin_auth` before any logic.
- Chat endpoints gate on `_require_chat_team` (which calls `_resolve_team`),
  isolating conversations by team token.
- Team tokens are scoped with a `cv_team_` prefix and generated with 128 bits
  of entropy via `secrets.token_hex(16)`.
- No `shell=True` subprocess usage — all CLI provider adapters use
  `asyncio.create_subprocess_exec`.
- SQLite queries are parameterized throughout; the SQL f-string pattern is
  used only for dynamically-built column sets (whitelisted) or WHERE clauses
  with hardcoded column names and `?` value placeholders.
- `.env` / key files are not tracked in git.
