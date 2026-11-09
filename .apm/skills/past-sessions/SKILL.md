---
name: past-sessions
description: "Use when the user references earlier work (\"last time\", \"yesterday\", \"that bug we fixed\", \"other session\") or context seems missing — search past Claude Code and Codex sessions via the sessions MCP server"
---

MCP server `sessions` indexes all past Claude Code and Codex transcripts (every project, including sessions that were cleared). Before asking the user to repeat context, search it: `search_sessions` then `read_session`. Use `list_sessions` for recent activity. Pass `source="claude"` or `source="codex"` to restrict to one tool.
