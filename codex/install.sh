#!/usr/bin/env bash
# Idempotent Codex CLI setup: sessions MCP server, $handoff skill, handoff hooks.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$DIR/.." && pwd)"
UV="$(command -v uv || true)"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
SERVER_DIR="$HOME/.claude/session-index"
SERVER="$SERVER_DIR/server.py"
HOOKS_JSON="$CODEX_HOME/hooks.json"
AGENTS_MD="$CODEX_HOME/AGENTS.md"

[ -x "$UV" ] || { echo "uv not found; install it (brew install uv)"; exit 1; }
command -v codex >/dev/null 2>&1 || { echo "codex not found on PATH"; exit 1; }
if grep -Eq '^\s*\[+\s*hooks[].]' "$CODEX_HOME/config.toml" 2>/dev/null; then
  echo "$CODEX_HOME/config.toml already defines [hooks]; Codex warns when hooks.json and config.toml are mixed."
  echo "Move those hooks into hooks.json (or add the two hooks there yourself), then re-run."
  exit 1
fi

mkdir -p "$SERVER_DIR" "$CODEX_HOME"
if cmp -s "$ROOT/sessions-mcp/server.py" "$SERVER" 2>/dev/null; then
  echo "server: up to date"
else
  cp "$ROOT/sessions-mcp/server.py" "$SERVER"
  echo "server: copied to $SERVER"
fi

if codex mcp get sessions >/dev/null 2>&1; then
  echo "mcp: sessions already registered"
else
  codex mcp add sessions -- "$UV" run -q --script "$SERVER"
  echo "mcp: sessions registered"
fi

mkdir -p "$CODEX_HOME/skills" "$CODEX_HOME/hooks"
rm -rf "$CODEX_HOME/skills/handoff"
cp -R "$DIR/skills/handoff" "$CODEX_HOME/skills/handoff"
echo "skill: \$handoff installed"
cp "$DIR"/hooks/*.py "$CODEX_HOME/hooks/"

CHANGED="$(python3 - "$HOOKS_JSON" "$CODEX_HOME/hooks" <<'PY'
import json, os, shutil, sys
path, hooks_dir = sys.argv[1], sys.argv[2]
d = json.load(open(path)) if os.path.exists(path) else {}
hooks = d.setdefault("hooks", {})
wanted = {
    "SessionStart": ("startup|clear", f"python3 {hooks_dir}/handoff-load.py 2>/dev/null || true"),
    "UserPromptSubmit": (None, f"python3 {hooks_dir}/ctx-guard.py 2>/dev/null || true"),
}
changed = False
for event, (matcher, cmd) in wanted.items():
    groups = hooks.setdefault(event, [])
    if any(h.get("command") == cmd for g in groups for h in g.get("hooks", [])):
        continue
    group = {"hooks": [{"type": "command", "command": cmd, "timeout": 10}]}
    if matcher:
        group = {"matcher": matcher, **group}
    groups.append(group)
    changed = True
if changed:
    if os.path.exists(path):
        shutil.copy(path, path + ".bak")
    with open(path, "w") as f:
        json.dump(d, f, indent=2)
        f.write("\n")
print("changed" if changed else "unchanged")
PY
)"
echo "hooks.json: $CHANGED"

if grep -q "^## Past sessions" "$AGENTS_MD" 2>/dev/null; then
  echo "AGENTS.md: instruction already present"
else
  cat >>"$AGENTS_MD" <<'MD'

## Past sessions

MCP server `sessions` indexes all past Codex and Claude Code transcripts (every project, incl. /new-ed sessions). When user references earlier work ("last time", "yesterday", "that bug we fixed", "other session") or context seems missing, search it before asking: `search_sessions` then `read_session`. `list_sessions` for recent activity. Pass `source="codex"` or `source="claude"` to restrict to one tool.
MD
  echo "AGENTS.md: instruction added"
fi

# Pre-warm so the first tool call doesn't time out decompressing old rollouts
"$UV" run -q --script "$SERVER" --sync >/dev/null 2>&1 || true

if [ "$CHANGED" = "changed" ]; then
  echo "done — open Codex, run /hooks, and trust the 2 new hooks (Codex skips them until trusted; any edit to a hook command needs re-trusting)"
else
  echo "done"
fi
