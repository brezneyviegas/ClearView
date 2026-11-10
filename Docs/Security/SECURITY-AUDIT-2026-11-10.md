# ClearView Security Audit — 2026-11-10

**Auditor:** Automated daily audit (Claude Code)
**Branch audited:** `main` → remediation branch `security/audit-2026-11-10`
**Prior report:** None (first audit)
**Audit scope:** Full codebase sweep (no prior baseline)

---

## Severity Summary

| Severity | Count | Fixed | Deferred |
|----------|-------|-------|----------|
| High     | 1     | 1     | 0        |
| Medium   | 2     | 1     | 1        |
| Low      | 1     | 0     | 1        |

---

## High Findings

### H-1 · Non-constant-time admin token comparison — **FIXED**

| Field | Value |
|-------|-------|
| File | `app/main.py:301` |
| Function | `_admin_auth` |

**Risk:** `token != expected` uses Python's built-in string equality, which short-circuits on the first mismatched byte. An attacker who can make rapid requests to any `CLEARVIEW_ADMIN_TOKEN`-protected endpoint can mount a timing side-channel attack to recover the token one character at a time.

**Remediation applied:** Replaced `token != expected` with `hmac.compare_digest(token, expected)` (stdlib `hmac`, imported at module top). `hmac.compare_digest` runs in constant time regardless of where strings differ.

---

## Medium Findings

### M-1 · Dockerfile runs container as root — **FIXED**

| Field | Value |
|-------|-------|
| File | `Dockerfile` |
| Lines | 1–21 |

**Risk:** The image has no `USER` directive, so the FastAPI process runs as `root` (UID 0) inside the container. If an attacker achieves remote code execution via a prompt-injection, deserialization, or dependency vulnerability, they gain root within the container. This violates least-privilege and makes container-escape harder to contain.

**Remediation applied:** Added `RUN mkdir -p /data && chown nobody:nogroup /data` followed by `USER nobody` before `VOLUME` / `EXPOSE`. The `/data` volume is pre-created and owned by `nobody` so the SQLite databases remain writable at runtime.

---

### M-2 · `/metrics` Prometheus endpoint unauthenticated — **DEFERRED**

| Field | Value |
|-------|-------|
| File | `app/main.py:2538` |
| Route | `GET /metrics` |

**Risk:** The Prometheus `/metrics` endpoint exposes per-team request counts, provider names, tier distribution, cumulative costs, and total token volume to any unauthenticated caller. An external observer can enumerate active teams (via `team_id` labels), infer provider configuration, and track spend — without any credentials.

**Why deferred:** Adding `_admin_auth` here would break existing Prometheus scrapers that may not carry the admin token. The safe fix requires either: (a) a dedicated `CLEARVIEW_METRICS_TOKEN` env var so scrape jobs can authenticate independently of the admin token, or (b) IP-allow-listing at the ingress/proxy layer. Neither can be applied without operator input on their scraping setup. Opening a design discussion is the appropriate next step.

**Proposed design:**
```
CLEARVIEW_METRICS_TOKEN=<dedicated scrape token>
```
`/metrics` checks this token (or the admin token as fallback) via `hmac.compare_digest`. Scrape jobs pass `--header 'Authorization: Bearer <metrics_token>'`. Document in README and docker-compose example.

---

## Low Findings

### L-1 · `setuptools` CVEs in build toolchain — **DEFERRED**

| Field | Value |
|-------|-------|
| Packages | `setuptools 79.0.1` |
| CVEs | `CVE-2026-59890` (fix: 83.0.0), `GHSA-xmj3-9gf7-h52w` (fix: 84.1.0) |

**Risk:** `CVE-2026-59890` — MANIFEST.in NFC/NFD exclusion bypass on macOS APFS/HFS+ could cause sensitive files to be included in published sdists. `GHSA-xmj3-9gf7-h52w` — Windows path traversal in legacy wheel installation. Neither affects the production runtime server; both affect the developer/CI build toolchain.

**Remediation:** Upgrade `setuptools` in development and CI environments to ≥ 84.1.0 (covers both CVEs). No runtime change required.

---

## Minimum-Sweep Results

| Check | Result |
|-------|--------|
| Every `@app.*` route calls `_admin_auth`, `_client_key_allowed`, `_resolve_team`, or `_require_chat_team` | Pass — with the noted exception of `/health` (intentionally open), `/v1/models` (intentionally open model listing), `/feedback` (intentionally open for client-side rating), and `/metrics` (see M-2) |
| `shell=True` / `os.system` / `eval(` / `exec(` / `pickle` / `yaml.load` | None found |
| SQL uses parameterized `?` placeholders (no f-string interpolation of user values) | Pass — all SQLite queries in `admin_timeseries`, `admin_calls_detail`, `admin_routing_quality`, etc. build `WHERE` clauses from fixed strings and pass user values only via `params` tuples |
| No tracked secret files (`.env`, `.key`, `.pem`) | Pass — only `.env.example` tracked |
| No live-looking API keys (`sk-`, `AKIA`, `ghp_`) in tracked files | Pass |
| Secret comparisons use constant-time compare | Fixed (H-1 above) |
| Token generation uses `secrets` module | Pass — `app/teams.py:117` uses `secrets.token_hex(16)` |
| Dockerfile has non-root USER | Fixed (M-1 above) |
| docker-compose does not publish unauthenticated services beyond intended scope | Pass — port `8000:8000` is intentional gateway exposure; `ollama:11434` is profile-gated and not exposed externally by default |
| `pip-audit` application CVEs | Only `setuptools` in build toolchain (see L-1); no runtime dependency CVEs |

---

## Positive Observations

- **Team token generation** uses `secrets.token_hex(16)` (cryptographically secure, 128 bits of entropy).
- **Team token listing** (`_team_to_redacted_dict`) redacts all but the first 18 characters — full tokens are only returned on creation.
- **Chat session cookies** are `HttpOnly`, `SameSite=Lax`, and scoped to `/`.
- **subprocess calls** in CLI providers all use `asyncio.create_subprocess_exec` (not `shell=True`), preventing shell-injection via model names or message content.
- **SQL construction** consistently uses parameterized queries; `WHERE` clauses are built from whitelisted column names, not user-controlled strings.
- **CLEARVIEW_ADMIN_TOKEN** defaults to open (dev mode) with clear log guidance via doctor, rather than a weak default password.
- No `eval(`, `exec(`, `pickle`, or unsafe `yaml.load` found anywhere in the codebase.

---

## Deferred Items Carried Forward

| ID | Description | Proposed action |
|----|-------------|-----------------|
| M-2 | `/metrics` unauthenticated | Add `CLEARVIEW_METRICS_TOKEN` with `hmac.compare_digest` check; document scraper config |
| L-1 | `setuptools` build-toolchain CVEs | Upgrade to ≥ 84.1.0 in dev/CI |

---

*Report generated by automated daily audit. Never includes `.env` contents or database token values.*
