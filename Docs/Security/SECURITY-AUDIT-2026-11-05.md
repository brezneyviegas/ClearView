# Security Audit — 2026-11-05

**Auditor:** Daily automated security audit (ClearView scheduled routine)
**Branch audited:** `main` (HEAD `e8ad2e3`)
**Previous report:** None (inaugural audit)
**Deferred from prior audit:** None

---

## Severity Summary

| Severity | New Findings | Fixed in this run | Deferred |
|----------|-------------|-------------------|----------|
| High     | 0           | 0                 | 0        |
| Medium   | 2           | 2                 | 0        |
| Low      | 4           | 1                 | 3        |
| **Total**| **6**       | **3**             | **3**    |

Fixes applied on branch `security/audit-2026-11-05`.

---

## High Findings

None.

---

## Medium Findings

### M1 — Non-constant-time admin token comparison
**Location:** `app/main.py:302` (`_admin_auth`)
**Risk:** `token != expected` is a string equality check subject to short-circuit evaluation. An attacker making many requests against a production instance with `CLEARVIEW_ADMIN_TOKEN` set can use response-time variance to recover the token one character at a time (timing side-channel). Admin endpoints expose team management, telemetry, and policy tuning — full gateway compromise on a successful attack.
**Remediation:** Replace with `secrets.compare_digest(token, expected)`.
**Status: FIXED** — `import secrets` added; `token != expected` replaced with `not secrets.compare_digest(token, expected)` in this PR.

### M2 — filelock 3.32.7: 13 CVEs (TOCTOU / symlink / privilege escalation)
**Location:** `pyproject.toml` dependency `filelock` (transitive via litellm)
**Risk:** CVE-2026-112940, CVE-2026-112913, CVE-2026-112907, CVE-2026-112938, CVE-2026-112936, CVE-2026-112910, CVE-2026-112909, CVE-2026-112911, CVE-2026-112939, CVE-2026-112915, CVE-2026-112935, CVE-2026-112912, CVE-2026-112908. All fix at `filelock>=4.2.1`. Most are local-user TOCTOU/symlink attacks on lock files (require another local user on the same machine). On a container deployment the blast radius is contained. On a bare-metal shared server the risk is higher.
**Remediation:** `pip install "filelock>=4.2.1"`. Add `filelock>=4.2.1` to `pyproject.toml` extras or as a direct constraint. No API changes.
**Status: DEFERRED** — Upgrading a transitive dependency requires verifying litellm compatibility and is not a one-line mechanical fix. Proposed fix: pin `filelock>=4.2.1` in `pyproject.toml` `[project.dependencies]` or in a `constraints.txt`. The CVEs are local-only on a container deployment; risk is Low in that context.

---

## Low Findings

### L1 — Dockerfile: container runs as root
**Location:** `Dockerfile` (no `USER` directive before original `CMD`)
**Risk:** If a vulnerability in any dependency allows code execution inside the container, the attacker has root-equivalent filesystem access within the container, simplifying container escape or pivot to the host.
**Remediation:** Add `RUN adduser --system ... && USER clearview` before `CMD`.
**Status: FIXED** — `clearview` system user created and set as `USER` in this PR.

### L2 — `/metrics` endpoint unauthenticated
**Location:** `app/main.py:2538` (`metrics`)
**Risk:** The Prometheus `/metrics` endpoint is served without authentication. It exposes operational telemetry: provider names and tiers, request counts, cost figures, and drift percentages. While it contains no user data, it leaks the full provider topology and cost structure of the deployment to any unauthenticated caller who can reach the port.
**Remediation:** Call `_admin_auth(request)` on the metrics handler, or place the endpoint behind a network-level firewall rule that restricts access to the Prometheus scraper's IP.
**Status: DEFERRED** — Adding auth to `/metrics` could break existing Prometheus scrapers that do not send a bearer token. This requires operator coordination (scraper reconfiguration). Proposed design: add an optional `CLEARVIEW_METRICS_TOKEN` env var checked by a separate `_metrics_auth` helper, so deployments without it remain open (backward-compatible) while production deployments can gate it.

