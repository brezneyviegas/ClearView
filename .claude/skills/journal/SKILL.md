---
name: journal
description: Append a timestamped progress entry to Docs/Journal/JOURNAL.md covering what was completed this session, next steps, files changed, and open questions/blockers. Use when the user says "/journal", "log progress", "write a journal entry", "update the journal", "log what we did", "note next steps", or "log this session".
---

# journal — session log writer

Single rolling file: `Docs/Journal/JOURNAL.md` (relative to repo root). Newest entry at top, separated by `---`. Create file + directory if missing.

## Steps

1. **Resolve repo root.** Anchor on the current working directory. Confirm `Docs/Journal/` exists; create with `mkdir -p` if not.

2. **Gather entry content from current conversation context** — do NOT ask the user. You already know what happened this session. If genuinely unclear, ask one focused question; otherwise infer.

   - **Completed**: concrete actions taken this session — features shipped, bugs fixed, agents dispatched, tests added. One bullet per discrete unit of work. Include short reasoning when not obvious from the action ("X to fix Y").
   - **Next steps**: what comes next — items the user mentioned, gaps that surfaced, follow-ups from agent reports. Order by priority. Include the *why* in 4-8 words per bullet.
   - **Files changed**: every file created/modified this session, absolute or repo-relative path, one-line description of the change. Skip if nothing changed.
   - **Open questions / blockers**: anything stuck waiting on user input, missing credentials, deferred design decisions, known bugs noticed but not fixed. Skip section if none.

3. **Format** (exact):
   ```
   # YYYY-MM-DD HH:MM
   ## Completed
   - <bullet>
   - <bullet>
   ## Next steps
   - <bullet>
   ## Files changed
   - `path/to/file` — what changed
   ## Open questions / blockers
   - <bullet>

   ---

   <existing content>
   ```
   Use 24-hour time, local timezone. Skip Files-changed and Open-questions sections if empty (keep Completed and Next steps always).

4. **Write.** If `JOURNAL.md` exists: Read it, prepend new entry + `---\n\n`, Write back. If absent: Write fresh file with just the new entry (no leading separator).

5. **Confirm to user**: one-line summary — "Logged N completed / M next-step bullets to Docs/Journal/JOURNAL.md". No verbose recap.

## Constraints

- Keep bullets terse. Caveman-mode style: drop articles, filler. Fragments OK.
- Don't invent work that didn't happen. If session was just discussion w/ no concrete output, write entry anyway with a "Completed: discussion only — <topic>" note.
- Don't dump full agent reports. Distill to outcomes.
- Don't include secrets, API keys, full prompts, or large code blocks. File paths + line refs OK.
- Date/time = real wall clock at write time. Use `date "+%Y-%m-%d %H:%M"` via Bash if needed.
- Never overwrite or modify existing entries — only prepend.
