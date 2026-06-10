---
name: daily-security-audit
description: Run a security audit of the ClearView codebase, write a dated findings report, and open a remediation PR. Use when the user says "/daily-security-audit", "run the security audit", or when invoked by the scheduled daily routine.
---

# Daily Security Audit

Audit the codebase, report findings to a dated file, and open a remediation PR for safe fixes.

## Procedure

1. **Establish baseline.** Read the most recent report in `Docs/Security/` (sorted by date). Re-check its deferred findings first — if any were fixed since, note them as resolved. Do not re-report accepted/deferred findings as new; track them in a "carried over" section.

2. **Scan for changes.** `git log --oneline <last-audit-date>..HEAD` and `git diff --stat`. Focus deep review on files changed since the last audit; do a lighter sweep of the rest.

3. **Audit checklist** (minimum, every run):
   - **Auth coverage:** every `@app.get/post/delete` in `app/main.py` must call `_admin_auth`, `_client_key_allowed`, `_resolve_team`, or `_require_chat_team` — or have a documented reason to be open. New unauthenticated endpoints are findings.
   - **Injection:** grep `shell=True`, `os.system`, `eval(`, `exec(`, `pickle`, `yaml.load(` (non-safe). SQL must be parameterized — flag any f-string/concat that interpolates non-whitelisted values into queries.
   - **Secrets:** `git ls-files | grep -iE 'env|key|secret|pem'` — nothing real may be tracked. Grep tracked files for live-looking keys (`sk-`, `AKIA`, `ghp_`, long hex assigned to *_KEY/*_TOKEN).
   - **Token handling:** comparisons against secrets use `secrets.compare_digest`; new tokens come from `secrets`, not `random`.
   - **Container/network:** Dockerfile has `USER` (non-root); compose doesn't publish unauthenticated services beyond loopback.
   - **Dependencies:** run `.venv/bin/pip-audit` (install with `.venv/bin/pip install pip-audit` if missing). Report any known CVEs with the fix version.

4. **Write the report** to `Docs/Security/SECURITY-AUDIT-<YYYY-MM-DD>.md` following the format of the previous report: severity table, High/Medium/Low sections (each finding: location, risk, remediation), carried-over findings, positive observations, remediation status. If there are **no new findings**, still write the report saying so — that's the daily attestation.

5. **Remediate.** For each new finding with a safe, mechanical fix (auth gate, constant-time compare, validation clamp, config hardening): create branch `security/audit-<YYYY-MM-DD>` from `main`, apply fixes, and run the test suite (`.venv/bin/python -m pytest -q`). All tests must pass — revert any fix that breaks tests and mark it deferred in the report instead. Never remediate findings that change product behavior (auth-on-by-default, token storage format, rate limiting) without explicit user sign-off; list those as deferred with a proposed design.

6. **Open the PR.** Commit the report + fixes to the branch, push, and `gh pr create` with title `security: daily audit <date> remediations`, a body summarizing findings by severity and which are fixed vs deferred. If there were no findings and nothing to fix, commit the report directly to a branch and open a docs-only PR (or, if the report is the only change, a PR is still preferred so there's a review record).

## Constraints

- Read-only toward production data: never print `.env` contents or database rows containing tokens into the report.
- One report per day; if today's report already exists, update it in place instead of duplicating.
- The report must be self-contained — a reader should not need the chat transcript to act on it.
