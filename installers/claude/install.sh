#!/usr/bin/env bash
# Non-APM fallback for Claude Code: sessions MCP server, /handoff skill, handoff hooks, settings.
# Do not combine with `apm install`; the hooks would run twice.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$DIR/../.." && pwd)"
UV="$(command -v uv || true)"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
SERVER_DIR="$HOME/.claude/session-index"
SERVER="$SERVER_DIR/server.py"
SETTINGS="$CLAUDE_DIR/settings.json"
CLAUDE_MD="$CLAUDE_DIR/CLAUDE.md"
PLUGIN_ROOT="$CLAUDE_DIR/hooks/claude-breadcrumbs"

[ -x "$UV" ] || { echo "uv not found; install it (brew install uv)"; exit 1; }
command -v claude >/dev/null 2>&1 || { echo "claude not found on PATH"; exit 1; }
if grep -qs 'claude-breadcrumbs' "$HOME/.apm/apm.lock.yaml"; then
  echo "claude-breadcrumbs is installed via APM; don't mix it with this installer (uninstall it first: apm uninstall -g Akceptor/claude-breadcrumbs)."
  exit 1
fi

mkdir -p "$SERVER_DIR" "$CLAUDE_DIR"
if cmp -s "$ROOT/server/server.py" "$SERVER" 2>/dev/null; then
  echo "server: up to date"
else
  cp "$ROOT/server/server.py" "$SERVER"
  echo "server: copied to $SERVER"
fi

if claude mcp get sessions >/dev/null 2>&1; then
  echo "mcp: sessions already registered"
else
  claude mcp add -s user sessions -- "$UV" run -q --script "$SERVER"
  echo "mcp: sessions registered"
fi

mkdir -p "$CLAUDE_DIR/skills" "$PLUGIN_ROOT/scripts"
rm -rf "$CLAUDE_DIR/skills/handoff"
cp -R "$ROOT/.apm/skills/handoff" "$CLAUDE_DIR/skills/handoff"
echo "skill: /handoff installed"
cp "$ROOT"/scripts/*.py "$PLUGIN_ROOT/scripts/"

HOOKS="$(python3 "$ROOT/installers/merge_hooks.py" "$ROOT/hooks/hooks.json" "$SETTINGS" "$PLUGIN_ROOT")"
echo "settings hooks: $HOOKS"

[ -f "$SETTINGS.bak" ] || cp "$SETTINGS" "$SETTINGS.bak"
python3 - "$SETTINGS" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
d["cleanupPeriodDays"] = max(d.get("cleanupPeriodDays", 0), 3650)
allow = d.setdefault("permissions", {}).setdefault("allow", [])
if "mcp__sessions" not in allow:
    allow.append("mcp__sessions")
with open(p, "w") as f:
    json.dump(d, f, indent=2)
    f.write("\n")
PY
echo "settings: cleanupPeriodDays>=3650, mcp__sessions allowed"

if grep -q "^## Past sessions" "$CLAUDE_MD" 2>/dev/null; then
  echo "CLAUDE.md: instruction already present"
else
  cat >>"$CLAUDE_MD" <<'MD'

## Past sessions

MCP server `sessions` indexes all past Claude Code and Codex transcripts (every project, incl. /clear-ed ones). When user references earlier work ("last time", "yesterday", "that bug we fixed", "other session") or context seems missing, search it before asking: `search_sessions` then `read_session`. `list_sessions` for recent activity. Pass `source="claude"` or `source="codex"` to restrict to one tool.
MD
  echo "CLAUDE.md: instruction added"
fi

# Pre-warm so the first tool call in a session is fast
"$UV" run -q --script "$SERVER" --sync >/dev/null 2>&1 || true
echo "done — restart Claude Code sessions to pick it up"
