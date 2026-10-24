# Security Audit — 2026-10-24

**Auditor:** Automated daily routine (Claude)
**Branch audited:** `main` (HEAD: `e8ad2e3`)
**Baseline:** First audit — no prior report to carry over.

---

## Severity Summary

| Severity | New findings | Fixed this run | Deferred |
|----------|-------------|----------------|----------|
| High     | 1           | 1              | 0        |
| Medium   | 3           | 1              | 2        |
| Low      | 3           | 0              | 3        |

---

## High Findings

### H-1 — Non-constant-time admin token comparison (FIXED)

**Location:** `app/main.py:301` — `_admin_auth()`

**Risk:** Python's `!=` operator short-circuits on the first differing byte, enabling
a timing oracle: an attacker who can make many requests and measure latency can
recover `CLEARVIEW_ADMIN_TOKEN` one byte at a time. All `/admin/*` endpoints and the
`/admin/tune?apply=1` mutation path are gated here.

**Remediation applied:** Replaced `if token != expected` with
`if not secrets.compare_digest(token, expected)`. Also added `import secrets` to
the module imports. **Tests pass.** (See PR diff.)

---

## Medium Findings

### M-1 — Dockerfile runs as root (FIXED)

**Location:** `Dockerfile` (no `USER` directive)

**Risk:** The container process runs as UID 0. A vulnerability in FastAPI, litellm, or
any provider subprocess that allows OS command execution would have root-level access
inside the container, trivially breaking out of many hardening layers.

**Remediation applied:** Added `RUN mkdir -p /data && chown nobody:nogroup /data`
(before the `VOLUME` declaration so the mount point is pre-owned) and `USER nobody`
before `CMD`. **Tests pass.**

### M-2 — docker-compose binds on all interfaces (DEFERRED)

**Location:** `docker-compose.yml:8` — `ports: - "8000:8000"`

**Risk:** On a multi-homed host or a VM with a routable interface, ClearView is
reachable from any network that can reach the host. When `CLEARVIEW_ADMIN_TOKEN` is
unset (dev default), all `/admin/*` endpoints are open to the LAN.

**Proposed design:** Change to `"127.0.0.1:8000:8000"` for single-machine dev; expose
a separate `production` compose profile that binds on a specific interface or uses a
reverse-proxy service. Deferred because the right bind address is deployment-specific
and requires operator sign-off.

### M-3 — `/metrics` endpoint unauthenticated (DEFERRED)

**Location:** `app/main.py:2538` — `@app.get("/metrics")`

**Risk:** The Prometheus scrape endpoint exposes per-tier/provider call counts,
aggregate cost data, team IDs (as label values), and token totals. No
`_admin_auth` gate. An unauthenticated caller can enumerate active providers,
infer usage patterns, and see team identifiers.

**Proposed design:** Add an optional `CLEARVIEW_METRICS_TOKEN` environment variable;
when set, require `Authorization: Bearer <token>` on `/metrics` (matching the
pattern used by `_admin_auth`). Deferred because adding auth here is a
behavior-changing operation (breaks existing Prometheus scrape configs) that
needs operator sign-off.

---

## Low Findings

### L-1 — filelock CVEs in dependency tree (DEFERRED)

**Source:** `pip-audit` output — `filelock` 3.32.7 (transitive, likely via litellm)

| CVE               | Fix version |
|-------------------|-------------|
| CVE-2026-112940   | 4.2.1       |
| CVE-2026-112913   | 4.2.1       |
| CVE-2026-112907   | 4.2.1       |

**Risk:** Unknown without CVE details available at audit time (NVD pages not yet
published). filelock is a cross-process file-locking library; typical CVEs in this
class involve lock bypass or path traversal. ClearView uses it indirectly via litellm
for model-weight caching, not for security-critical operations.

**Remediation:** Pin or constrain `filelock>=4.2.1` in `pyproject.toml` once the
upstream litellm constraint allows it. Deferred pending confirmation that
litellm supports filelock ≥ 4.2.1.

### L-2 — pip CVEs (build-time tool only) (DEFERRED)

**Source:** `pip-audit` — `pip` 24.0 (8 CVEs; fix versions 25.3 → 26.2.2)

**Risk:** The `pip` binary in the build environment may be vulnerable to malicious
wheel installation attacks during `docker build`. Does not affect the running
application — `pip` is not imported at runtime. Upgrade the base Python image or
run `pip install --upgrade pip` in the Dockerfile.

