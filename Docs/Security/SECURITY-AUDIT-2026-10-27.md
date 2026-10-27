# ClearView Security Audit — 2026-10-27

**Auditor:** Automated daily audit (Claude Code scheduled task)
**Scope:** Full repo sweep + code changed since first audit (initial run — no prior report)
**Branch reviewed:** `main` @ `e8ad2e3`

---

## Severity Summary

| Severity | Count | Status |
|----------|-------|--------|
| High     | 0     | —      |
| Medium   | 3     | 2 fixed, 1 deferred |
| Low      | 2     | 1 fixed (constraint pinned), 1 deferred |

---

## Findings

### Medium — Non-constant-time admin token comparison

**File/Location:** `app/main.py:302` — `_admin_auth()`

**Risk:** The admin token comparison `token != expected` uses Python's default
string `!=`, which short-circuits on the first mismatched byte. An attacker
with many requests and precise timing measurements could infer the correct
`CLEARVIEW_ADMIN_TOKEN` character-by-character (timing oracle). In practice the
noise from network RTT makes this hard to exploit remotely, but it is a
unnecessary vulnerability.

**Remediation:** Replace with `hmac.compare_digest(token, expected)`.

**Status: FIXED** — `import hmac` added; comparison changed to
`hmac.compare_digest` in this PR.

---

### Medium — Dockerfile runs container as root (no USER directive)

**File/Location:** `Dockerfile`

**Risk:** The image had no `USER` instruction, so the Python process runs as
UID 0 inside the container. If the application is compromised or if a
dependency vulnerability allows arbitrary code execution, the attacker
gains root privileges inside the container, making privilege escalation and
lateral movement significantly easier.

**Remediation:** Add a non-root `appuser` and switch to it before the `CMD`.

**Status: FIXED** — `adduser appuser`, `chown appuser /data`, and `USER appuser`
added to Dockerfile in this PR.

---

### Medium — filelock CVEs (indirect dependency via litellm)

**Package:** `filelock 3.32.7`
**CVEs:** CVE-2026-112940, CVE-2026-112913, CVE-2026-112907
**Fix version:** 4.2.1

**Risk:** Three vulnerabilities in `filelock`, which is pulled in transitively
by `litellm` (and `huggingface_hub`). ClearView uses litellm for upstream
provider dispatch; filelock is used for litellm's internal caching. The CVEs
have not been publicly detailed yet (2026 advisories), but all are fixed in
4.2.1.

**Remediation:** Pin `filelock>=4.2.1` as a direct dependency so pip resolves
a fixed version regardless of what litellm requests.

**Status: FIXED** — `"filelock>=4.2.1"` added to `pyproject.toml` in this PR.

---

### Low — docker-compose publishes ClearView to all interfaces (0.0.0.0)

**File/Location:** `docker-compose.yml:9` — `ports: "8000:8000"`

**Risk:** The default Docker port-binding publishes to 0.0.0.0, making the
gateway reachable on every host network interface. When `CLEARVIEW_ADMIN_TOKEN`
and `CLEARVIEW_CLIENT_KEYS` are unset (the default dev-mode), both the admin
and completions endpoints are unauthenticated and accessible from the LAN or
internet (depending on firewall rules). This is intentional for local
development but dangerous if deployed on a cloud VM.

**Remediation (proposed — deferred, design decision):** Change the docker-compose
port binding to `127.0.0.1:8000:8000` for the default profile, and document
clearly that production deployments must set `CLEARVIEW_ADMIN_TOKEN` and
`CLEARVIEW_CLIENT_KEYS`. Alternatively, add a startup check that warns loudly
when both are unset and the bind address is not loopback-only.

**Status: DEFERRED** — Behavior change; requires operator documentation update.
Tracked as a follow-up hardening item.

---

### Low — pip and setuptools CVEs (dev/build-time tooling)

**Packages:** `pip 24.0` (PYSEC-2026-1795/1796/2875/2876/196/3721),
`setuptools 79.0.1` (PYSEC-2026-3447, GHSA-xmj3-9gf7-h52w)
**Fix versions:** pip ≥ 26.2.2, setuptools ≥ 84.1.0

