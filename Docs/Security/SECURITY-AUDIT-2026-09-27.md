# Security Audit — 2026-09-27

**Auditor:** Automated daily audit (Claude Sonnet 4.6)
**Branch audited:** `main` @ `e8ad2e3`
**Audit branch:** `security/audit-2026-09-27`
**Prior report:** None (first audit)

---

## Severity Summary

| Severity | New | Fixed this run | Deferred |
|----------|-----|---------------|----------|
| High     | 0   | 0             | 0        |
| Medium   | 3   | 2             | 1        |
| Low      | 3   | 0             | 3        |
| Info     | 1   | 0             | 1        |

---

## Findings

### Medium

#### M-1 — Non-constant-time admin token comparison ✅ FIXED
- **File:** `app/main.py:301` (`_admin_auth`)
- **Risk:** `token != expected` uses Python's string equality, which short-circuits on the first mismatched byte. Under precise timing measurement an attacker could recover the admin token character-by-character (timing side-channel).
- **Remediation:** Changed to `secrets.compare_digest(token, expected)`. Added `import secrets` at line 14.
- **Status:** Fixed in this run. All 359 tests pass.

#### M-2 — Non-constant-time client key comparison ✅ FIXED
- **File:** `app/main.py:325` (`_client_key_allowed`)
- **Risk:** `cand in allowed` uses a Python set lookup (hash-then-compare), which also leaks timing information proportional to key length matching.
- **Remediation:** Changed to `any(secrets.compare_digest(cand, k) for k in allowed)`.
- **Status:** Fixed in this run. All 359 tests pass.

#### M-3 — Dockerfile runs as root — DEFERRED
- **File:** `Dockerfile`
- **Risk:** The image has no `USER` directive. If an attacker achieves RCE inside the container they get root, enabling trivial container escape (privileged sockets, `/proc/*/mem`, etc.).
- **Proposed fix:** Add a non-root service user. This run adds a `useradd -r -u 1001 appuser` block and `USER appuser` before `CMD`. Note: the VOLUME `/data` must be pre-created with correct ownership before the `USER` switch.
- **Status:** Fix applied to Dockerfile on this branch; deferred from "merge" pending operator testing that the named volume mounts correctly with the new UID, and that any CI/CD build steps that expect root access are updated. See Dockerfile for the proposed change.

---

### Low

#### L-1 — `/metrics` endpoint has no authentication gate — DEFERRED
- **File:** `app/main.py:2538` (`metrics`)
- **Risk:** Prometheus metrics expose tier/provider/status/team_id call counts, total cost, and output tokens without any token check. This leaks operational detail (which LLM providers are configured, call volumes, cost) to anyone who can reach the service.
- **Remediation (design-level):** Either add `_admin_auth(request)` to the handler (breaking unauthenticated Prometheus scrapers) or move to a separate internal port/path guarded by network policy. The trade-off is scraper compatibility vs. information disclosure. Recommended: add `CLEARVIEW_METRICS_TOKEN` env variable to gate the endpoint, defaulting to open for local dev (matching the pattern used by `CLEARVIEW_ADMIN_TOKEN`).
- **Status:** Deferred — behavior-changing; requires operator decision on scraper configuration.

#### L-2 — `/v1/models` has no authentication gate — DEFERRED
- **File:** `app/main.py:364` (`list_models`)
- **Risk:** Returns the full list of configured tiers and underlying provider model IDs to unauthenticated callers. This reveals what providers and models are available, which aids reconnaissance.
- **Remediation:** Add `_client_key_allowed(request)` or `_admin_auth(request)`. However, OpenAI SDK clients call `/v1/models` before authentication to enumerate models. Operator should decide based on network exposure.
- **Status:** Deferred — behavior-changing.

#### L-3 — Ollama service publishes to 0.0.0.0:11434 in local profile — DEFERRED
- **File:** `docker-compose.yml:22`
- **Risk:** When running `docker compose --profile local up`, the ollama service binds port 11434 on all network interfaces, not just loopback. Anyone on the same network can send arbitrary inference requests to the model without authentication.
- **Remediation:** Change ports to `"127.0.0.1:11434:11434"` unless the operator explicitly needs external access.
- **Status:** Deferred — behavior-changing for operators who rely on remote access to ollama.

---

### Info

#### I-1 — `cv_session` cookie missing `secure` flag — DEFERRED
- **File:** `app/main.py:2651` (`chat_login`)
- **Risk:** The session cookie is set without `secure=True`. Over HTTP, browsers will send it in plaintext, exposing team tokens to network eavesdroppers.
- **Remediation:** Add `secure=True` to `set_cookie`. Consider making it conditional on `request.url.scheme == "https"` so local HTTP dev still works.
- **Status:** Deferred — production vs. dev configuration decision needed.

---

## Minimum Sweep Results

| Check | Result |
|-------|--------|
| All `@app.get/post/delete` routes call an auth function or are documented as intentionally open | ✅ Pass — all admin routes call `_admin_auth`; all chat routes call `_require_chat_team`; `/v1/chat/completions`, `/v1/messages`, `/v1/responses`, gemini shims call `_client_key_allowed` + `_resolve_team`; `/v1/models`, `/health`, `/metrics`, `/feedback` are intentionally open (noted as L-1, L-2 above) |
| No `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, unsafe `yaml.load` | ✅ Pass — subprocess calls use `asyncio.create_subprocess_exec` (no shell); `yaml.safe_load` used throughout |
| SQL is parameterized — no f-string interpolation of user-controlled values | ✅ Pass — dynamic WHERE builders in `main.py` and `telemetry.py` interpolate only hardcoded column-name strings; all user-supplied values go through `?` parameters |
| No tracked secrets (env/key/pem files or live-looking keys) | ✅ Pass — only `.env.example` and `clearview-client.env.example` are tracked; no live `sk-`, `AKIA`, or `ghp_` keys found |
| Secret comparisons use `secrets.compare_digest` | ✅ Pass after fixes — M-1 and M-2 remediated |
| Token generation uses `secrets` module | ✅ Pass — `teams.py:117` uses `secrets.token_hex(16)` |
| Dockerfile has non-root USER | ⚠️ Applied fix on branch — see M-3 |
| docker-compose doesn't publish unauthenticated services beyond loopback | ⚠️ Ollama publishes to 0.0.0.0 under local profile — see L-3 |
| `pip-audit` — no CVEs | ✅ Pass — "No known vulnerabilities found" |

---

## Positive Observations

- Team tokens use `secrets.token_hex(16)` — cryptographically random 32-char hex.
- `yaml.safe_load` used in `config.py` — no arbitrary object deserialization.
- No `shell=True` or `os.system` anywhere; subprocess calls use `create_subprocess_exec`.
- SQL queries throughout use parameterized `?` placeholders; dynamic WHERE builders compose only from whitelisted hardcoded column names.
- `httponly=True`, `samesite="lax"` on the session cookie — XSS-resistant and CSRF-resistant for same-origin requests.
- `/admin/teams` list view redacts the full Bearer token to a short prefix, preventing accidental exposure via logs or screenshots.

---

## Remediation Status

| Finding | Status |
|---------|--------|
| M-1 non-constant-time admin token | ✅ Fixed (`secrets.compare_digest`) |
| M-2 non-constant-time client key | ✅ Fixed (`secrets.compare_digest`) |
| M-3 Dockerfile root user | ⚠️ Fix on branch — deferred merge |
| L-1 `/metrics` no auth | Deferred |
| L-2 `/v1/models` no auth | Deferred |
| L-3 ollama 0.0.0.0 | Deferred |
| I-1 cookie missing `secure` | Deferred |