**Proposed design:** Add `RUN pip install --upgrade pip` as the first `RUN` in the
Dockerfile, or pin `FROM python:3.12-slim` to a digest that ships pip ≥ 26.2.2.

### L-3 — setuptools CVE (DEFERRED)

**Source:** `pip-audit` — `setuptools` 79.0.1 (CVEs PYSEC-2026-3447, GHSA-xmj3-9gf7-h52w; fix: 84.1.0)

**Risk:** setuptools is a build/packaging library. The GHSA advisory concerns
`pkg_resources`; if any dependency imports `pkg_resources` at runtime, this could
be exploitable. Severity depends on usage pattern.

**Proposed design:** Upgrade `setuptools` in the build environment; pin
`setuptools>=84.1.0` in `pyproject.toml` build requirements.

---

## Audit Checklist Results

| Check                              | Result                        |
|------------------------------------|-------------------------------|
| Auth coverage (all routes)         | PASS (see notes below)        |
| Injection: shell=True / os.system  | PASS                          |
| Injection: eval / exec / pickle    | PASS                          |
| Injection: yaml.load (non-safe)    | PASS                          |
| SQL parameterization               | PASS                          |
| Tracked secrets (env/key/pem)      | PASS — only .example files    |
| Live keys in tracked files         | PASS — none found             |
| Token generation uses `secrets`    | PASS                          |
| Token comparison constant-time     | **FAIL → FIXED** (H-1)        |
| Dockerfile non-root USER           | **FAIL → FIXED** (M-1)        |
| Compose loopback binding           | FAIL → DEFERRED (M-2)         |
| Dependencies (pip-audit)           | FAIL → DEFERRED (L-1, L-2, L-3) |

### Auth coverage notes

All routes meet the checklist requirement (each calls at least one of
`_admin_auth`, `_client_key_allowed`, `_resolve_team`, or `_require_chat_team`),
with the following intentionally-open endpoints documented here:

| Route                                        | Reason open                                     |
|----------------------------------------------|-------------------------------------------------|
| `GET /v1/models`                             | OpenAI-compatible model discovery; no secret data |
| `GET /health`                                | Liveness probe; no secret data                  |
| `POST /feedback`                             | Client rates own responses by request_id handle |
| `GET /metrics`                               | Prometheus scrape target — flagged as M-3       |
| `GET /chat`                                  | Serves HTML login page; no data                 |
| `POST /chat/login`                           | Login endpoint; validates token before session  |
| `POST /chat/logout`                          | Clears session cookie; no secret data           |

`subprocess.create_subprocess_exec` (not `shell=True`) is used in all four
provider CLI adapters — no shell injection surface.

---

## Positive Observations

- All team token IDs minted via `secrets.token_hex(16)` (teams.py:117) — cryptographically random.
- Conversation IDs minted via `secrets.token_hex(8)` (chat.py:62) — cryptographically random.
- SQL in timeseries, calls_detail, shadow_verdicts, and routing_quality endpoints uses
  f-strings only for structural SQL (column names, fixed clauses); all user-supplied
  values (session_id, team_id, timestamps) are in the parameterized `params` tuple.
- `_resolve_team` caches resolved team on `request.state` — avoids double-DB-hit and
  ensures a single authoritative lookup per request.
- Admin listing redacts team Bearer tokens to a short prefix (`_team_to_redacted_dict`)
  — prevents token leakage via logs or shoulder-surfing the explorer UI.
- `.gitignore` excludes `.env`, `*.db`, `*.pem` — no accidental secret tracking.

---

## Remediation Status

| Finding | Status     | Commit / Notes                              |
|---------|------------|---------------------------------------------|
| H-1     | **Fixed**  | `secrets.compare_digest` in `_admin_auth`   |
| M-1     | **Fixed**  | `USER nobody` + `/data` chown in Dockerfile |
| M-2     | Deferred   | Needs deployment-specific bind address      |
| M-3     | Deferred   | Auth gate would break existing scrape jobs  |
| L-1     | Deferred   | Pin `filelock>=4.2.1` after litellm compat check |
| L-2     | Deferred   | Upgrade pip in Docker build layer           |
| L-3     | Deferred   | Upgrade setuptools to ≥84.1.0              |
