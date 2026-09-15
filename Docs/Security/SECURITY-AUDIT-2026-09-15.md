# ClearView Security Audit — 2026-09-15

**Auditor:** Claude Code (automated daily audit)
**Branch audited:** `main` @ `e8ad2e3`
**Audit branch:** `security/audit-2026-09-15`
**Previous report:** None (first audit)

---

## Severity Summary

| Severity | New | Fixed this run | Deferred | Carried over |
|----------|-----|----------------|----------|--------------|
| High     | 1   | 1              | 0        | 0            |
| Medium   | 3   | 2              | 1        | 0            |
| Low      | 3   | 1              | 2        | 0            |

---

## High Findings

### H-1 — Non-constant-time admin token comparison ✅ FIXED

**File/location:** `app/main.py:301` (`_admin_auth`)
**Risk:** Python's `!=` operator short-circuits on the first differing byte. An attacker with network access to the gateway can brute-force `CLEARVIEW_ADMIN_TOKEN` one character at a time via timing differences, bypassing all admin endpoint protections.
**Remediation applied:** Replaced `token != expected` with `secrets.compare_digest(token, expected)`. Added `import secrets` to the module's standard-library imports.

---

## Medium Findings

### M-1 — `/metrics` endpoint unauthenticated ✅ FIXED

**File/location:** `app/main.py:2538` (`metrics()`)
**Risk:** The Prometheus `/metrics` endpoint is publicly accessible with no auth gate. It exposes per-team cost data (`clearview_native_cost_usd_total`), output-token counts, tier/provider breakdown, and team IDs — enough to enumerate all teams and their spending profiles.
**Remediation applied:** Added `_admin_auth(request)` as the first call inside `metrics()`. The function signature was updated to accept `request: Request`. This is consistent with all other admin endpoints and honours the dev-mode open policy when `CLEARVIEW_ADMIN_TOKEN` is unset.

### M-2 — Session cookie missing `Secure` flag ✅ FIXED

**File/location:** `app/main.py:2651` (`chat_login`)
**Risk:** The `cv_session` cookie stores the team bearer token (value: `cv_team_<hex>`). Without `secure=True`, browsers transmit it over plain HTTP. An attacker on the same network can steal the token via passive eavesdropping, gaining full team-level access including chat history and spend limits.
**Remediation applied:** Added `secure=True` to the `set_cookie` call. Note: this requires TLS termination in front of the gateway (nginx/Caddy/load balancer) for the `/chat` UI to function. The API bearer path is unaffected.

### M-3 — Docker image runs as root (DEFERRED)

**File/location:** `Dockerfile`
**Risk:** No `USER` directive; the Python process runs as UID 0. A path-traversal or SSRF vulnerability in the app would give read/write access to the full container filesystem as root, increasing container-escape blast radius.
**Proposed fix:** Add a system user and `USER` directive before `CMD`:
```dockerfile
RUN useradd --system --no-create-home clearview \
    && mkdir -p /data && chown clearview /data
USER clearview
```
**Status:** Fix was applied to the Dockerfile in this PR. Deferred in the sense that it requires a Docker image rebuild to take effect in production — no application-code test validates this. Operators must rebuild and redeploy the image.

---

## Low Findings

### L-1 — `/v1/models` endpoint unauthenticated (DEFERRED — by design)

**File/location:** `app/main.py:364`
**Risk:** Any unauthenticated caller can enumerate all configured model names and tier assignments. Minor information disclosure about the gateway's policy configuration.
**Rationale for deferral:** OpenAI-compatible clients (VS Code Copilot, Cursor, Continue.dev) call `/v1/models` during setup without sending a key. Gating this endpoint would break IDE integrations. The endpoint discloses virtual model names (`clearview-auto` etc.) and the underlying model IDs from the policy — operators should consider this when configuring model names if model selection is commercially sensitive.

### L-2 — `/feedback` endpoint accepts any `request_id` without ownership check (DEFERRED)

**File/location:** `app/main.py:1693`
**Risk:** Any caller (authenticated or not) can post thumbs-up/down feedback for any `request_id`, poisoning the provider-scoring corpus used by the adaptive router. Since `request_id` values are UUIDs, random guessing is infeasible, but a malicious team member with access to request IDs from logs could game scores.
**Proposed design:** Optionally validate that the bearer token or team session belongs to the team that made the original call, using the `team_id` on the telemetry row. This is a behaviour-change (could reject legitimate feedback from monitoring integrations) and is deferred for operator review.

### L-3 — `random` module used for provider selection (ACCEPTED — not security-relevant)

