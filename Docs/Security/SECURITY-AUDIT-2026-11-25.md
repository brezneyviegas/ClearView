# ClearView Security Audit — 2026-11-25

**Auditor:** Automated daily audit (Claude Code)
**Branch audited:** `main` (HEAD `e8ad2e3`)
**Audit date:** 2026-11-25
**Previous report:** `SECURITY-AUDIT-2026-09-28.md` (branch `security/audit-2026-09-28`)

---

## Severity Summary

| Severity | New | Fixed This Run | Deferred | Carried Over |
|----------|-----|----------------|----------|--------------|
| High     | 1   | 1              | 0        | 0            |
| Medium   | 1   | 1              | 4        | 3            |
| Low      | 0   | 0              | 1        | 1            |

---

## High Findings

### H-1 — Non-constant-time admin token comparison (REGRESSION) ✅ FIXED

**File:** `app/main.py:302` (`_admin_auth`)
**Risk:** Timing side-channel allows an attacker to enumerate `CLEARVIEW_ADMIN_TOKEN`
character-by-character using response-time differences, reducing brute-force search
space from O(charset^length) to O(charset × length).
**Root cause:** The `secrets.compare_digest` fix applied in the 2026-09-28 audit was not
present in `main` HEAD `e8ad2e3` — the branch was never merged, and `main` retained
the original vulnerable comparison (`token != expected`).
**Remediation:** Added `import secrets` (line 11) and replaced `token != expected` with
`not secrets.compare_digest(token, expected)`.
**Status:** Fixed in this PR.

---

## Medium Findings

### M-1 — Dockerfile: container runs as root (REGRESSION) ✅ FIXED

**File:** `Dockerfile` (no `USER` directive before `CMD`)
**Risk:** RCE through the app gives an attacker root inside the container, simplifying
container-escape and filesystem attacks.
**Root cause:** Same as H-1 — the `appuser` fix from 2026-09-28 was not present in `main`.
**Remediation:** Added `useradd` for uid 1001 and `USER appuser` before `CMD`.
**Status:** Fixed in this PR.

### M-2 — `/metrics` endpoint unauthenticated ⏳ DEFERRED (carried over)

**File:** `app/main.py:2538`
**Risk:** Publicly exposes per-tier/provider request counts, cost totals, token counts,
and drift percentage — enough to infer pricing configuration and usage patterns.
**Proposed design:** Add optional `CLEARVIEW_METRICS_TOKEN`; bearer check applied when
set, endpoint stays open otherwise for local Prometheus scraping.
**Reason deferred:** Behavior-changing for existing Prometheus setups.

### M-3 — docker-compose: port 8000 bound to all interfaces ⏳ DEFERRED (carried over)

**File:** `docker-compose.yml:8` (`ports: "8000:8000"`)
**Risk:** Gateway exposed on `0.0.0.0:8000`; any host on the Docker host's network can
reach it.
**Proposed design:** Change to `127.0.0.1:8000:8000` for single-developer setups;
document that production deployments must sit behind a reverse proxy.
**Reason deferred:** Could break existing users who rely on LAN access.

### M-4 — pip/setuptools CVEs in build toolchain ⏳ DEFERRED (carried over + new)

**Tool:** `pip==24.0`, `setuptools==79.0.1` (dev/build environment)
**CVEs:**
- `pip`: PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-196, PYSEC-2026-2875,
  PYSEC-2026-2876, PYSEC-2026-3721 (fix: upgrade to pip ≥ 26.2.1)
- `setuptools`: PYSEC-2026-3447 (fix: upgrade to setuptools ≥ 83.0.0),
  **NEW** GHSA-xmj3-9gf7-h52w (fix: upgrade to setuptools ≥ 84.1.0)
**Risk:** Affects build/install toolchain only, not runtime app code. Exploitation
requires a malicious package to be installed during a build step.
**Proposed design:** Pin `pip>=26.2.1` and `setuptools>=84.1.0` in `pyproject.toml`
build deps or add `RUN pip install --upgrade pip setuptools>=84.1.0` to Dockerfile
before the app install step.
**Reason deferred:** Requires testing of full build pipeline; no shipped app runtime impact.

