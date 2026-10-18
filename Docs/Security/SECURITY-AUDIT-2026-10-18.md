# Security Audit — 2026-10-18

**Auditor:** Automated daily audit (Claude Code)
**Branch audited:** `security/audit-2026-10-18` (off `main` @ `e8ad2e3`)
**Prior report:** None — this is the first audit run.

---

## Severity Summary

| Severity | Count | Fixed this run | Deferred |
|----------|-------|---------------|----------|
| High     | 1     | 1             | 0        |
| Medium   | 1     | 1             | 0        |
| Low      | 3     | 1             | 2        |
| Info     | 1     | 0             | 1        |

---

## High Findings

### H-1 — Bearer tokens leaked as Prometheus metric labels (FIXED)

**File/Location:** `app/telemetry.py:487`, `app/main.py:2538`

**Risk:** The `/metrics` endpoint (unauthenticated) emits Prometheus counters with a `team_id` label whose value is the full `cv_team_<32hex>` bearer token for every team that has made an API call. Any entity with network access to the metrics endpoint can harvest all active team tokens, granting full API access on behalf of those teams with no further credentials.

**Root cause:** `metrics_snapshot()` aggregated call rows grouped by the raw `team_id` column (which IS the bearer token). The `/metrics` handler has no `_admin_auth` gate.

**Remediation applied:** `app/telemetry.py` now replaces the raw `team_id` with `sha256(team_id)[:8]` in the bucket key so the label is an opaque fingerprint, not the credential itself. This preserves per-team cardinality in dashboards without exposing the token.

**Deferred (separate design needed):** Add `_admin_auth` gate to `/metrics` so the full endpoint is inaccessible to unauthenticated scrapers. This requires Prometheus scrape config to supply a Bearer token and is a behavior-changing change; tracked as a follow-up hardening item.

---

## Medium Findings

### M-1 — Non-constant-time admin token comparison (FIXED)

**File/Location:** `app/main.py:301`

**Risk:** `_admin_auth()` compared the supplied bearer token against `CLEARVIEW_ADMIN_TOKEN` using Python `!=`, which is not constant-time. A remote attacker making many timing measurements against a network-accessible deployment could infer the admin token byte by byte.

**Remediation applied:** Changed to `secrets.compare_digest(token, expected)` and added `import secrets` to `app/main.py`.

---

## Low Findings

### L-1 — Dockerfile runs as root (FIXED)

**File/Location:** `Dockerfile`

**Risk:** The container image had no `USER` directive, so the process ran as root (UID 0). A container escape or path-traversal vulnerability would then have root access to the host.

**Remediation applied:** Added `RUN adduser --disabled-password --gecos "" clearview && mkdir -p /data && chown clearview /data` followed by `USER clearview` before the `VOLUME` and `CMD` directives.

---

### L-2 — pip 24.0 has multiple known CVEs (DEFERRED)

**File/Location:** `Dockerfile` (base image ships pip 24.0)

**Risk:** pip 24.0 is affected by PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-196, PYSEC-2026-3721. Fix versions: 26.2+. These are in the build-tool chain (not a runtime app dependency) but could affect `pip install` in CI/CD or during image build.

**Proposed remediation:** Add `RUN pip install --upgrade pip` in the Dockerfile after the base image, or pin the base image to one shipping pip ≥ 26.2.

**Reason deferred:** Upgrading pip in the Dockerfile is low-urgency for a runtime gateway but should be done in a follow-up.

---

### L-3 — setuptools 79.0.1 has known CVE (DEFERRED)

**File/Location:** `Dockerfile` / build environment

**Risk:** setuptools 79.0.1 is affected by PYSEC-2026-3447. Fix version: 83.0.0.

**Proposed remediation:** Pin `setuptools>=83.0.0` in `pyproject.toml` build dependencies or upgrade during Docker image build.

**Reason deferred:** setuptools is a build tool, not a runtime dependency loaded by the app. Risk is confined to the build step.

---

## Info / Design Notes

### I-1 — `/metrics` lacks authentication gate (DEFERRED — see H-1)

The `/metrics` endpoint is open to unauthenticated access. While H-1 removes the credential leak from the output, the endpoint still exposes aggregate call counts, tier distribution, provider usage, and cost totals to anyone who can reach it. Recommend adding `_admin_auth(request)` to the handler, with Prometheus scrape config updated to pass the `CLEARVIEW_ADMIN_TOKEN` as a `Authorization: Bearer` header.

---

## Positive Observations

- **SQL queries are parameterized.** All f-string SQL in admin endpoints builds only the `WHERE` clause structure from trusted/hardcoded values; all user-supplied filter values (session, team, window) are passed as `?` placeholders.
- **No shell=True / os.system / eval / exec / pickle found.** subprocess use (`asyncio.create_subprocess_exec`) passes arguments as a list, not via shell interpolation.
- **No tracked secrets.** `git ls-files` returns no `.env`, `.key`, `.pem`, or similar files. Grep for `sk-`, `AKIA`, `ghp_` found no hits in tracked files.
- **Token generation uses `secrets` module.** `teams.create()` uses `secrets.token_hex(16)` and `chat.py` uses `secrets.token_hex(8)`.
- **Team tokens in list responses are redacted.** `/admin/teams` (GET) returns only `id_short` (first 18 chars + `...`), never the full token.
- **`/chat/login` cookie is HttpOnly + SameSite=Lax.** Prevents JS token theft and most CSRF attacks on the chat login flow.
- **`/v1/models` open endpoint is appropriate.** Model listing is standard OpenAI-compatible behavior and exposes no sensitive data.
- **`/health` open endpoint is appropriate.** Standard liveness probe.
- **`/feedback` open endpoint is intentional.** Documented; request_id is the coupling handle, not a secret.

---

## Remediation Status

| ID  | Finding                                     | Status  | Commit |
|-----|---------------------------------------------|---------|--------|
| H-1 | Bearer tokens in Prometheus labels          | Fixed   | this PR |
| M-1 | Non-constant-time admin token compare       | Fixed   | this PR |
| L-1 | Dockerfile runs as root                     | Fixed   | this PR |
| L-2 | pip 24.0 CVEs                               | Deferred | — |
| L-3 | setuptools 79.0.1 CVE                       | Deferred | — |
| I-1 | /metrics lacks auth gate                    | Deferred | — |
