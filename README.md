# AI-breadcrumbs

Session continuity for Claude Code and Codex CLI: search past sessions of both tools, and carry verified state across `/clear` (Claude Code) or `/new` (Codex), including from one tool to the other.

## Install with APM (recommended)

Prerequisites: [uv](https://docs.astral.sh/uv/), `python3`, [APM](https://github.com/microsoft/apm) (developed against 0.33.0), `gh auth login` (APM validates private repos through the GitHub API) and a GitHub SSH key (the MCP server is fetched by `uvx` over SSH).

1. **Enable both targets in `~/.apm/apm.yml`.** This is required:

   ```yaml
   targets: [claude, codex]
   ```

   `apm install -g ... --target claude,codex` deploys to Codex once but does not persist the target. If the manifest says `targets: [claude]`, the next plain `apm install -g` removes the Codex deployments. A per-dependency `targets:` only narrows the global list, never widens it. To keep another dependency Claude-only, give it object form:

   ```yaml
   dependencies:
     apm:
       - git: your/other-package
         targets: [claude]
   ```

2. `apm install -g Akceptor/AI-breadcrumbs`
3. Pre-warm the MCP environment and index (avoids a startup timeout on the first tool call):
   `uvx --from git+ssh://git@github.com/Akceptor/AI-breadcrumbs breadcrumbs-mcp --sync`
4. **Codex only:** open Codex, run `/hooks` and trust the two new hooks. Codex skips untrusted hooks, and a changed hook command needs re-trusting.
5. Claude Code, optional (APM cannot set these): in `~/.claude/settings.json` allow the tools and stop transcript cleanup:

   ```json
   { "cleanupPeriodDays": 3650, "permissions": { "allow": ["mcp__sessions"] } }
   ```

Update: `apm update -g` for hooks and skills (the MCP server tracks `main` on each start). Uninstall: `apm uninstall -g Akceptor/AI-breadcrumbs`.

### Where things land (user scope)

| | Claude Code | Codex |
|---|---|---|
| Skills `handoff`, `past-sessions` | `~/.claude/skills/` | `~/.agents/skills/` |
| Hooks config | merged into `~/.claude/settings.json` | merged into `~/.codex/hooks.json` |
| Hook scripts | `~/.claude/hooks/<pkg>/scripts/` | `~/.codex/hooks/<pkg>/scripts/` |
| MCP `sessions` | `mcpServers` in `~/.claude.json` | `[mcp_servers.sessions]` in `~/.codex/config.toml` |

`<pkg>` is the install directory name (the repo name for git installs, unverified). The scripts never depend on it.

## Components

- `server/server.py`: MCP server `sessions`. Indexes every Claude Code transcript (`~/.claude/projects/*/*.jsonl`) and Codex rollout (`$CODEX_HOME/sessions/**/rollout-*.jsonl[.zst]`) into one SQLite FTS5 database at `~/.claude/session-index/index.db`. Tools: `search_sessions`, `list_sessions`, `read_session`, each with a `source` filter (`claude` or `codex`; omit for both). Indexing is incremental on every tool call; `--sync` indexes once and exits. Packaged as the `breadcrumbs-mcp` console script via `pyproject.toml`.
- `.apm/skills/handoff`: writes `~/.breadcrumbs/handoffs/<cwd-slug>.md` (Goal, Verified state, Ruled out, Hypothesis, Next step, Environment). Explicit invocation only: `/handoff` in Claude Code, `$handoff` in Codex.
- `scripts/handoff-load.py` (`SessionStart`, matcher `startup|clear`): injects a handoff younger than 3 days for the cwd, then renames it to `<slug>.prev.md` (one-shot). The folder is shared, so a handoff written in one tool loads in the other.
- `scripts/ctx-guard.py` (`UserPromptSubmit`): requests an immediate one-sentence warning. Claude Code: once at 200k / 350k / 500k tokens. Codex: the same 200k / 350k / 500k token bands as Claude, with reminders every three submitted prompts until `$handoff` is invoked; a higher band re-arms the warning after `$handoff`. A context that drops to a lower band (compaction) resets reminders. Checks run on user prompt submission, not continuously during a long turn; the warning does not pause the task. State is kept in `~/.breadcrumbs/state/`. The tool is detected from the transcript path, a Codex-only `turn_id` field, then the script's own install location; `BREADCRUMBS_TOOL=claude|codex` overrides.
- `.apm/skills/past-sessions`: tells the model to search past sessions when you refer to earlier work (replaces the CLAUDE.md / AGENTS.md paragraph, which APM cannot edit).

### MCP trade-off

The MCP entry is `uvx --from git+ssh://git@github.com/Akceptor/AI-breadcrumbs breadcrumbs-mcp`, unpinned. Each start runs a `git fetch`, so it needs GitHub reachable and fails offline; the first start also builds the environment. Offline options: pin `@<sha>` in the `--from` URL (no fetch, runs from cache), or register `uv run -q --script <path>/server/server.py` yourself.

## Fallback install without APM

Do not combine with an APM install (the installers refuse when `~/.apm/apm.lock.yaml` lists this package). Both are idempotent, copy `server/server.py` to `~/.claude/session-index/`, install the `handoff` skill and hooks under `<tool home>/hooks/ai-breadcrumbs/scripts/`, and add a "Past sessions" paragraph to `CLAUDE.md` / `AGENTS.md`.

```sh
installers/claude/install.sh   # needs the claude CLI; also sets cleanupPeriodDays and allows mcp__sessions
installers/codex/install.sh    # needs the codex CLI; then trust the hooks via /hooks
```

## Repository layout

```
apm.yml  pyproject.toml  hooks/hooks.json     APM manifest, console script, hook config
.apm/skills/{handoff,past-sessions}/          skills for both tools
scripts/                                      hook scripts and shared helpers
server/server.py                              sessions MCP server
installers/{claude,codex}/install.sh          non-APM fallbacks (installers/merge_hooks.py is shared)
```

## Not verified yet

Tested against synthetic fixtures, documented schemas and temp-HOME APM installs only:

- Codex `SessionStart` `source` for `/new` (matcher is `startup|clear`; if it differs, handoffs will not auto-load), whether `transcript_path` is the live rollout, and that hooks run through a shell.
- Whether Codex loads skills from `~/.agents/skills`, honors `agents/openai.yaml` there, and tolerates the `disable-model-invocation` frontmatter key.
- Whether Claude Code passes text typed after `/handoff` (the skill has no `$ARGUMENTS`) and lists the skill as `/handoff`.
- The install directory name for git installs; only local-path installs were tested.
- MCP startup timeouts in either tool versus the `uvx` cold start and per-start `git fetch`; Codex may also prompt for each `sessions` tool call.
- Whether a Codex hook trust survives an `apm update` that leaves the command string unchanged.
- The Codex rollout format is not a stable interface; if Codex stops appending to the JSONL files, the index goes stale.

## Privacy

The index database, `~/.breadcrumbs/` (handoffs, state) and transcripts contain your conversations. They stay local; none belong in this repo (`index.db` is gitignored).