**Risk:** These tools are used only during `pip install` at image build time.
They are not present in the running container's Python environment and do not
affect runtime behavior. The CVEs relate to package installation security (e.g.
path traversal, hash mismatch bypass).

**Remediation (proposed — deferred):** Add a `RUN pip install --upgrade pip setuptools`
step to the Dockerfile before the application install, so the build environment
uses patched versions. Low urgency because they're not runtime dependencies.

**Status: DEFERRED** — Low runtime risk; recommended as a follow-up Dockerfile
improvement.

---

## Minimum-sweep Checklist

| Check | Result |
|-------|--------|
| All `@app.get/post/delete` routes call `_admin_auth`, `_client_key_allowed`, `_resolve_team`, or `_require_chat_team`, or are intentionally open (see note) | PASS (see below) |
| `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, unsafe `yaml.load` | NONE FOUND |
| SQL parameterized (no f-string/concat with user values) | PASS |
| No tracked `.env`, `.pem`, `.key` files; no live-looking keys (`sk-`, `AKIA`, `ghp_`) | PASS (test fixtures with `sk-x`/`sk-test` are expected non-real values) |
| Secret comparisons use constant-time digest | FIXED (admin token); team tokens looked up by DB index, not direct compare |
| Token generation uses `secrets` module | PASS — `secrets.token_hex` used in `teams.py` and `chat.py` |
| Dockerfile has non-root USER | FIXED |
| docker-compose does not publish unauthenticated services beyond loopback | DEFERRED (see Medium finding above) |
| pip-audit: application dependencies | 3 CVEs in filelock — FIXED; pip/setuptools build-time only — DEFERRED |

**Open route note:** The following routes are intentionally open (no auth gate):

| Route | Reason |
|-------|--------|
| `GET /v1/models` | Public model listing; no sensitive data |
| `POST /v1/chat/completions` | Client key gate via `_client_key_allowed` + team resolution |
| `POST /v1/messages` | Same — routes through `_handle_chat_completions` |
| `POST /v1/responses` | Same |
| `POST /v1beta/models/{model}/generateContent` | Same |
| `POST /v1/models/{model}/generateContent` | Same |
| `POST /feedback` | Intentionally open — callers rate their own responses |
| `GET /health` | Health check; no data |
| `GET /metrics` | Prometheus scrape endpoint; exposes cost/call counts — operator should firewall in production |
| `GET /chat` | Unauthenticated page load; shows login prompt; no data |
| `POST /chat/login` | Login handler; returns only team name on success |
| `POST /chat/logout` | Clears cookie; no sensitive action |

**Note on `/metrics`:** This endpoint returns aggregated cost and token counts
by tier/provider without auth. In a multi-tenant deployment where customers
share a ClearView instance, this could leak aggregate usage information across
teams. Consider gating with `_admin_auth` if the instance is shared. Noted for
follow-up.

---

## Positive Observations

- Team token generation uses `secrets.token_hex(16)` — cryptographically random.
- Conversation IDs use `secrets.token_hex(8)` — no sequential guessing.
- Team tokens are redacted in list responses (only 18-char prefix shown).
- Budget enforcement prevents runaway spend per team and globally.
- All admin routes consistently call `_admin_auth` first.
- SQL queries use parameterized `?` placeholders throughout; f-strings only
  inject static WHERE clause fragments, not user values.
- No `shell=True` subprocess calls; all subprocess usage is `create_subprocess_exec`.
- `.env` and key files are gitignored; no secrets tracked.
- Admin cookie is `HttpOnly` and `SameSite=Lax`, limiting XSS exposure.

---

## Deferred Items (carry forward)

| ID | Severity | Description |
|----|----------|-------------|
| D-2026-10-27-1 | Low | docker-compose port binding / unauthenticated dev default — needs design decision |
| D-2026-10-27-2 | Low | pip/setuptools CVEs — add upgrade step to Dockerfile |
| D-2026-10-27-3 | Low | `/metrics` exposes cost data without auth — consider gating |
