#!/usr/bin/env python3
import glob, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from breadcrumbs_common import codex_home, detect_tool, state_path

CLAUDE_BANDS = (200_000, 350_000, 500_000)
CODEX_FRACTIONS = (0.5, 0.7, 0.85)
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
if not band:
    sys.exit(0)
state = state_path(tool, sid)
os.makedirs(os.path.dirname(state), exist_ok=True)
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
        "additionalContext": f"Context is {k}k tokens. At a natural break (after this request), suggest to the user in one line: run {cmd}, then {clear} — the handoff auto-loads into the fresh session.",
    },
}))
