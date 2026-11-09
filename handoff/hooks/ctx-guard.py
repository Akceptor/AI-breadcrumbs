#!/usr/bin/env python3
import json, os, sys

BANDS = (200_000, 350_000, 500_000)

d = json.load(sys.stdin)
tp = d.get("transcript_path")
if not tp or not os.path.exists(tp):
    sys.exit(0)
with open(tp, "rb") as f:
    f.seek(max(0, os.path.getsize(tp) - 2_000_000))
    lines = f.read().decode(errors="ignore").splitlines()
ctx = 0
for line in reversed(lines):
    if '"usage"' not in line or '"assistant"' not in line:
        continue
    try:
        r = json.loads(line)
    except ValueError:
        continue
    if r.get("isSidechain"):
        continue
    u = r.get("message", {}).get("usage") or {}
    ctx = u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
    if ctx:
        break
band = max((b for b in BANDS if ctx >= b), default=0)
if not band:
    sys.exit(0)
state = os.path.join(os.path.dirname(tp), f".ctxguard-{d.get('session_id')}")
last = int(open(state).read() or 0) if os.path.exists(state) else 0
if band <= last:
    sys.exit(0)
open(state, "w").write(str(band))
k = ctx // 1000
print(json.dumps({
    "systemMessage": f"Context at {k}k tokens — every call re-reads it. Consider /handoff then /clear.",
    "hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": f"Context is {k}k tokens. At a natural break (after this request), suggest to the user in one line: run /handoff, then /clear — the handoff auto-loads into the fresh session.",
    },
}))
