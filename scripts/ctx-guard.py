#!/usr/bin/env python3
import glob, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from breadcrumbs_common import codex_home, detect_tool, state_path

CLAUDE_BANDS = (200_000, 350_000, 500_000)
CODEX_FRACTIONS = (0.5, 0.7, 0.8, 0.85)
REPEAT_PROMPTS = 3
CODEX_FALLBACK_TOKENS = 200_000  # single absolute band when the rollout reports no context window


def tail_lines(path):
    with open(path, "rb") as f:
        f.seek(max(0, os.path.getsize(path) - 2_000_000))
        return f.read().decode(errors="ignore").splitlines()


def claude_ctx(lines):
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
            return ctx, CLAUDE_BANDS
    return 0, CLAUDE_BANDS


def codex_ctx(lines):
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
        if ctx:
            window = info.get("model_context_window") or p.get("model_context_window") or 0
            return ctx, [window * f for f in CODEX_FRACTIONS] if window else [CODEX_FALLBACK_TOKENS]
    return 0, [CODEX_FALLBACK_TOKENS]


d = json.load(sys.stdin)
if d.get("agent_id"):
    sys.exit(0)
sid = d.get("session_id") or ""
tool = detect_tool(d, os.path.abspath(__file__))
tp = d.get("transcript_path")
if tool == "codex" and (not tp or not os.path.exists(tp)):
    hits = glob.glob(os.path.join(codex_home(), "sessions", "**", f"rollout-*-{sid}.jsonl"), recursive=True) if sid else []
    tp = hits[0] if hits else None
if not tp or not os.path.exists(tp):
    sys.exit(0)
ctx, thresholds = (codex_ctx if tool == "codex" else claude_ctx)(tail_lines(tp))
band = sum(ctx >= t for t in thresholds)
state = state_path(tool, sid)
if not band:
    # A compacted context can grow again in the same session.
    if tool == "codex" and os.path.exists(state):
        os.unlink(state)
    sys.exit(0)
os.makedirs(os.path.dirname(state), exist_ok=True)
if tool == "codex":
    previous = json.loads(open(state).read() or "0") if os.path.exists(state) else 0
    # Older installs saved only the last warning band as an integer.
    if isinstance(previous, int):
        previous = {"band": previous, "prompts_since_warning": 0}
    acknowledged = previous.get("acknowledged", False) or bool(
        re.match(r"^\s*\$handoff(?:\s|$)", d.get("prompt") or ""))
    prompts = previous.get("prompts_since_warning", 0) + 1
    warn = not acknowledged and (band > previous.get("band", 0) or prompts >= REPEAT_PROMPTS)
    with open(state, "w") as f:
        json.dump({"band": band, "prompts_since_warning": 0 if warn else prompts,
                   "acknowledged": acknowledged}, f)
    if not warn:
        sys.exit(0)
else:
    last = int(open(state).read() or 0) if os.path.exists(state) else 0
    if band <= last:
        sys.exit(0)
    open(state, "w").write(str(band))
k = int(ctx) // 1000
cmd, clear = ("$handoff", "/new") if tool == "codex" else ("/handoff", "/clear")
print(json.dumps({
    "systemMessage": f"Context at {k}k tokens — every call re-reads it. Consider {cmd} then {clear}.",
    "hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": f"Before continuing with the task or calling tools, tell the user in one sentence: 'Context is at {k}k tokens; run {cmd}, then {clear}, to continue in a fresh session.' Do not defer this warning until a natural break or the end of the task. Continue the authorized work after the warning; do not invoke handoff or stop automatically.",
    },
}))
