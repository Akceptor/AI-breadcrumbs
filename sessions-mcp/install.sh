#!/usr/bin/env bash
# Idempotent setup for the `sessions` MCP server (past-session search for Claude Code).
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
UV="$(command -v uv || true)"
SETTINGS="$HOME/.claude/settings.json"
CLAUDE_MD="$HOME/.claude/CLAUDE.md"

[ -x "$UV" ] || { echo "uv not found; install it (brew install uv)"; exit 1; }

if claude mcp get sessions >/dev/null 2>&1; then
  echo "mcp: sessions already registered"
else
  claude mcp add -s user sessions -- "$UV" run -q --script "$DIR/server.py"
  echo "mcp: sessions registered"
fi

[ -f "$SETTINGS" ] || echo '{}' >"$SETTINGS"
cp "$SETTINGS" "$SETTINGS.bak"
python3 - "$SETTINGS" <<'EOF'
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
EOF
echo "settings: cleanupPeriodDays>=3650, mcp__sessions allowed"

if grep -q "^## Past sessions" "$CLAUDE_MD" 2>/dev/null; then
  echo "CLAUDE.md: instruction already present"
else
  cat >>"$CLAUDE_MD" <<'EOF'

## Past sessions

MCP server `sessions` indexes all past Claude Code transcripts (every project, incl. /clear-ed ones). When user references earlier work ("last time", "yesterday", "that bug we fixed", "other session") or context seems missing, search it before asking: `search_sessions` then `read_session`. `list_sessions` for recent activity.
EOF
  echo "CLAUDE.md: instruction added"
fi

# Pre-warm uv dep cache so first server start in a session is fast
(printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"install","version":"1"}}}'; sleep 3) \
  | "$UV" run -q --script "$DIR/server.py" >/dev/null 2>&1 || true
echo "done — restart Claude Code sessions to pick it up"
