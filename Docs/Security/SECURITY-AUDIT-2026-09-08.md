# Security Audit — 2026-09-08

**Auditor:** Daily automated routine  
**Branch audited:** `main` (HEAD: e8ad2e3)  
**Remediation branch:** `security/audit-2026-09-08`  
**Previous report:** None (first audit)

---

## Severity Summary

| Severity | New | Fixed this run | Deferred |
|----------|-----|----------------|----------|
| High     | 1   | 1              | 0        |
| Medium   | 2   | 1              | 1        |
| Low      | 2   | 0              | 2        |

---

## High Findings

### H-1 — Dockerfile runs as root (no USER directive) ✅ FIXED

**File/Location:** `Dockerfile`  
**Risk:** The container process ran as UID 0. Any RCE vulnerability, path-traversal, or misconfigured bind-mount grants full root in the container. Best-practice and many container-hardening scanners (Trivy, Hadolint) flag this as Critical.  
**Remediation applied:** Added a `useradd` for a `clearview` (UID 1001) service user and a `USER clearview` directive before `CMD`. `/data` is chowned to that user so the volume mount remains writable.  
**Tests:** All 359 tests pass.

---

## Medium Findings

### M-1 — Non-constant-time admin token comparison ✅ FIXED

**File/Location:** `app/main.py:301` (`_admin_auth`)  
**Risk:** `token != expected` is a standard string comparison that short-circuits on the first differing byte, enabling a timing-oracle attack. An adversary on a low-latency path could use timing differences to recover the value of `CLEARVIEW_ADMIN_TOKEN` one character at a time, bypassing all admin endpoints.  
**Remediation applied:** Replaced with `secrets.compare_digest(token, expected)`, which compares in constant time regardless of token content. Added `import secrets` to the file header.  
**Tests:** All 359 tests pass.

### M-2 — `/metrics` endpoint unauthenticated — DEFERRED

**File/Location:** `app/main.py:2538` (`metrics`)  
**Risk:** The Prometheus `/metrics` endpoint is fully open (no auth check). Its label set includes `team_id`, which for ClearView is the literal `cv_team_` bearer token that authorises client API access. An unauthenticated observer can read all team tokens from the metrics page, together with per-team spend, provider usage, and tier patterns.  
**Proposed remediation (deferred — behaviour change):**  
Option A: gate with `_admin_auth(request)` — breaks existing Prometheus scrapers if `CLEARVIEW_ADMIN_TOKEN` is set.  
Option B: strip `team_id` from the metrics labels and replace with a stable opaque hash (e.g. `sha256(team_id)[:12]`) so the token value is never emitted. This is less disruptive to scraper configs but requires a one-time Prometheus label rename.  
Option C: add a separate `CLEARVIEW_METRICS_TOKEN` env var and gate with that, allowing independent rotation from the admin token.  
**Recommendation:** Option B (hash the label) is the safest non-breaking fix; deploy Option A or C if a breaking change is acceptable for your Prometheus setup. Requires explicit user sign-off before applying.

---

## Low Findings

### L-1 — pip 24.0 build-tool CVEs — DEFERRED (build-tool only)

**File/Location:** Build environment / `Dockerfile` pip invocation  
**CVEs:** PYSEC-2026-196, PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-3721 (fix: pip ≥ 26.1.2)  
**Risk:** pip is only present at image build time; it is not imported or reachable at runtime. Exploitation requires a compromised PyPI package or build pipeline — not a direct user-reachable vector in the shipped container.  
**Proposed remediation:** Pin `pip >= 26.1.2` in the Dockerfile `RUN pip install --upgrade pip` step before installing app dependencies, or use a base image that ships a newer pip. Deferred pending a scheduled base-image refresh; low urgency given build-only scope.

### L-2 — setuptools 79.0.1 CVE — DEFERRED (build-tool only)

**File/Location:** Build environment  
**CVE:** PYSEC-2026-3447 (fix: setuptools ≥ 83.0.0)  
**Risk:** Same scope as pip above — build-time only, not reachable at runtime.  
**Proposed remediation:** Same base-image or explicit `pip install --upgrade setuptools>=83.0.0` in the Dockerfile RUN step.

---

## Carried-Over Deferred Findings

None (first audit).

---

## Audit Checklist — Attestation

| Check | Result |
|-------|--------|
| Auth coverage: every `@app.get/post/delete` in `app/main.py` gated or documented-open | ✅ Pass — all admin routes call `_admin_auth`; `/metrics` is an **open** finding (M-2); `/health`, `/v1/models`, `/chat/login`, `/chat/logout`, `/feedback` are intentionally open and commented as such |
| Injection: `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, unsafe `yaml.load` | ✅ Pass — none found; subprocess calls in providers use `create_subprocess_exec` (list args, no shell) |
| SQL parameterization — f-string/concat interpolating user input into queries | ✅ Pass — all `WHERE` clauses are built from pre-validated clause strings; user-supplied values always bound via `?` parameters |
| Tracked secrets: `git ls-files` for env/key/pem; grep for `sk-`, `AKIA`, `ghp_`, live-looking keys | ✅ Pass — only `.env.example` and `clearview-client.env.example` (example files, no live values); no live keys found in tracked files |
| Token comparisons use `secrets.compare_digest`; token generation uses `secrets` module | ✅ Pass after fix — `_admin_auth` now uses `compare_digest`; team tokens generated with `secrets.token_hex` |
| Dockerfile: non-root `USER`; compose: services not published beyond loopback unless intentional | ✅ Pass after fix — `USER clearview` added; compose publishes `0.0.0.0:8000` which is expected for a gateway service |
| `pip-audit` CVEs with fix versions | ⚠️ See L-1 / L-2 — 7 CVEs in build-only tools, deferred |

---

## Positive Observations

- **Team token generation** uses `secrets.token_hex(16)` — cryptographically strong.
- **Conversation IDs** use `secrets.token_hex(8)` — not sequential or guessable.
- **Team token listing** redacts tokens to an 18-char prefix in admin list views, reducing leakage via admin logs.
- **SQL injection posture** is good: `WHERE` clauses are assembled from whitelisted string fragments; all user-supplied filter values are bound as parameters.
- **No `eval`, `exec`, `pickle`, `shell=True`** anywhere in the codebase.
- **No live secrets in tracked files** — API keys flow through environment variables only.
- **Budget enforcement** is consistently present on the primary request path (per-team daily/monthly + global daily caps).

---

## Remediation Status

| ID  | Description                         | Status            |
|-----|-------------------------------------|-------------------|
| H-1 | Dockerfile non-root USER            | ✅ Fixed, pushed   |
| M-1 | `_admin_auth` constant-time compare | ✅ Fixed, pushed   |
| M-2 | `/metrics` unauthenticated          | ⏳ Deferred        |
| L-1 | pip build-tool CVEs                 | ⏳ Deferred        |
| L-2 | setuptools build-tool CVE           | ⏳ Deferred        |
