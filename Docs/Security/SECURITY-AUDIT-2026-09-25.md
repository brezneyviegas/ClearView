# Security Audit — 2026-09-25

**Auditor:** Automated daily audit (Claude Code)  
**Repo:** ClearView (Python/FastAPI LLM gateway)  
**Branch audited:** `main` (HEAD `e8ad2e3`)  
**Prior audit:** None (first audit)  
**Remediation branch:** `security/audit-2026-09-25`

---

## Severity Summary

| Severity | Total | Fixed this run | Deferred |
|----------|-------|----------------|----------|
| High     | 0     | 0              | 0        |
| Medium   | 3     | 2              | 1        |
| Low      | 2     | 1              | 1        |

---

## Findings

### Medium — M1: Non-constant-time admin token comparison (FIXED)

**File/location:** `app/main.py:301` (`_admin_auth`)  
**Risk:** Python's `!=` operator short-circuits on the first differing byte, exposing a timing side-channel. An attacker on the same network could use statistical timing measurements to brute-force the `CLEARVIEW_ADMIN_TOKEN` character by character.  
**Remediation:** Replace `if token != expected:` with `hmac.compare_digest`. Applied in this PR.  

### Medium — M2: Dockerfile runs as root (FIXED)

**File/location:** `Dockerfile` (no `USER` directive)  
**Risk:** The containerised app process runs as UID 0. Any remote code execution vulnerability (e.g., in a dependency) immediately yields full container root, and — if the container is misconfigured — potential host escape. Docker best practice is a non-root UID.  
**Remediation:** Added `useradd -r clearview` + `USER clearview` before the entrypoint. Applied in this PR. Note: the `/data` volume mount must be writable by UID 1001; the Dockerfile `chown clearview /data` handles this.  

### Medium — M3: `/metrics` Prometheus endpoint unauthenticated (DEFERRED)

**File/location:** `app/main.py:2538` (`metrics()`)  
**Risk:** The Prometheus scrape endpoint exposes `tier`, `provider`, `status`, and `team_id` labels without any auth gate. An unauthenticated caller can enumerate active team IDs, infer spending patterns, and fingerprint the routing policy.  
**Remediation proposal:** Add an optional `CLEARVIEW_METRICS_TOKEN` env var and call a lightweight auth check in `metrics()`. When unset, warn at startup rather than silently defaulting open; or at minimum restrict to loopback in `docker-compose.yml`. Deferred because adding auth breaks existing Prometheus scrapers and needs an operator-side config change.

### Low — L1: pip/setuptools CVEs in Docker build toolchain (DEFERRED)

**File/location:** `Dockerfile` (`pip install`)  
**CVEs:** `pip 24.0` — PYSEC-2026-1795 (fix: 25.3), PYSEC-2026-1796 (fix: 26.0), PYSEC-2026-2875/2876 (fix: 26.1), PYSEC-2026-196 (fix: 26.1.2), PYSEC-2026-3721 (fix: 26.2); `setuptools 79.0.1` — PYSEC-2026-3447 (fix: 83.0.0).  
**Risk:** Low at runtime — these are build-time tools. The CVEs affect maliciously crafted packages. ClearView installs from a controlled `pyproject.toml`; exploitation requires a supply-chain compromise of a pinned dependency. However, the Dockerfile does not pin pip/setuptools to safe versions.  
**Remediation proposal:** Add `RUN pip install --upgrade pip>=26.2.1 setuptools>=83.0.0` before the main `pip install` in the Dockerfile, or upgrade the base image to one bundling a newer pip. Deferred because this is a build-toolchain change with no app-runtime impact, and the fix versions should be validated before pinning.

### Low — L2: `/v1/models` open endpoint — documented (NO ACTION)

**File/location:** `app/main.py:364`  
**Risk:** The model-listing endpoint has no auth guard. This is intentional — OpenAI-compatible IDE clients (VS Code Continue, Cline, etc.) probe `/v1/models` before sending the first request and expect it to be open.  
**Status:** Accepted. The risk is minimal (returns only model names, no telemetry). Documented here for the record.

---

## Deferred Items (carried forward)

| ID  | Severity | Description                                 | Proposed Design                                     |
|-----|----------|---------------------------------------------|-----------------------------------------------------|
| M3  | Medium   | `/metrics` unauthenticated                  | `CLEARVIEW_METRICS_TOKEN` opt-in + startup warning  |
| L1  | Low      | pip/setuptools CVEs in Docker build chain   | Pin `pip>=26.2.1 setuptools>=83.0.0` in Dockerfile  |

---

## Minimum-sweep Checklist

| Check | Result |
|-------|--------|
| Every `@app.get/post/delete` route calls an auth gate or has a documented reason | ✓ All admin routes call `_admin_auth`. Chat routes call `_require_chat_team`. `/v1/models`, `/health`, `/feedback`, `/metrics` open (see L2 and M3). |
| `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, `yaml.load` | ✓ None found in `app/` |
| SQL parameterization | ✓ All queries use `?` placeholders. f-strings only inject hardcoded column-name fragments, never user values. |
| Tracked secrets (`.env`, `.pem`, live key patterns) | ✓ Only `.env.example` and `clearview-client.env.example` tracked; no live keys (`sk-x`/`sk-test` values in tests are fixtures). |
| Secret comparisons use `secrets.compare_digest` / `hmac.compare_digest` | ✓ Fixed in M1 (this run). |
| Token generation uses `secrets` module | ✓ `secrets.token_hex` used for team IDs and conversation IDs. |
| Dockerfile has non-root `USER` | ✓ Fixed in M2 (this run). |
| `docker-compose.yml` does not publish unauthenticated services beyond loopback | ⚠ `ollama` publishes `0.0.0.0:11434` under `--profile local`. Acceptable for a dev-mode profile; operators should firewall port 11434 in shared environments. |
| `pip-audit` CVEs | See L1 (pip + setuptools build-toolchain only; no app-runtime CVEs). |

---

## Positive Observations

- No use of `shell=True`, `eval`, `exec`, `pickle`, or unsafe `yaml.load` anywhere in `app/`.
- All SQL queries are properly parameterized; no f-string injection of user-controlled values.
- Team tokens and conversation IDs are generated with `secrets.token_hex` (cryptographically strong).
- Admin token is gated by a `CLEARVIEW_ADMIN_TOKEN` env var; when unset the service intentionally runs in dev-open mode with a startup note.
- Budget enforcement guards are in place for both team-level and global daily/monthly caps.
- No live API keys or credentials tracked in git.
- Cookie auth uses `HttpOnly` + `SameSite=Lax` (CSRF-safe for same-origin fetch).

---

## Remediation Status

| ID | Finding | Action | Commit |
|----|---------|--------|--------|
| M1 | Non-constant-time admin token compare | Fixed — `hmac.compare_digest` | this PR |
| M2 | Dockerfile root user | Fixed — `USER clearview` (UID 1001) | this PR |
| M3 | `/metrics` unauthenticated | Deferred — needs design + operator config | — |
| L1 | pip/setuptools CVEs | Deferred — build-toolchain upgrade | — |
| L2 | `/v1/models` open | Accepted as documented | — |
