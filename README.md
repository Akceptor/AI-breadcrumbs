# claude-breadcrumbs

Session continuity for Claude Code: find past work, and carry verified state across `/clear`.

## Components

### `sessions-mcp/` — search past sessions

MCP server that indexes every Claude Code transcript (`~/.claude/projects/*/*.jsonl`) into a local SQLite FTS5 database and exposes:

- `search_sessions(query, project?, since?, limit?)` — full-text search, FTS5 syntax
- `list_sessions(project?, since?, limit?)` — recent sessions with title, branch, first prompt
- `read_session(session_id, offset?, limit?, include_tools?, grep?)` — page through a conversation

Indexing is incremental (per-file offset + mtime) and runs on each tool call. The database lives at `~/.claude/session-index/index.db`.

Install (requires [uv](https://docs.astral.sh/uv/) and the `claude` CLI):

```sh
mkdir -p ~/.claude/session-index
cp sessions-mcp/* ~/.claude/session-index/
~/.claude/session-index/install.sh
```

`install.sh` registers the MCP server (user scope), raises `cleanupPeriodDays` to 3650 so transcripts aren't deleted, allows `mcp__sessions`, and adds a short "Past sessions" instruction to `~/.claude/CLAUDE.md`.

### `handoff/` — checkpoint and resume

- `handoff.md` — `/handoff` slash command. Writes `~/.claude/projects/<cwd-slug>/handoff.md` with Goal, Verified state, Ruled out, Current hypothesis, Next step, Environment.
- `hooks/handoff-load.py` — `SessionStart` hook. If a handoff under 3 days old exists for the cwd, injects it into the new session and renames it to `handoff.prev.md` (one-shot).
- `hooks/ctx-guard.py` — `UserPromptSubmit` hook. At 200k / 350k / 500k context tokens (once each per session), nudges you to run `/handoff` then `/clear`.

Install:

```sh
mkdir -p ~/.claude/commands ~/.claude/hooks
cp handoff/handoff.md ~/.claude/commands/
cp handoff/hooks/*.py ~/.claude/hooks/
```

Then add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "clear|startup",
        "hooks": [
          { "type": "command", "command": "python3 ~/.claude/hooks/handoff-load.py 2>/dev/null || true", "timeout": 10 }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          { "type": "command", "command": "python3 ~/.claude/hooks/ctx-guard.py 2>/dev/null || true", "timeout": 10 }
        ]
      }
    ]
  }
}
```

Workflow: `/handoff` → `/clear` → new session starts with the checkpoint loaded.

## Privacy

The index database, handoff files and transcripts contain your conversations. They stay local; none of them belong in this repo (`index.db` is gitignored).
