# claude-breadcrumbs

Session continuity for Claude Code and Codex CLI: find past work, and carry verified state across `/clear` (Claude Code) or `/new` (Codex).

## Components

### `sessions-mcp/` — search past sessions

MCP server that indexes every Claude Code transcript (`~/.claude/projects/*/*.jsonl`) and every Codex rollout (`$CODEX_HOME/sessions/**/rollout-*.jsonl[.zst]`, default `~/.codex`) into one local SQLite FTS5 database and exposes:

- `search_sessions(query, project?, since?, limit?, source?)` — full-text search, FTS5 syntax
- `list_sessions(project?, since?, limit?, source?)` — recent sessions with title, branch, first prompt
- `read_session(session_id, offset?, limit?, include_tools?, grep?)` — page through a conversation

`source` is `claude` or `codex`; omit it to search both. Codex sessions use the cwd slug as `project`, like Claude, so the `project` filter works across both. Codex subagent rollouts are skipped. Codex stores most older rollouts zstd-compressed, hence the `zstandard` dependency (handled by the `uv` inline script header); `server.py --sync` indexes everything once and exits, used by the installers to pre-warm.

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

## Codex CLI

Requires [uv](https://docs.astral.sh/uv/), `python3` and the `codex` CLI (developed against codex-cli 0.167.0).

```sh
codex/install.sh
```

The installer (idempotent):

- copies `sessions-mcp/server.py` to `~/.claude/session-index/server.py` (shared with the Claude install) and runs `codex mcp add sessions`;
- installs the `$handoff` skill to `$CODEX_HOME/skills/handoff/` (explicit invocation only);
- copies the hooks to `$CODEX_HOME/hooks/` and merges `SessionStart` (matcher `startup|clear`) and `UserPromptSubmit` entries with absolute paths into `$CODEX_HOME/hooks.json` (a `.bak` is written);
- adds a "Past sessions" paragraph to `$CODEX_HOME/AGENTS.md`;
- pre-warms the index.

It refuses to run if `config.toml` already defines `[hooks]`, since Codex warns about mixing `hooks.json` and `config.toml` hooks.

**Trust step:** Codex skips new or changed hooks until you approve them. After installing, open Codex, run `/hooks` and trust the two new hooks. Trust is per hook hash, so if a later install changes a hook command you must re-trust it.

Workflow: `$handoff` -> `/new` -> the new session starts with the checkpoint loaded. Handoffs live in `$CODEX_HOME/handoffs/<cwd-slug>.md` and are renamed to `<cwd-slug>.prev.md` once loaded (one-shot, ignored after 3 days).

The Codex `ctx-guard` nudges at 50% / 70% / 85% of the model's context window (once per band per session; `last_token_usage.input_tokens` against `model_context_window`), or at a single 200k-token band if the rollout reports no window. Codex auto-compacts, so the top band may rarely fire. It ignores subagent prompts.

Not verified yet (tested only against synthetic fixtures and the documented hook schemas):

- which `SessionStart` `source` Codex sends for `/new` versus its clear-terminal command. The matcher is `startup|clear`; if `/new` reports something else the handoff will not auto-load, and the matcher needs adjusting;
- that `transcript_path` is the active rollout file (the hook falls back to globbing by `session_id`);
- that hook commands run through a shell (`|| true`, `2>/dev/null`);
- Codex MCP `tool_timeout_sec` and approval defaults: the installer sets neither, so Codex may prompt before each `sessions` tool call and a very large first index could hit a tool timeout (the pre-warm exists to avoid this);
- whether `$handoff` receives extra text typed after it (no `$ARGUMENTS` substitution is assumed), and the `/skills` picker behavior;
- Codex's auto-compaction threshold and rollout retention.

The rollout format is not a stable Codex interface. If a future Codex stops appending to the JSONL rollouts (it has hints of a paginated history store), the index will silently go stale.

## Privacy

The index database, handoff files (`~/.claude/projects/*/handoff*.md`, `~/.codex/handoffs/`) and transcripts contain your conversations. They stay local; none of them belong in this repo (`index.db` is gitignored).
