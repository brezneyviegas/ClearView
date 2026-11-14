# Security Audit — 2026-11-14

**Repository:** ClearView (Python/FastAPI LLM gateway)  
**Audit date:** 2026-11-14  
**Branch audited:** main (HEAD `e8ad2e3`)  
**Auditor:** Automated daily audit (Claude Code scheduled routine)  
**Previous report:** None (inaugural audit)

---

## Severity Summary

| Severity | New | Fixed this run | Deferred | Total open |
|----------|-----|---------------|----------|------------|
| High     | 0   | 0             | 0        | 0          |
| Medium   | 1   | 1             | 0        | 0          |
| Low      | 3   | 1             | 2        | 2          |
| CVE      | 15  | 0             | 15       | 15         |

---

## Findings

### Medium

#### M-1 — Non-constant-time admin token comparison (TIMING ATTACK)

| Field | Value |
|-------|-------|
| File | `app/main.py:301` |
| Function | `_admin_auth` |
| Risk | A remote attacker can recover `CLEARVIEW_ADMIN_TOKEN` byte-by-byte via timing side-channel. The gateway exposes write-capable admin routes (tune/apply, delete teams, revert policy). |
| Remediation | Replace `token != expected` with `secrets.compare_digest(token, expected)`. |
| **Status** | **Fixed in this PR** |

The comparison `if token != expected:` uses CPython's short-circuit string equality, which exits early on the first differing byte — exposing a timing oracle across HTTP round-trips on a co-located attacker or low-latency network.

---

### Low

#### L-1 — Dockerfile runs as root (no non-root USER)

| Field | Value |
|-------|-------|
| File | `Dockerfile` |
| Risk | Container process runs as UID 0. If the application is compromised (e.g. via a path-traversal in LiteLLM or a crafted LLM provider response), the attacker gains root within the container, reducing containment. |
| Remediation | Add `RUN addgroup --system appuser && adduser --system --ingroup appuser appuser` and `USER appuser` before `CMD`. Ensure the `/data` volume mount path is writable by the non-root user. |
| **Status** | **Fixed in this PR** |

#### L-2 — `/metrics` endpoint unauthenticated

| Field | Value |
|-------|-------|
| File | `app/main.py:2538` |
| Risk | The Prometheus `/metrics` endpoint is open with no auth gate. It exposes per-team spend totals (label `team_id`), provider names, tier routing, and cumulative cost counters to any network-reachable client. This is information-disclosure only (no write capability), but can aid targeted attacks. |
| Remediation | Add an optional `CLEARVIEW_METRICS_TOKEN` env var gate following the same pattern as `_admin_auth`, or document an intentional decision to leave it open (Prometheus scraping typically uses network controls). |
| **Status** | **Deferred** — requires design decision on whether to match `_admin_auth` or rely on network-layer controls (e.g. firewall, Prometheus scrape network). |

#### L-3 — docker-compose binds port 8000 on 0.0.0.0

| Field | Value |
|-------|-------|
| File | `docker-compose.yml:8` |
| Risk | `ports: - "8000:8000"` publishes on all host interfaces by default. When `CLEARVIEW_ADMIN_TOKEN` is unset (the default dev mode), admin routes (`/admin/*`, policy tune/revert, team CRUD) are open to any host on the LAN or Docker network. |
| Remediation | Change to `"127.0.0.1:8000:8000"` for local dev. For multi-host deployments, document that `CLEARVIEW_ADMIN_TOKEN` must be set. |
| **Status** | **Deferred** — this is intentional for the Docker single-command-setup UX. Mitigation: operators must set `CLEARVIEW_ADMIN_TOKEN` in production. Added a prominent note to `.env.example`. |

---

### CVE Findings (pip-audit — build/tooling packages only)

All 15 CVEs are in **build-environment packages** (`pip 24.0`, `setuptools 79.0.1`). None are in production runtime dependencies.

| Package | Current | CVE IDs | Fix version | Risk in this repo |
|---------|---------|---------|-------------|-------------------|
| pip | 24.0 | CVE-2025-8869, CVE-2026-1703, CVE-2026-3219, CVE-2026-6357, CVE-2026-8643, CVE-2026-13346 (×2 dedupe) | 26.2.0 | Low — only exploitable when `pip install`ing from a malicious index |
| setuptools | 79.0.1 | CVE-2026-59890 (GHSA-h35f-9h28-mq5c), GHSA-xmj3-9gf7-h52w | 84.1.0 | Low — CVE-2026-59890 is macOS APFS Unicode normalization only; GHSA-xmj3-9gf7-h52w is Windows wheel install path traversal |

**Recommendation:** Upgrade `pip` and `setuptools` in the dev environment / CI image; production containers do not expose these build-tool attack surfaces directly.

---

## Deferred Items (carried forward)

| ID | Item | Deferred reason |
|----|------|----------------|
| D-1 | `/metrics` auth gate (L-2) | Requires design: either add `CLEARVIEW_METRICS_TOKEN` env gate, or document network-layer mitigation. |
| D-2 | docker-compose LAN exposure (L-3) | Intentional dev UX; mitigation note added to `.env.example`. Operator responsibility. |
| D-3 | pip/setuptools CVEs | Build-environment only; upgrade pip in dev/CI environment. Not a production runtime risk. |

---

## Positive Observations

- Token generation uses `secrets.token_hex` throughout (`app/chat.py:62`, `app/teams.py:117`). ✓
- All SQL queries use parameterized placeholders (`?`) — no f-string interpolation of user-controlled values into query structure. ✓
- All `/admin/*` routes call `_admin_auth`. ✓
- No tracked secrets: no `.env`, `.pem`, `.key` files committed; no live API keys (`sk-`, `AKIA`, `ghp_`) in tracked files. ✓
- No dangerous code patterns: `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, `yaml.load` — none found. ✓
- Session cookie set with `HttpOnly=True`, `SameSite=lax` — mitigates XSS session hijacking. ✓
- Team token listing redacts to 18-char prefix only; full token returned only at creation. ✓
- `/feedback` is intentionally unauthenticated with inline documentation justifying the design. ✓
- `/health` is intentionally unauthenticated (health check probes require it). ✓
- Budget enforcement is present (per-team daily/monthly + global daily caps). ✓

---

## Remediation Status

| Finding | Action | Commit |
|---------|--------|--------|
| M-1 (timing attack on admin token) | Fixed: `secrets.compare_digest` in `_admin_auth` | This PR |
| L-1 (Dockerfile root) | Fixed: added `USER appuser` | This PR |
| L-2 (metrics auth) | Deferred | — |
| L-3 (docker-compose 0.0.0.0) | Deferred | — |
| CVEs (pip/setuptools) | Deferred | — |
