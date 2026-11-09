#!/usr/bin/env python3
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from handoff_path import handoff_path

d = json.load(sys.stdin)
if d.get("source") not in ("startup", "clear"):
    sys.exit(0)
p = handoff_path(d.get("cwd") or os.getcwd())
if not os.path.exists(p) or time.time() - os.path.getmtime(p) > 3 * 86400:
    sys.exit(0)
body = open(p).read()
# One-shot: keep a copy but don't re-inject into unrelated later sessions.
os.replace(p, p[: -len(".md")] + ".prev.md")
print(json.dumps({
    "systemMessage": "Loaded handoff checkpoint from previous session.",
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": "Handoff from previous session (verified state — trust it, don't re-derive):\n\n" + body,
    },
}))