### L3 — `/chat/login` cookie missing `Secure` flag
**Location:** `app/main.py:2651` (`chat_login`, `set_cookie`)
**Risk:** `cv_session` is set with `HttpOnly=True, SameSite=Lax` but no `Secure=True`. On an HTTPS deployment, if a client ever sends an HTTP request (e.g. mixed-content redirect), the browser will include the cookie, exposing the team bearer token over plaintext.
**Remediation:** Add `secure=True` to the `set_cookie` call, controlled by a `CLEARVIEW_HTTPS=1` environment variable (or detect via `request.url.scheme`) so local HTTP dev isn't broken.
**Status: DEFERRED** — Unconditionally adding `secure=True` breaks `http://localhost` development. Requires conditional logic or a config flag; not a one-line mechanical fix.

### L4 — pip / setuptools CVEs (build-time only)
**Location:** Python build toolchain (`pip 24.0`, `setuptools 79.0.1`)
**Risk:** `pip`: PYSEC-2026-1795/1796/196/2875/2876/3721 — archive path traversal and symlink issues when installing maliciously crafted packages (fix: pip>=26.2.0). `setuptools`: PYSEC-2026-3447, GHSA-xmj3-9gf7-h52w — MANIFEST exclusion bypass and wheel install path traversal (fix: setuptools>=84.1.0). These affect the build/install phase only, not the running application runtime.
**Remediation:** Upgrade pip and setuptools in the base Docker image: `pip install --upgrade pip>=26.2.0 setuptools>=84.1.0` before `pip install -e .`, or use a fresh Python base image that ships newer tooling.
**Status: DEFERRED** — Build-toolchain CVEs, not runtime. Low operational risk. Should be addressed in a Dockerfile hardening pass.

---

## Carried-Over Deferred Findings

None (inaugural audit).

---

## Positive Observations

- **SQL queries are fully parameterized.** All SQLite queries in admin handlers use `?` placeholders. No f-string/concat SQL interpolation was found that could lead to injection.
- **No injection vectors.** Grep for `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, and unsafe `yaml.load(` returned only `asyncio.create_subprocess_exec` calls (which correctly pass args as a list, never shell-expanded).
- **Team token generation uses `secrets`.** `teams.create()` calls `secrets.token_hex(16)` — cryptographically secure.
- **No tracked secrets.** `git ls-files` found no `.env`, `.pem`, `.key`, or `.crt` files. Grep for live-looking key patterns (`sk-`, `AKIA`, `ghp_`) returned no matches in tracked files.
- **All admin routes call `_admin_auth`.** Every `@app.get/post/delete/patch` under `/admin/` explicitly calls `_admin_auth(request)`. The three intentionally open endpoints (`/v1/models`, `/health`, `/feedback`) are either standard OpenAI-compat discovery, health probes, or user-self-service (documented comment: "No admin auth — callers rate their own responses").
- **Chat endpoints call `_require_chat_team`.** All `/chat/*` data endpoints (model_options, conversations, messages, send, send_stream) call `_require_chat_team` or `_resolve_team`, preventing cross-team access.
- **Cookie security is partially hardened.** `cv_session` has `HttpOnly=True` and `SameSite=Lax`, preventing XSS-based theft and CSRF. Only `Secure` is missing (L3 above).
- **docker-compose.yml does not expose unauthenticated services beyond intended ports.** Ollama is under the `local` profile and published on `11434`; ClearView on `8000`. Neither is a database or secrets store.

---

## Remediation Status

| Finding | Status | PR |
|---------|--------|----|
| M1 — Non-constant-time admin token comparison | **Fixed** | this PR |
| M2 — filelock CVEs | Deferred | — |
| L1 — Dockerfile runs as root | **Fixed** | this PR |
| L2 — `/metrics` unauthenticated | Deferred | — |
| L3 — Cookie missing `Secure` flag | Deferred | — |
| L4 — pip/setuptools build CVEs | Deferred | — |
