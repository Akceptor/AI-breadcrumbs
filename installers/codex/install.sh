#!/usr/bin/env bash
# Non-APM fallback for Codex CLI: sessions MCP server, $handoff skill, handoff hooks.
# Do not combine with `apm install`; the hooks would run twice.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$DIR/../.." && pwd)"
UV="$(command -v uv || true)"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
SERVER_DIR="$HOME/.claude/session-index"
SERVER="$SERVER_DIR/server.py"
HOOKS_JSON="$CODEX_HOME/hooks.json"
AGENTS_MD="$CODEX_HOME/AGENTS.md"
PLUGIN_ROOT="$CODEX_HOME/hooks/claude-breadcrumbs"

[ -x "$UV" ] || { echo "uv not found; install it (brew install uv)"; exit 1; }
command -v codex >/dev/null 2>&1 || { echo "codex not found on PATH"; exit 1; }
if grep -qs 'claude-breadcrumbs' "$HOME/.apm/apm.lock.yaml"; then
  echo "claude-breadcrumbs is installed via APM; don't mix it with this installer (uninstall it first: apm uninstall -g Akceptor/claude-breadcrumbs)."
  exit 1
fi
if grep -Eq '^\s*\[+\s*hooks[].]' "$CODEX_HOME/config.toml" 2>/dev/null; then
  echo "$CODEX_HOME/config.toml already defines [hooks]; Codex warns when hooks.json and config.toml are mixed."
  echo "Move those hooks into hooks.json (or add the two hooks there yourself), then re-run."
  exit 1
fi

mkdir -p "$SERVER_DIR" "$CODEX_HOME"
if cmp -s "$ROOT/server/server.py" "$SERVER" 2>/dev/null; then
  echo "server: up to date"
else
  cp "$ROOT/server/server.py" "$SERVER"
  echo "server: copied to $SERVER"
fi

if codex mcp get sessions >/dev/null 2>&1; then
  echo "mcp: sessions already registered"
else
  codex mcp add sessions -- "$UV" run -q --script "$SERVER"
  echo "mcp: sessions registered"
fi

mkdir -p "$CODEX_HOME/skills" "$PLUGIN_ROOT/scripts"
rm -rf "$CODEX_HOME/skills/handoff"
cp -R "$ROOT/.apm/skills/handoff" "$CODEX_HOME/skills/handoff"
echo "skill: \$handoff installed"
cp "$ROOT"/scripts/*.py "$PLUGIN_ROOT/scripts/"

CHANGED="$(python3 "$ROOT/installers/merge_hooks.py" "$ROOT/hooks/hooks.json" "$HOOKS_JSON" "$PLUGIN_ROOT")"
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
