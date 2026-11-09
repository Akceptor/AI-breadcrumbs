#!/usr/bin/env python3
import glob, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from handoff_path import codex_home, handoff_dir

FRACTIONS = (0.5, 0.7, 0.85)
FALLBACK_TOKENS = 200_000  # single absolute band when the rollout reports no context window

d = json.load(sys.stdin)
if d.get("agent_id"):
    sys.exit(0)
sid = d.get("session_id") or ""
tp = d.get("transcript_path")
if not tp or not os.path.exists(tp):
    hits = glob.glob(os.path.join(codex_home(), "sessions", "**", f"rollout-*-{sid}.jsonl"), recursive=True) if sid else []
    tp = hits[0] if hits else None
if not tp:
    sys.exit(0)
with open(tp, "rb") as f:
    f.seek(max(0, os.path.getsize(tp) - 2_000_000))
    lines = f.read().decode(errors="ignore").splitlines()
ctx = window = 0
for line in reversed(lines):
    if '"token_count"' not in line:
        continue
    try:
        p = json.loads(line).get("payload") or {}
    except ValueError:
        continue
    info = p.get("info")
    if p.get("type") != "token_count" or not info:
        continue
    ctx = (info.get("last_token_usage") or {}).get("input_tokens") or 0
    window = info.get("model_context_window") or p.get("model_context_window") or 0
    if ctx:
        break
if window:
    thresholds = [window * f for f in FRACTIONS]
else:
    thresholds = [FALLBACK_TOKENS]
band = sum(ctx >= t for t in thresholds)
if not band:
    sys.exit(0)
os.makedirs(handoff_dir(), exist_ok=True)
state = os.path.join(handoff_dir(), f".ctxguard-{sid}")
last = int(open(state).read() or 0) if os.path.exists(state) else 0
if band <= last:
    sys.exit(0)
open(state, "w").write(str(band))
k = ctx // 1000
print(json.dumps({
    "systemMessage": f"Context at {k}k tokens — every call re-reads it. Consider $handoff then /new.",
    "hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": f"Context is {k}k tokens. At a natural break (after this request), suggest to the user in one line: run $handoff, then /new — the handoff auto-loads into the fresh session.",
    },
}))
