# Plan: Codex CLI port of claude-breadcrumbs

Branch `codex-port`. Target: codex-cli 0.167.0, `CODEX_HOME` default `~/.codex`.
Claude-side files (`sessions-mcp/install.sh`, `handoff/**`) stay byte-identical; `sessions-mcp/server.py` gains Codex indexing but must behave identically for Claude data.

Legend: **[V]** verified on this machine (rollouts, binary-embedded JSON schemas, local skills) or in official docs. **[U]** not verified, with a verification step listed.

---

## 1. Verified facts this plan relies on

### 1.1 Codex rollout files
- Location: `~/.codex/sessions/YYYY/MM/DD/rollout-<YYYY-MM-DDTHH-MM-SS>-<uuid>.jsonl` **[V]**
- **Most files are zstd-compressed.** Of 342 rollouts, 323 are `*.jsonl.zst` (older ones, mtimes up to May 2026) and 19 are plain `*.jsonl` (recent). A given id never exists as both. **[V]** `~/.codex/state_5.sqlite` `threads.rollout_path` still records the `.jsonl` name for compressed files. **[V]** Codex has an `archived_sessions` dir concept (string in binary). It doesn't exist here. **[V]**
- Python 3.12 is installed here, so stdlib `compression.zstd` (3.14+) isn't available. Add the `zstandard` dependency to the uv inline script. `ZstdDecompressor().decompress()` worked on all 323 files. **[V]**
- Every file starts with a `session_meta` record. All 342 files use the same envelope, including ones written by CLI 0.10x through 0.167 (apparently migrated). **[V]**
- Envelope: `{"timestamp": "2026-01-02T03:04:05.678Z", "ordinal": 12, "type": <type>, "payload": {...}}`. Two old files lack `ordinal`. **[V]**
- The filename uuid == first `session_meta.payload.id`, in all checked files. **[V]**

Record mapping (synthetic examples only):