---

## Low Findings

### L-1 — `/metrics` leaks provider model names via label values ⏳ DEFERRED (carried over)

**File:** `app/main.py:2547` (`metrics()`)
**Risk:** Provider/model names embedded in Prometheus label values reveal the configured
provider roster. Sub-issue of M-2; fixing M-2 resolves it.
**Reason deferred:** Subsumes into M-2.

---

## Minimum-sweep checklist

| Check | Result |
|-------|--------|
| Every `@app.*` route calls `_admin_auth`, `_client_key_allowed`, `_resolve_team`, or `_require_chat_team`, or has a documented reason to be open | **Pass** — `/v1/models`, `/health`, `/metrics`, `/feedback`, `/chat` (login page), `/chat/login`, `/chat/logout` are intentionally public; all `/admin/*` routes call `_admin_auth`; all `/chat/*` data routes call `_require_chat_team`; all `/v1/chat`, `/v1/messages`, `/v1/responses`, Gemini shims call `_client_key_allowed`+`_resolve_team` |
| `shell=True` / `os.system` / `eval(` / `exec(` / `pickle` / `yaml.load` | **Pass** — none found; providers use `asyncio.create_subprocess_exec` (no shell) |
| SQL parameterized — no f-string/concat with non-whitelisted values | **Pass** — `WHERE` clause fragments are assembled from hardcoded column/operator strings only; all user-supplied values (`session`, `team`, `window`) go through `?` parameters |
| No tracked secrets: `.env`/`.pem`/key files in git | **Pass** — only `.env.example` and `clearview-client.env.example` (examples, no live values) |
| Live-looking keys in tracked files (`sk-`, `AKIA`, `ghp_`) | **Pass** — no live keys found |
| Secret comparisons use `secrets.compare_digest` | **Fixed** — H-1 regression patched this run |
| Token generation uses `secrets` module | **Pass** — `teams.py:117` uses `secrets.token_hex(16)`, `chat.py:62` uses `secrets.token_hex(8)` |
| Dockerfile has non-root `USER` | **Fixed** — M-1 regression patched this run |
| docker-compose doesn't publish unauthenticated services beyond loopback | **Deferred** — M-3 |
| pip-audit: no runtime CVEs | **Pass (with note)** — CVEs found only in `pip` and `setuptools` (build toolchain, not app runtime); see M-4; one new CVE GHSA-xmj3-9gf7-h52w for setuptools |

---

## Positive Observations

- Team tokens generated with `secrets.token_hex(16)` (128-bit entropy) — strong.
- Chat session cookies are `HttpOnly`, `SameSite=Lax` — protects against XSS token theft.
- Team token listing redacts to a short prefix (`id_short`) — minimizes leakage via operator UI.
- Budget enforcement (daily/monthly per-team + global) in place with reject/warn/allow modes.
- No `shell=True` subprocess calls; all CLI providers use `create_subprocess_exec`.
- All SQL queries use parameterized `?` bindings for user-supplied values.
- `/feedback` accepts ratings without auth, but only by `request_id` handle — no sensitive data exposed.
- New Gemini and OpenAI Responses shims (`/v1/responses`, `/v1beta/models/...`) all route through `_handle_chat_completions`, inheriting its auth checks.

---

## Remediation Status

| ID  | Finding                                   | Status      | Commit   |
|-----|-------------------------------------------|-------------|----------|
| H-1 | Non-constant-time admin token (regression)| ✅ Fixed    | This PR  |
| M-1 | Dockerfile non-root USER (regression)     | ✅ Fixed    | This PR  |
| M-2 | `/metrics` unauthenticated                | ⏳ Deferred | —        |
| M-3 | docker-compose all-interface binding      | ⏳ Deferred | —        |
| M-4 | pip/setuptools CVEs in build toolchain    | ⏳ Deferred | —        |
| L-1 | `/metrics` leaks provider names           | ⏳ Deferred (subsumes into M-2) | — |
