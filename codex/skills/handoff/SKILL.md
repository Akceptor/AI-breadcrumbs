---
name: handoff
description: "Use when the user asks to checkpoint/hand off the session before /new — writes ~/.codex/handoffs/<cwd-slug>.md"
---

Write a handoff checkpoint for continuing this work in a fresh session. Target file: `${CODEX_HOME:-~/.codex}/handoffs/<cwd with every non-alphanumeric char replaced by "-">.md` (compute from the current working directory; run `mkdir -p` on the directory first; overwrite if present).

Keep it under ~60 lines. Only facts verified in this session — mark anything unverified as such. Sections:

- **Goal** — what we're ultimately trying to achieve.
- **Verified state** — what works / is confirmed, with evidence (commit SHAs, test results, observed device behavior).
- **Ruled out** — hypotheses tested and rejected, and why. Prevents redoing dead ends.
- **Current hypothesis / open question** — where we are right now.
- **Next step** — the exact next action, with commands.
- **Environment** — branch, build env, ports/devices, flags, files touched, anything non-obvious needed to resume.

No log dumps — summarize to the one line that matters. Extra instructions are whatever the user wrote alongside `$handoff`.

After writing, reply in one line: path written, and tell the user to run `/new` (handoff auto-loads into the new session).