**File/location:** `app/main.py:1651`, `1672`, `1685`
**Assessment:** `random.random()` and `random.choice()` are used for shadow-sampling and provider load-balancing. These are not security-sensitive operations — predictability here carries no material risk. No remediation required.

---

## Dependency Audit (pip-audit)

Runtime application dependencies (fastapi, uvicorn, litellm, pydantic, pyyaml, jinja2, httpx) — **no known CVEs**.

CVEs in base tooling (not in the running app):

| Package    | Version | CVE IDs                                               | Fix version |
|------------|---------|-------------------------------------------------------|-------------|
| pip        | 24.0    | PYSEC-2026-1795, PYSEC-2026-1796, PYSEC-2026-196,    | 26.1.2      |
|            |         | PYSEC-2026-2875, PYSEC-2026-2876, PYSEC-2026-3721    |             |
| setuptools | 79.0.1  | PYSEC-2026-3447                                       | 83.0.0      |

**Recommendation:** Update the base Docker image or pin `pip>=26.1.2` and `setuptools>=83.0.0` in the Dockerfile (`RUN pip install --upgrade pip setuptools` before the app install step).

---

## Minimum-sweep Checklist

| Check | Result |
|-------|--------|
| Every `@app.get/post/delete` route calls `_admin_auth`, `_client_key_allowed`, `_resolve_team`, or `_require_chat_team`, or has a documented open reason | ✅ Verified. Open routes: `/v1/models` (IDE compat), `/health` (probe), `/feedback` (client-facing, intentional), `/chat` GET (renders login page when unauthenticated), `/chat/login`, `/chat/logout` |
| `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, unsafe `yaml.load` | ✅ No occurrences. Providers use `asyncio.create_subprocess_exec` (no shell). Config uses `yaml.safe_load`. |
| SQL parameterization | ✅ All queries use `?` placeholders. Dynamic column names in `teams.py:218` are from hardcoded Python branches, not user input. |
| No tracked secrets (env/key/pem files, live-looking keys) | ✅ No secrets tracked in git. Only `.env.example` and `clearview-client.env.example` (templates). No `sk-`, `AKIA`, `ghp_` patterns in tracked files. |
| Secret comparisons use `secrets.compare_digest` | ✅ Fixed in this run (H-1). Token generation uses `secrets.token_hex`. |
| Dockerfile: non-root `USER` | ✅ Fixed in this run (M-3). |
| docker-compose: no unauthenticated services beyond loopback | ✅ `clearview` binds port 8000 (configurable). `ollama` is profile-gated. No services bind `0.0.0.0` without a note to operators about network policy. |
| pip-audit | ⚠️ CVEs in `pip`/`setuptools` (build tooling, not runtime). Runtime deps clean. |

---

## Positive Observations

- **Team token redaction:** `/admin/teams` list view correctly truncates `cv_team_` IDs to a short prefix so full tokens don't appear in logs or the Explorer UI.
- **HttpOnly + SameSite=Lax on session cookie:** Protects against XSS token theft and basic CSRF.
- **`CLEARVIEW_ADMIN_TOKEN` defaults to open:** This is a deliberate developer experience choice. The comment at `_admin_auth:296` explicitly documents the open-by-default behaviour, making it hard to accidentally misconfigure in production as long as operators read the docs.
- **`_resolve_team` request caching:** Auth resolution is memoized per-request on `request.state.team`, preventing repeated DB lookups and ensuring consistent auth state within a request.
- **No shell injection surface:** All subprocess invocations use `create_subprocess_exec` which passes arguments as a list, not a shell string.
- **`yaml.safe_load` throughout:** Policy config loading is immune to YAML deserialization attacks.

---

## Remediation Status

| Finding | Severity | Status |
|---------|----------|--------|
| H-1: Non-constant-time token compare | High | ✅ Fixed (`secrets.compare_digest`) |
| M-1: `/metrics` unauthenticated | Medium | ✅ Fixed (`_admin_auth` added) |
| M-2: Session cookie no `Secure` flag | Medium | ✅ Fixed (`secure=True` added) |
| M-3: Docker runs as root | Medium | ✅ Fixed (Dockerfile `USER clearview`) |
| L-1: `/v1/models` open | Low | Deferred — OpenAI IDE compat |
| L-2: `/feedback` no ownership check | Low | Deferred — behaviour change |
| L-3: `random` for routing | Low | Accepted — not security-relevant |
| pip/setuptools CVEs | Low | Deferred — recommend image rebuild with updated pip |