| Record (`type` / `payload.type`) | Field paths | Use |
|---|---|---|
| `session_meta` (first line) | `payload.id` (thread/session id), `payload.session_id` (root session id, differs for subagents), `payload.cwd`, `payload.git.branch` (`git` may be absent), `payload.timestamp`, `payload.thread_source` (`user`/`subagent`/`voice_chat`), `payload.source` (`"vscode"`, `"cli"`… or `{"subagent": {...}}`), `payload.parent_thread_id`, `payload.forked_from_id` | session row. **Skip the whole file if `thread_source=="subagent"` or `source` is a dict containing `subagent`.** Subagent files contain a second `session_meta` (the parent's) plus forked parent history, so indexing them would duplicate content. This is the equivalent of Claude's `isSidechain` skip. |
| `response_item` / `message`, `role=user` | `payload.content[]` items `{type:"input_text", text}` / `{type:"input_image"}` | role `user`. Skip injected boilerplate: text starting with `<environment_context>` or `# AGENTS.md instructions`. For `first_prompt`, also skip any text starting with `<`. |
| `response_item` / `message`, `role=assistant` | `payload.content[]` `{type:"output_text", text}`, `payload.phase` (`commentary`/`final_answer`) | role `assistant` (index both phases) |
| `response_item` / `message`, `role=developer` | system/base instructions | skip |
| `response_item` / `function_call` | `payload.name`, `payload.namespace` (optional), `payload.arguments` (JSON **string**), `payload.call_id` | `tool_use`: `"{namespace.}name: {arguments[:TOOL_INPUT_MAX]}"` |
| `response_item` / `custom_tool_call` | `payload.name` (e.g. `exec`), `payload.input` (string) | `tool_use` |
| `response_item` / `function_call_output`, `custom_tool_call_output` | `payload.output`: either a string or a list of `{type:"input_text", text}` | `tool_result`, truncated to `TOOL_RESULT_MAX` |
| `response_item` / `web_search_call` | (not inspected beyond the type name) | skip in v1 |
| `response_item` / `reasoning` | `encrypted_content` | skip |
| `event_msg` / `item_completed` | `payload.item.type` ∈ UserMessage, AgentMessage, CommandExecution, … | skip: it duplicates `response_item` |
| `event_msg` / `token_count` | `payload.info` (can be `null`) → `last_token_usage.{input_tokens, cached_input_tokens, output_tokens, …}`, `total_token_usage`, `model_context_window` | ctx-guard only |
| `token_usage_record` | `payload.usage`, `thread_token_usage` | unused |
| `turn_context`, `world_state`, `compacted`, `task_*`, `thread_settings_applied`, `realtime_item`, `inter_agent_communication_metadata` | — | skip (`compacted.replacement_history` duplicates earlier content) |

- Token semantics: `cached_input_tokens` ≤ `input_tokens` in all 1420 samples, so cached tokens are a subset of input. Context in use ≈ `last_token_usage.input_tokens`. **[V]**
- `model_context_window` is 258400 or 475000 here. The largest observed input was about 244k. **[V]** As a result, Claude's absolute 350k/500k bands would never fire for most Codex sessions.
- Titles: `~/.codex/session_index.jsonl` has lines `{"id", "thread_name", "updated_at"}`. There are 72 entries, all of which match rollout files. Later lines win. **[V]** A fuller title source is `state_5.sqlite threads.title` (330 non-empty), but it's an internal DB, so don't use it in v1.

### 1.2 Codex hooks (schemas extracted from the binary + official docs)
- Config files: `~/.codex/hooks.json` (JSON, same shape as Claude's `hooks` block) or inline `[[hooks.<Event>]]` in `config.toml`. All sources merge. If one layer has both formats, Codex warns. Neither exists here yet. **[V docs]**
- Format: `{"hooks": {"SessionStart": [{"matcher": "startup|clear", "hooks": [{"type": "command", "command": "...", "timeout": 10}]}]}}`. The matcher is a regex on `source` for SessionStart. `timeout` is in seconds. **[V docs]**
- **Trust gate:** new or changed non-managed hooks are skipped until the user approves them in `/hooks`. Trust is stored per hook hash, so any edit to the command string requires re-trusting. **[V docs]**
- `features.hooks` is stable and enabled. **[V]**
- SessionStart stdin: `{cwd, hook_event_name:"SessionStart", model, permission_mode, session_id, source: startup|resume|clear|compact|fork, transcript_path: string|null}`. **[V schema]**
- UserPromptSubmit stdin: `{cwd, hook_event_name, model, permission_mode, prompt, session_id, transcript_path: string|null, turn_id, agent_id?, agent_type?}`. **[V schema]**
- Output for both events: `{continue, stopReason, suppressOutput, systemMessage, hookSpecificOutput: {hookEventName, additionalContext}}` (UserPromptSubmit also accepts `decision`/`reason`). `additionalProperties: false` is set, so extra keys make the output invalid. This matches the JSON the existing Claude hooks emit. Plain-text stdout is also accepted as context. Docs say `systemMessage` is shown as a UI warning. **[V]**
- Commands run with the session cwd as working directory. The binary contains `-lc`, which suggests shell execution. **[U]** To be safe, install with absolute paths and no `~`.

### 1.3 Codex skills
- Layout: `<root>/<name>/SKILL.md` with YAML frontmatter `name`, `description` (required). Optional `agents/openai.yaml` supports `interface.{display_name, short_description, default_prompt}` and `policy.allow_implicit_invocation` (default true). **[V local skills + skill-creator refs]**
- Roots: `$CODEX_HOME/skills` (`~/.codex/skills`, which already holds the managed `.system/`) and `~/.agents/skills` (superpowers is symlinked there). **[V]**
- Invocation: `$handoff` explicitly, or implicitly when the description matches. The `/skills` picker exists but its exact UX is **[U]**. Skills receive no `$ARGUMENTS` substitution **[U]**: the user's text after `$handoff` is simply part of the prompt.
- Codex has both `/new` ("start a new chat during a conversation") and a "clear the terminal and start a new chat" command **[V strings]**. Which SessionStart `source` each one produces is **[U]**.

### 1.4 Codex MCP
- `codex mcp add <name> [--env K=V] -- <cmd...>` writes `[mcp_servers.<name>]` to `config.toml`. Also available: `codex mcp get|list|remove`. **[V]**
- Config keys `startup_timeout_sec`, `tool_timeout_sec`, `enabled_tools`, `default_tools_approval_mode` exist in the binary. Their allowed values and defaults are **[U]**.

---

## 2. Design decisions

1. **One index, one server file.** `sessions-mcp/server.py` indexes both sources into `~/.claude/session-index/index.db`. Both installers point their client at the same copied server file, `~/.claude/session-index/server.py`.
2. **Migration without rebuild.** Add `sessions.source TEXT NOT NULL DEFAULT 'claude'` with `ALTER TABLE` (O(1) in SQLite, and existing rows read `'claude'`, which is correct). The `msgs` FTS table is unchanged: the source filter joins `sessions` (search already does `LEFT JOIN sessions`). Gate the migration with `PRAGMA user_version` (0 → 1). No re-index is needed because existing data is all Claude.
3. **Codex `project` = cwd slug** using Claude's rule (`re.sub(r'[^A-Za-z0-9]', '-', cwd)`). The existing `project` filter then works across both sources.
4. **Files-table key for Codex = path with `.zst` stripped.** Offsets are uncompressed byte offsets. A future `.jsonl` → `.jsonl.zst` compression therefore continues from the stored offset instead of re-indexing and duplicating rows. For `.zst` files, skip the `size == offset` fast path and compare mtime only. Decompress the whole file, then slice from the offset.
5. **Subagent detection on every sync:** read the first line (`session_meta`) of each changed Codex file. This also provides `cwd`/`id` when resuming from offset > 0.
6. **Concurrency trap (new):** Claude's and Codex's MCP processes will sync the same DB at the same time. Without serialization, both read the same `files` offsets and insert duplicate `msgs`. Fix: `sqlite3.connect(DB_PATH, timeout=30)`, and in `_sync` run `BEGIN IMMEDIATE` *before* reading `files`, committing at the end. This one trap deserves a code comment.
7. **Separate Codex hook scripts** in `codex/hooks/`, so Claude hooks stay untouched. They need different paths, different usage parsing, and different command names in messages.
8. **ctx-guard bands for Codex: fractions of `model_context_window`.** Use 0.5 / 0.7 / 0.85, with an absolute fallback of 200k when the window is missing. *User should confirm; this deviates from 200k/350k/500k because of the 258k window.* Codex also auto-compacts (threshold **[U]**), so the 0.85 band may rarely fire.
9. **Handoff files:** `~/.codex/handoffs/<slug>.md`, consumed by renaming to `<slug>.prev.md`. ctx-guard state lives in `~/.codex/handoffs/.ctxguard-<session_id>` so nothing is written into Codex's `sessions/` tree.

---

## 3. Files

| File | Action |
|---|---|
| `sessions-mcp/server.py` | modify: migration, Codex indexer, `source` filter, `--sync` flag, `zstandard` dep, concurrency fix |
| `codex/install.sh` | new |
| `codex/skills/handoff/SKILL.md` | new |
| `codex/skills/handoff/agents/openai.yaml` | new (`policy.allow_implicit_invocation: false`, `default_prompt: "Use $handoff to checkpoint this session."`) |
| `codex/hooks/handoff_path.py` | new |
| `codex/hooks/handoff-load.py` | new |
| `codex/hooks/ctx-guard.py` | new |
| `tests/conftest.py`, `tests/test_server_claude.py`, `tests/test_server_codex.py`, `tests/test_migration.py`, `tests/test_codex_hooks.py`, `tests/test_claude_hooks.py` | new |
| `tests/fixtures/claude/projects/-tmp-proj/s1.jsonl`, `tests/fixtures/codex/sessions/2026/01/02/rollout-…-<uuid>.jsonl`, `…-<uuid2>.jsonl` (subagent), `tests/fixtures/codex/session_index.jsonl` | new, synthetic only |
| `README.md` | modify |
| `sessions-mcp/install.sh`, `handoff/**` | **unchanged** |

Tests run with `uv run --with pytest --with 'mcp>=1.2,<2' --with zstandard pytest tests/`. `conftest.py` imports `server.py` via `importlib.util.spec_from_file_location` and monkeypatches `PROJECTS`, `DB_PATH`, `CODEX_HOME` to `tmp_path` copies of the fixtures. `.zst` fixtures are generated at test time from the plain fixture; no binary files are committed.

---

## 4. Steps (TDD: write the listed tests first, see them fail, then implement)

### Step 0: Safety net for the current Claude behavior
Tests (`test_server_claude.py`), which must pass against the **unmodified** `server.py` before anything changes:
- C1 sync indexes user/assistant text, sets cwd/branch/started/ended/n, and sets the title from `ai-title`.
- C2 `isSidechain` records are skipped. Partial last lines (no `\n`) wait until the next sync.
- C3 incremental: appending lines indexes only the new ones. A shrunk file is re-read from 0.
- C4 `search_sessions` handles FTS errors by retrying with quoted tokens (`esp32-c3`), and `project`/`since` filters work.
- C5 `read_session` handles `include_tools` and `grep`, and accepts a prefix id.
Commit.

### Step 1: DB migration
Tests (`test_migration.py`):
- M1 Build a DB with the *old* DDL (copied verbatim from current `_db`), insert a session and msgs, then run the new `_db()`. Expect `source='claude'` on the existing rows, msgs count unchanged, `user_version==1`, and the `files` rows intact so nothing gets re-indexed.
- M2 Running `_db()` twice is idempotent.
- M3 A fresh DB has the `source` column.
Implementation: inside `_db()`, after the `CREATE IF NOT EXISTS` statements, run `if user_version < 1:` and add the column if `PRAGMA table_info(sessions)` lacks it, then `PRAGMA user_version=1`. New Claude inserts set `source='claude'` explicitly.

### Step 2: Concurrency fix
Test S1: two threads call `_sync()` against the same fixture DB at once. The msgs count must equal a single sync's count.
Implementation: `connect(..., timeout=30, isolation_level=None)`, `BEGIN IMMEDIATE` before the `files` read, `COMMIT` at the end. Rerun Step 0 tests.

### Step 3: Codex parser
Fixture rollout: `session_meta` (with git), an `<environment_context>` user message, a `# AGENTS.md instructions` user message, a real user message, a developer message, an assistant `commentary`, `custom_tool_call` + output (list form), `function_call` + output (string form), `reasoning`, `event_msg/item_completed`, `token_count`, `compacted`, and an assistant `final_answer`. Second fixture: a subagent file (`thread_source:"subagent"` plus a second parent `session_meta`).
Tests (`test_server_codex.py`):
- X1 The session row has `id`=filename uuid, `source='codex'`, `project`=slug(cwd), cwd, branch from `git.branch`, `started` from the first record and `ended` from the last indexed message, and `n` = user + assistant message count.
- X2 Indexed roles and texts match the expected list exactly. Boilerplate user messages, developer messages, reasoning, `item_completed`, and `compacted` are absent.
- X3 `first_prompt` is the real user text.
- X4 `tool_use` text is `exec: …` / `ns.name: {args}`, truncated. `tool_result` handles both output shapes.
- X5 Subagent file: no session row and no msgs. Its offset is still recorded, so it isn't re-parsed when unchanged.
- X6 Incremental: appending messages to the rollout indexes only those. The first-line read still supplies the cwd.
- X7 `.jsonl.zst` (generated at test time) indexes identically to `.jsonl`.
- X8 Compressing an already-indexed `.jsonl` to `.jsonl.zst` (delete the original) adds no duplicate msgs. Appending more lines to the source and re-compressing indexes only the new ones.
- X9 Titles from `session_index.jsonl` (last line wins) apply even when the index line is synced before the rollout. Only re-read the file when its mtime changes.
- X10 A missing `~/.codex` changes nothing for Claude-only users (no error).
Implementation: rename the existing `_index_file` → `_index_claude_file` (body unchanged except `source`). Add `_index_codex_file(con, path, key, offset)`, `_codex_title_sync(con)`, and a glob over `CODEX_HOME/{sessions,archived_sessions}/**/rollout-*.jsonl{,.zst}`. Read `CODEX_HOME` from `os.environ.get("CODEX_HOME", "~/.codex")`. Add `zstandard` to the script deps.

### Step 4: `source` filter on the tools
Tests:
- T1 `search_sessions(q, source="codex")` returns only Codex hits. `None` returns both.
- T2 `list_sessions(source=...)` behaves the same way. Output lines include `source=`.
- T3 The `read_session` header includes `source=`.
- T4 An invalid source returns a clear error string.
Also update the tool docstrings: mention Codex and that `source` ∈ `claude|codex`. Rename "past Claude Code sessions" → "past Claude Code and Codex sessions".

### Step 5: `--sync` flag
Test: `python server.py --sync` runs `_sync()` and exits 0 without starting stdio. It's used by the installers to pre-warm, so the first Codex tool call doesn't time out decompressing about 323 files. Tool timeout is **[U]**.

### Step 6: Codex hooks
`codex/hooks/handoff_path.py`: `~/.codex/handoffs/<slug>.md`, honoring `CODEX_HOME`.
`handoff-load.py`: same logic as the Claude version. Exit unless `source in ("startup","clear")`; the matcher also enforces this, but the script checks too so tests and odd configs behave. Rename to `<slug>.prev.md`. Emit `hookEventName: "SessionStart"`.
`ctx-guard.py`: use `transcript_path`. If it is null or missing, fall back to `glob(CODEX_HOME/sessions/**/rollout-*-{session_id}.jsonl)`. Read the last 2 MB and find the last `event_msg` with `payload.type=="token_count"` and non-null `info`. ctx = `last_token_usage.input_tokens`; window = `model_context_window`. Pick the band and write the state file `~/.codex/handoffs/.ctxguard-<session_id>`. The message says "run `$handoff`, then `/new`". Exit early when `agent_id` is present (subagent).
Tests (`test_codex_hooks.py`, run the scripts with `subprocess` and synthetic stdin, `HOME`/`CODEX_HOME` set to `tmp_path`):
- H1 A fresh handoff with `source=startup` emits valid JSON: only allowed keys, `hookEventName=="SessionStart"`, and `additionalContext` contains the body. The file is renamed to `.prev.md`.
- H2 A handoff older than 3 days, `source=resume`/`compact`, or a missing file produces no stdout and exit 0.
- H3 A second run after consumption produces no output.
- H4 ctx-guard below the first band: no output. Crossing 0.5 emits once. A repeat at the same band is silent. Crossing 0.7 emits again.
- H5 With `transcript_path: null`, the session_id glob fallback works.
- H6 With `info: null` token_count lines and no usable usage: silent.
- H7 With `agent_id` present: silent.
- H8 The output JSON validates against the schema keys in §1.2 (a hand-written allowed-keys check is enough).
`test_claude_hooks.py`: equivalent smoke tests for the existing Claude hooks. These protect "unchanged" but are optional.

### Step 7: Codex skill
`codex/skills/handoff/SKILL.md` frontmatter:
- `name: handoff`
- `description: "Use when the user asks to checkpoint/hand off the session before /new — writes ~/.codex/handoffs/<cwd-slug>.md"`

Body: the same sections and rules as `handoff/handoff.md`. Differences:
- Target is `${CODEX_HOME:-~/.codex}/handoffs/<slug>.md`, created with `mkdir -p`.
- "Extra instructions are whatever the user wrote alongside `$handoff`" replaces `$ARGUMENTS`.
- The final line tells the user to run `/new`.

`agents/openai.yaml` sets `allow_implicit_invocation: false`, so the skill only runs on explicit `$handoff`. Test: a lint test checks that the frontmatter parses and contains name + description. Manual: in the Codex TUI, `$handoff` writes the file.

### Step 8: `codex/install.sh` (idempotent, mirrors the Claude installer's style)
1. Require `uv` and `codex`.
2. `mkdir -p ~/.claude/session-index` and copy `sessions-mcp/server.py` there. This is the same target as the Claude install; skip if identical.
3. If `codex mcp get sessions` fails, run `codex mcp add sessions -- "$UV" run -q --script "$HOME/.claude/session-index/server.py"`. Then set `tool_timeout_sec`/approval mode only after verifying the keys (see §6).
4. Copy the skill to `${CODEX_HOME:-$HOME/.codex}/skills/handoff/`.
5. Copy the hooks to `$CODEX_HOME/hooks/`. Merge into `$CODEX_HOME/hooks.json` with a Python snippet: back it up, then add `SessionStart` (matcher `startup|clear`) and `UserPromptSubmit` entries using **absolute** paths: `python3 /abs/handoff-load.py 2>/dev/null || true`, `timeout: 10`. Skip any entry whose command is already present. Abort with a message if `config.toml` already has `[hooks]`, to avoid the mixed-format warning.
6. Append a "Past sessions" paragraph to `$CODEX_HOME/AGENTS.md` (the global instructions file; it exists and is empty here) unless the heading is present. Word it for Codex: "`/new`-ed sessions" and the `source` filter.
7. Pre-warm: `"$UV" run -q --script server.py --sync`.
8. Print: "Open Codex, run `/hooks`, and trust the 2 new hooks (they are skipped until trusted)."

Test: run with `HOME=$tmp CODEX_HOME=$tmp/.codex` and a stub `codex` on PATH that records its args. Run it twice and assert `hooks.json` has exactly one entry per event and `AGENTS.md` has one heading.

### Step 9: README
- Retitle: "Session continuity for Claude Code and Codex CLI".
- sessions-mcp: both sources, the `source` param, and the zstd note.
- New "Codex" section: the install command, `$handoff` → `/new` workflow, the `/hooks` trust step, the handoff location, and ctx-guard's fractional bands.
- Privacy: add `~/.codex/handoffs`.

### Step 10: Manual end-to-end (not automatable)
1. Add a temporary hook that logs the stdin `source` and `transcript_path`, then run Codex startup, `/new`, the clear command, and `resume`. Confirm the matcher, and that `transcript_path` is the rollout path.
2. Run `$handoff` → `/new`, and confirm the context is injected and the `systemMessage` is visible.
3. Confirm the ctx-guard nudge on a long session, or temporarily lower the bands with an env var override.
4. From Codex, call `search_sessions(source="claude")`, and from Claude call `source="codex"`.
5. Run both clients at once to check for "database is locked" errors and duplicate rows.

---

## 5. Risks
- The rollout format is officially "not a stable interface" (per the hooks docs). Mitigation: the parser ignores unknown types and the fixtures pin today's schema.
- `history_mode: "paginated"`, `thread_history_1.sqlite`, and `rollout-migrations/` hint that Codex may move history out of JSONL. If rollouts stop growing, the index silently goes stale. Add a README note.
- Codex retention/cleanup of old rollouts (the Claude `cleanupPeriodDays` equivalent) is unknown **[U]**.
- Hook trust is per hash. Changing the command string in a future install silently disables the hooks until the user re-trusts them, so the installer must say so every time it changes `hooks.json`.

## 6. Unverified, with how to verify
| Item | How |
|---|---|
| SessionStart `source` for `/new` vs the clear command | debug hook (Step 10.1) |
| `transcript_path` is the rollout `.jsonl` for active sessions | debug hook |
| Hook command runs in a shell (`~`, `\|\|`, `2>/dev/null`) | debug hook with `\|\| true`. Absolute paths avoid `~` regardless. |
| `default_tools_approval_mode` values; whether Codex prompts before each `sessions` tool call; default `tool_timeout_sec` | `codex mcp add --help` (not listed there), docs config reference, or try it live |
| Codex auto-compaction threshold vs ctx-guard bands | docs / observe `compacted` records |
| `/skills` picker UX and whether skills get any argument substitution | manual |
| Whether Codex re-decompresses `.zst` on resume (a file appearing as `.jsonl` again) | handled by the `.zst`-stripped key either way. Covered by test X8. |
| Codex rollout retention | docs |
