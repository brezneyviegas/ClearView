# Security Audit — 2026-06-10

Scope: full codebase (`app/`, `app/providers/`, `eval/`, Dockerfile, docker-compose.yml, env handling).
Method: manual review of auth paths, SQL construction, subprocess usage, secrets handling, container config, dependency surface.

## Summary

| Severity | Count |
|----------|-------|
| High     | 2     |
| Medium   | 6     |
| Low      | 4     |

No critical findings (no injection, no secret leakage into git, no shell=True, no unsafe deserialization). SQL is consistently parameterized; CLI providers use exec-form subprocess with prompts via stdin; `.env` is gitignored and untracked.

---

## High

### H1 — `/metrics` endpoint is unauthenticated
`app/main.py` (`metrics()`): exposes per-team request counts, team IDs, native/baseline costs, and token totals with no auth check, even when `CLEARVIEW_ADMIN_TOKEN` is set. Every other admin-grade read is gated by `_admin_auth`.
**Risk:** information disclosure of usage/spend/tenant data to anyone who can reach the port (Docker image binds 0.0.0.0).
**Remediation:** apply `_admin_auth` to `/metrics` (Prometheus scrapers support bearer auth via `authorization` config).

### H2 — Unauthenticated `/feedback` can manipulate routing
`app/main.py` (`post_feedback`): no auth, no rate limit, and the rating validation `rating not in (1,-1,...) and not isinstance(rating, (int, float))` accepts ANY numeric (e.g. `1000`). Feedback feeds `record_provider_outcome`, which drives provider-learning scores → an unauthenticated caller who can guess/observe a `request_id` can poison routing decisions.
**Risk:** routing manipulation, telemetry pollution.
**Remediation:** clamp rating to exactly ±1; require the `request_id` to exist before recording; apply `_client_key_allowed` so the gateway lock (when configured) also covers feedback.

---

## Medium

### M1 — Admin token compared with `!=` (timing side-channel)
`app/main.py` (`_admin_auth`): `token != expected` is not constant-time.
**Remediation:** `secrets.compare_digest(token, expected)`.

### M2 — Open-by-default posture + Docker binds 0.0.0.0
Admin endpoints, client traffic, and chat are all open when `CLEARVIEW_ADMIN_TOKEN` / `CLEARVIEW_CLIENT_KEYS` are unset. Reasonable for localhost dev, but the Docker image sets `CLEARVIEW_HOST=0.0.0.0` and compose publishes `8000:8000` — a default `docker compose up` exposes an unauthenticated gateway (that spends your API keys) to the LAN.
**Remediation:** log a prominent startup warning when host is 0.0.0.0 and no admin token/client keys are configured; document the hardening envs in README/.env.example.

### M3 — Container runs as root
`Dockerfile` has no `USER` directive.
**Remediation:** create and switch to a non-root user; ensure `/data` is writable by it.

### M4 — Team tokens stored in plaintext as primary keys
`app/teams.py`: `cv_team_<hex>` bearer tokens are the row IDs in SQLite. Any read of the DB file (backup, volume snapshot, path traversal elsewhere) yields valid credentials.
**Remediation (larger change, not in this PR):** store SHA-256 of the token, look up by hash, show the plaintext only at creation time.

### M5 — No rate limiting on `/chat/login`
Token brute-force is impractical (128-bit tokens) but unthrottled guessing is free and unlogged.
**Remediation:** basic per-IP throttle or failed-attempt logging.

### M6 — Session cookie lacks `Secure` flag
`/chat/login` sets `cv_session` with `httponly` + `samesite=lax` but never `secure`, so the raw team token transits cleartext HTTP if deployed behind TLS-terminating proxies inconsistently.
**Remediation:** `CLEARVIEW_COOKIE_SECURE=1` env to opt in (default off for localhost dev).

---

## Low

### L1 — Unbounded request body
`await request.json()` everywhere; uvicorn does not cap body size → memory-exhaustion vector.
**Remediation:** lightweight max-body middleware (generous default, e.g. 10 MB, env-tunable) — IDE clients send large contexts.

### L2 — Compose publishes ollama to the host network
`docker-compose.yml` maps `11434:11434`; ollama has no auth. Only ClearView needs to reach it inside the compose network.
**Remediation:** bind publish to loopback (`127.0.0.1:11434:11434`) so `ollama pull` from the host still works without LAN exposure.

### L3 — No dependency vulnerability scanning
No `pip-audit`/Dependabot/CI audit step. Current pins (fastapi 0.136.1, litellm 1.83.14, urllib3 2.7.0, jinja2 3.1.6) had no known issues checked at audit time, but nothing enforces this.
**Remediation:** add `pip-audit` to CI / the daily audit skill.

### L4 — Refusal-marker escalation is spoofable
`_looks_refusal_or_too_short` retries on substring markers — a prompt that instructs the model to emit "I can't" forces tier escalation (cost amplification). Minor; budget caps bound the damage.

---

## Positive observations

- All SQL uses parameter binding; the two f-string queries (`telemetry.feedback_summary`, `teams.update`) interpolate only whitelisted column fragments, never user input.
- CLI providers (`claude_cli`, `codex_cli`, `gemini_cli`) use `subprocess` exec form with fixed argv; prompts go via stdin — no shell injection surface.
- `.env` is gitignored and not tracked; `.env.example` contains no real secrets.
- `secrets.token_hex` used for token generation (CSPRNG); `random` only for shadow sampling (non-security).
- Prompt cache keys include `team_id` → no cross-tenant cache poisoning.
- Jinja2Templates autoescaping on (default) for HTML surfaces.

## Remediation status

PR with fixes for H1, H2, M1, M2, M3, M6, L1, L2: see branch `security/audit-2026-06-10`.
Deferred (need design discussion): M4 (token hashing at rest), M5 (rate limiting), L3 (CI audit step — partially covered by daily audit skill), L4.
