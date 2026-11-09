---
name: handoff
description: "Checkpoint verified state to ~/.breadcrumbs/handoffs/<cwd-slug>.md so the next session (after /clear in Claude Code or /new in Codex) resumes without the old context"
disable-model-invocation: true
---

Write a handoff checkpoint for continuing this work in a fresh session, possibly in the other tool (Claude Code or Codex). Target file: `~/.breadcrumbs/handoffs/<cwd with every non-alphanumeric char replaced by "-">.md` (compute from the current working directory; run `mkdir -p` on the directory first; overwrite if present).

Keep it under ~60 lines. Only facts verified in this session — mark anything unverified as such. Sections:

- **Goal** — what we're ultimately trying to achieve.
- **Verified state** — what works / is confirmed, with evidence (commit SHAs, test results, observed device behavior).
- **Ruled out** — hypotheses tested and rejected, and why. Prevents redoing dead ends.
- **Current hypothesis / open question** — where we are right now.
- **Next step** — the exact next action, with commands.
- **Environment** — branch, build env, ports/devices, flags, files touched, anything non-obvious needed to resume.

No log dumps — summarize to the one line that matters. Treat any text the user wrote alongside the invocation as extra instructions.

After writing, reply in one line: path written, and tell the user to run `/clear` (Claude Code) or `/new` (Codex) — the handoff auto-loads into the new session in either tool.
