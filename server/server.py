# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2", "zstandard"]
# ///
"""MCP server giving search/read access to past Claude Code and Codex session transcripts."""

import json
import os
import re
import sqlite3
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

PROJECTS = Path.home() / ".claude" / "projects"
CODEX_HOME = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
DB_PATH = Path.home() / ".claude" / "session-index" / "index.db"
TOOL_RESULT_MAX = 2000
TOOL_INPUT_MAX = 1000

mcp = FastMCP("sessions")


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, offset INTEGER, mtime REAL);
        CREATE TABLE IF NOT EXISTS sessions(
            id TEXT PRIMARY KEY, project TEXT, cwd TEXT, branch TEXT, title TEXT,
            first_prompt TEXT, started TEXT, ended TEXT, n INTEGER DEFAULT 0);
        CREATE VIRTUAL TABLE IF NOT EXISTS msgs USING fts5(
            text, session UNINDEXED, project UNINDEXED, ts UNINDEXED, role UNINDEXED,
            tokenize='porter unicode61');
        """
    )
    if con.execute("PRAGMA user_version").fetchone()[0] < 1:
        if "source" not in [r[1] for r in con.execute("PRAGMA table_info(sessions)")]:
            try:
                con.execute("ALTER TABLE sessions ADD COLUMN source TEXT NOT NULL DEFAULT 'claude'")
            except sqlite3.OperationalError:
                pass  # another process migrated between the check and the ALTER
        con.execute("PRAGMA user_version=1")
    return con


def _blocks(rec: dict):
    msg = rec.get("message") or {}
    content = msg.get("content")
    if isinstance(content, str):
        yield rec["type"], content
        return
    for b in content or []:
        t = b.get("type")
        if t == "text":
            yield rec["type"], b.get("text", "")
        elif t == "tool_use":
            inp = json.dumps(b.get("input", {}), ensure_ascii=False)[:TOOL_INPUT_MAX]
            yield "tool_use", f"{b.get('name')}: {inp}"
        elif t == "tool_result":
            c = b.get("content")
            if isinstance(c, list):
                c = "\n".join(x.get("text", "") for x in c if isinstance(x, dict))
            if c:
                yield "tool_result", str(c)[:TOOL_RESULT_MAX]


def _index_claude_file(con: sqlite3.Connection, path: Path, project: str, offset: int) -> int:
    with open(path, "rb") as f:
        f.seek(offset)
        for raw in f:
            if not raw.endswith(b"\n"):
                break  # partial line still being written; pick it up next sync
            offset += len(raw)
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            sid = rec.get("sessionId")
            if not sid:
                continue
            con.execute("INSERT OR IGNORE INTO sessions(id, project, source) VALUES(?, ?, 'claude')", (sid, project))
            typ = rec.get("type")
            if typ == "ai-title":
                con.execute("UPDATE sessions SET title=? WHERE id=?", (rec.get("aiTitle"), sid))
                continue
            if typ not in ("user", "assistant") or rec.get("isSidechain"):
                continue
            ts = rec.get("timestamp")
            con.execute(
                "UPDATE sessions SET cwd=COALESCE(cwd, ?), branch=COALESCE(?, branch),"
                " started=COALESCE(started, ?), ended=?, n=n+1 WHERE id=?",
                (rec.get("cwd"), rec.get("gitBranch"), ts, ts, sid),
            )
            for role, text in _blocks(rec):
                if not text.strip():
                    continue
                if role == "user" and not text.startswith("<"):
                    con.execute(
                        "UPDATE sessions SET first_prompt=COALESCE(first_prompt, ?) WHERE id=?",
                        (text[:300], sid),
                    )
                con.execute(
                    "INSERT INTO msgs(text, session, project, ts, role) VALUES(?,?,?,?,?)",
                    (text, sid, project, ts, role),
                )
    return offset

_CODEX_BOILERPLATE = ("<environment_context>", "# AGENTS.md instructions")


def _codex_key(path: Path) -> str:
    # Offsets are uncompressed byte offsets, so a later .jsonl -> .jsonl.zst
    # compression must resolve to the same files row and continue, not re-index.
    return str(path)[:-4] if path.suffix == ".zst" else str(path)


def _codex_read(path: Path, offset: int) -> tuple[bytes, bytes]:
    """Return (first line, bytes from offset) of the uncompressed rollout."""
    if path.suffix == ".zst":
        import zstandard

        with open(path, "rb") as f:
            data = zstandard.ZstdDecompressor().stream_reader(f, read_across_frames=True).read()
        return data.split(b"\n", 1)[0] + b"\n", data[offset:]
    with open(path, "rb") as f:
        first = f.readline()
        f.seek(offset)
        return first, f.read()


def _codex_blocks(payload: dict):
    typ = payload.get("type")
    if typ == "message":
        role = payload.get("role")
        if role not in ("user", "assistant"):
            return
        for item in payload.get("content") or []:
            text = item.get("text") if isinstance(item, dict) else None
            if not text or not text.strip():
                continue
            if role == "user" and text.startswith(_CODEX_BOILERPLATE):
                continue
            yield role, text
    elif typ in ("function_call", "custom_tool_call"):
        name = payload.get("name") or ""
        if payload.get("namespace"):
            name = f"{payload['namespace']}.{name}"
        body = payload.get("arguments") if typ == "function_call" else payload.get("input")
        yield "tool_use", f"{name}: {str(body or '')[:TOOL_INPUT_MAX]}"
    elif typ in ("function_call_output", "custom_tool_call_output"):
        out = payload.get("output")
        if isinstance(out, list):
            out = "\n".join(x.get("text", "") for x in out if isinstance(x, dict))
        if out:
            yield "tool_result", str(out)[:TOOL_RESULT_MAX]


def _index_codex_file(con: sqlite3.Connection, path: Path, offset: int) -> int:
    first, chunk = _codex_read(path, offset)
    if not first.endswith(b"\n"):
        return offset  # session_meta still being written
    try:
        meta = json.loads(first)
    except ValueError:
        meta = {}
    mp = meta.get("payload") or {} if meta.get("type") == "session_meta" else {}
    src = mp.get("source")
    skip = not mp or mp.get("thread_source") == "subagent" or (isinstance(src, dict) and "subagent" in src)
    fname = re.search(r"-([0-9a-f]{8}-[0-9a-f-]{27})\.jsonl", path.name)
    sid = mp.get("id") or (fname.group(1) if fname else None)
    skip = skip or not sid
    if not skip:
        cwd = mp.get("cwd")
        project = re.sub(r"[^A-Za-z0-9]", "-", cwd) if cwd else "unknown"
        branch = (mp.get("git") or {}).get("branch")
        con.execute(
            "INSERT OR IGNORE INTO sessions(id, project, cwd, branch, started, source) VALUES(?,?,?,?,?,'codex')",
            (sid, project, cwd, branch, meta.get("timestamp")),
        )
    for raw in chunk.splitlines(keepends=True):
        if not raw.endswith(b"\n"):
            break
        offset += len(raw)
        if skip:
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if rec.get("type") != "response_item":
            continue
        ts = rec.get("timestamp")
        blocks = list(_codex_blocks(rec.get("payload") or {}))
        if not blocks:
            continue
        for role, text in blocks:
            if role == "user" and not text.startswith("<"):
                con.execute(
                    "UPDATE sessions SET first_prompt=COALESCE(first_prompt, ?) WHERE id=?", (text[:300], sid)
                )
            con.execute(
                "INSERT INTO msgs(text, session, project, ts, role) VALUES(?,?,?,?,?)", (text, sid, project, ts, role)
            )
        is_msg = any(role in ("user", "assistant") for role, _ in blocks)
        con.execute("UPDATE sessions SET ended=?, n=n+? WHERE id=?", (ts, int(is_msg), sid))
    return offset


def _codex_sync_titles(con: sqlite3.Connection, force: bool) -> None:
    idx = CODEX_HOME / "session_index.jsonl"
    try:
        mtime = idx.stat().st_mtime
    except OSError:
        return
    row = con.execute("SELECT mtime FROM files WHERE path=?", (str(idx),)).fetchone()
    if row and row[0] == mtime and not force:
        return
    titles = {}
    for line in idx.read_text(errors="ignore").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("id") and r.get("thread_name"):
            titles[r["id"]] = r["thread_name"]
    con.executemany(
        "UPDATE sessions SET title=? WHERE id=? AND source='codex'", [(t, i) for i, t in titles.items()]
    )
    con.execute("INSERT OR REPLACE INTO files VALUES(?,?,?)", (str(idx), 0, mtime))


def _codex_files():
    for sub in ("sessions", "archived_sessions"):
        base = CODEX_HOME / sub
        yield from sorted(base.glob("**/rollout-*.jsonl"))
        yield from sorted(base.glob("**/rollout-*.jsonl.zst"))


def _sync() -> sqlite3.Connection:
    con = _db()
    # Claude and Codex each run their own server process on the same DB. Taking the
    # write lock before reading `files` makes the second process wait and then see
    # the first one's offsets; otherwise both re-index the same lines and duplicate msgs.
    con.execute("BEGIN IMMEDIATE")
    try:
        known = {p: (o, m) for p, o, m in con.execute("SELECT path, offset, mtime FROM files")}
        for path in PROJECTS.glob("*/*.jsonl"):
            st = path.stat()
            key = str(path)
            offset, mtime = known.get(key, (0, 0))
            if st.st_mtime == mtime and st.st_size == offset:
                continue
            if st.st_size < offset:
                offset = 0  # file was rewritten
            new_offset = _index_claude_file(con, path, path.parent.name, offset)
            con.execute("INSERT OR REPLACE INTO files VALUES(?,?,?)", (key, new_offset, st.st_mtime))
        new_sessions = con.total_changes
        for path in _codex_files():
            st = path.stat()
            key = _codex_key(path)
            offset, mtime = known.get(key, (0, 0))
            zst = path.suffix == ".zst"
            if st.st_mtime == mtime and (zst or st.st_size == offset):
                continue
            if not zst and st.st_size < offset:
                offset = 0
            new_offset = _index_codex_file(con, path, offset)
            con.execute("INSERT OR REPLACE INTO files VALUES(?,?,?)", (key, new_offset, st.st_mtime))
            known[key] = (new_offset, st.st_mtime)
        _codex_sync_titles(con, force=con.total_changes != new_sessions)
    except BaseException:
        con.execute("ROLLBACK")
        raise
    con.execute("COMMIT")
    return con


def _project_filter(project: str | None, col: str) -> tuple[str, list]:
    if not project:
        return "", []
    return f" AND {col} LIKE ?", [f"%{project.replace('/', '-')}%"]


def _source_filter(source: str | None, col: str) -> tuple[str, list]:
    if not source:
        return "", []
    return f" AND {col} = ?", [source]


_SOURCE_ERR = "Invalid source {!r}: use 'claude' or 'codex'."


@mcp.tool()
def search_sessions(
    query: str, project: str | None = None, since: str | None = None, limit: int = 20, source: str | None = None
) -> str:
    """Full-text search across all past Claude Code and Codex sessions (all projects).

    query: FTS5 syntax — words are ANDed; use "exact phrase", OR, prefix*, NEAR(a b).
    project: substring of project path, e.g. "firmware" or "/Users/x/Drones/ExpressLRS".
    since: ISO date, e.g. "2026-09-01".
    source: "claude" or "codex" to restrict to one tool; default searches both.
    Returns matching snippets with session id; use read_session to see full context.
    """
    if source and source not in ("claude", "codex"):
        return _SOURCE_ERR.format(source)
    con = _sync()
    pf, pargs = _project_filter(project, "m.project")
    sf, sargs = _source_filter(source, "s.source")
    sql = (
        "SELECT m.session, m.project, m.ts, m.role, snippet(msgs, 0, '[', ']', '…', 24), s.title, s.source"
        " FROM msgs m LEFT JOIN sessions s ON s.id = m.session WHERE msgs MATCH ?" + pf + sf
    )
    args = [query, *pargs, *sargs]
    if since:
        sql += " AND m.ts >= ?"
        args.append(since)
    sql += " ORDER BY rank LIMIT ?"
    args.append(limit)
    try:
        rows = con.execute(sql, args).fetchall()
    except sqlite3.OperationalError:
        # bare tokens like esp32-c3 are FTS5 syntax errors; retry with every token quoted
        args[0] = " ".join('"' + t.replace('"', "") + '"' for t in query.split())
        try:
            rows = con.execute(sql, args).fetchall()
        except sqlite3.OperationalError as e:
            return f"Query error: {e}"
    if not rows:
        return "No matches."
    return "\n\n".join(
        f"[{ts[:16] if ts else '?'}] session={sid} source={src} project={proj} title={title!r} role={role}\n  {snip}"
        for sid, proj, ts, role, snip, title, src in rows
    )


@mcp.tool()
def list_sessions(
    project: str | None = None, since: str | None = None, limit: int = 30, source: str | None = None
) -> str:
    """List past Claude Code and Codex sessions, newest first, with title, branch, time range and first prompt.

    project: substring of project path, e.g. "firmware".
    since: ISO date, e.g. "2026-09-01".
    source: "claude" or "codex" to restrict to one tool; default lists both.
    """
    if source and source not in ("claude", "codex"):
        return _SOURCE_ERR.format(source)
    con = _sync()
    pf, args = _project_filter(project, "project")
    sf, sargs = _source_filter(source, "source")
    args += sargs
    sql = "SELECT id, project, branch, title, first_prompt, started, ended, n, source FROM sessions WHERE n > 0" + pf + sf
    if since:
        sql += " AND ended >= ?"
        args.append(since)
    sql += " ORDER BY ended DESC LIMIT ?"
    args.append(limit)
    out = []
    for sid, proj, branch, title, fp, start, end, n, src in con.execute(sql, args):
        out.append(
            f"{sid}  {(start or '?')[:16]} → {(end or '?')[:16]}  msgs={n}  source={src}  project={proj}  branch={branch}\n"
            f"  title: {title}\n  first: {(fp or '').splitlines()[0][:200] if fp else ''}"
        )
    return "\n".join(out) or "No sessions."


@mcp.tool()
def read_session(
    session_id: str, offset: int = 0, limit: int = 60, include_tools: bool = False, grep: str | None = None
) -> str:
    """Read a past Claude Code or Codex session's conversation in order.

    offset/limit: page through messages (check 'total' in header).
    include_tools: also show tool calls and (truncated) tool results.
    grep: only messages containing this substring (case-insensitive).
    """
    con = _sync()
    meta = con.execute(
        "SELECT project, cwd, branch, title, started, ended, source FROM sessions WHERE id LIKE ?", (session_id + "%",)
    ).fetchone()
    if not meta:
        return f"Unknown session {session_id}."
    roles = ("user", "assistant", "tool_use", "tool_result") if include_tools else ("user", "assistant")
    sql = f"SELECT ts, role, text FROM msgs WHERE session LIKE ? AND role IN ({','.join('?' * len(roles))})"
    args = [session_id + "%", *roles]
    if grep:
        sql += " AND instr(lower(text), lower(?)) > 0"
        args.append(grep)
    total = con.execute(sql.replace("ts, role, text", "count(*)"), args).fetchone()[0]
    rows = con.execute(sql + " ORDER BY rowid LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
    proj, cwd, branch, title, start, end, src = meta
    head = f"# {title}\nsource={src} project={proj} cwd={cwd} branch={branch} {start} → {end}\nshowing {offset}-{offset + len(rows)} of {total}\n"
    body = "\n\n".join(f"--- {role} [{(ts or '')[11:16]}]\n{text}" for ts, role, text in rows)
    return head + "\n" + body


def main() -> None:
    os.makedirs(DB_PATH.parent, exist_ok=True)
    if "--sync" in sys.argv[1:]:
        _sync().close()
    else:
        mcp.run()


if __name__ == "__main__":
    main()
