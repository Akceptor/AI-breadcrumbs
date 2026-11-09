# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2"]
# ///
"""MCP server giving Claude search/read access to past Claude Code session transcripts."""

import json
import os
import sqlite3
from pathlib import Path

from mcp.server.fastmcp import FastMCP

PROJECTS = Path.home() / ".claude" / "projects"
DB_PATH = Path.home() / ".claude" / "session-index" / "index.db"
TOOL_RESULT_MAX = 2000
TOOL_INPUT_MAX = 1000

mcp = FastMCP("sessions")


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
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


def _index_file(con: sqlite3.Connection, path: Path, project: str, offset: int) -> int:
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
            con.execute("INSERT OR IGNORE INTO sessions(id, project) VALUES(?, ?)", (sid, project))
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


def _sync() -> sqlite3.Connection:
    con = _db()
    known = {p: (o, m) for p, o, m in con.execute("SELECT path, offset, mtime FROM files")}
    for path in PROJECTS.glob("*/*.jsonl"):
        st = path.stat()
        key = str(path)
        offset, mtime = known.get(key, (0, 0))
        if st.st_mtime == mtime and st.st_size == offset:
            continue
        if st.st_size < offset:
            offset = 0  # file was rewritten
        new_offset = _index_file(con, path, path.parent.name, offset)
        con.execute("INSERT OR REPLACE INTO files VALUES(?,?,?)", (key, new_offset, st.st_mtime))
    con.commit()
    return con


def _project_filter(project: str | None, col: str) -> tuple[str, list]:
    if not project:
        return "", []
    return f" AND {col} LIKE ?", [f"%{project.replace('/', '-')}%"]


@mcp.tool()
def search_sessions(query: str, project: str | None = None, since: str | None = None, limit: int = 20) -> str:
    """Full-text search across all past Claude Code sessions (all projects).

    query: FTS5 syntax — words are ANDed; use "exact phrase", OR, prefix*, NEAR(a b).
    project: substring of project path, e.g. "firmware" or "/Users/x/Drones/ExpressLRS".
    since: ISO date, e.g. "2026-09-01".
    Returns matching snippets with session id; use read_session to see full context.
    """
    con = _sync()
    pf, pargs = _project_filter(project, "m.project")
    sql = (
        "SELECT m.session, m.project, m.ts, m.role, snippet(msgs, 0, '[', ']', '…', 24), s.title"
        " FROM msgs m LEFT JOIN sessions s ON s.id = m.session WHERE msgs MATCH ?" + pf
    )
    args = [query, *pargs]
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
        f"[{ts[:16] if ts else '?'}] session={sid} project={proj} title={title!r} role={role}\n  {snip}"
        for sid, proj, ts, role, snip, title in rows
    )


@mcp.tool()
def list_sessions(project: str | None = None, since: str | None = None, limit: int = 30) -> str:
    """List past sessions, newest first, with title, branch, time range and first prompt.

    project: substring of project path, e.g. "firmware".
    since: ISO date, e.g. "2026-09-01".
    """
    con = _sync()
    pf, args = _project_filter(project, "project")
    sql = "SELECT id, project, branch, title, first_prompt, started, ended, n FROM sessions WHERE n > 0" + pf
    if since:
        sql += " AND ended >= ?"
        args.append(since)
    sql += " ORDER BY ended DESC LIMIT ?"
    args.append(limit)
    out = []
    for sid, proj, branch, title, fp, start, end, n in con.execute(sql, args):
        out.append(
            f"{sid}  {(start or '?')[:16]} → {(end or '?')[:16]}  msgs={n}  project={proj}  branch={branch}\n"
            f"  title: {title}\n  first: {(fp or '').splitlines()[0][:200] if fp else ''}"
        )
    return "\n".join(out) or "No sessions."


@mcp.tool()
def read_session(
    session_id: str, offset: int = 0, limit: int = 60, include_tools: bool = False, grep: str | None = None
) -> str:
    """Read a past session's conversation in order.

    offset/limit: page through messages (check 'total' in header).
    include_tools: also show tool calls and (truncated) tool results.
    grep: only messages containing this substring (case-insensitive).
    """
    con = _sync()
    meta = con.execute(
        "SELECT project, cwd, branch, title, started, ended FROM sessions WHERE id LIKE ?", (session_id + "%",)
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
    proj, cwd, branch, title, start, end = meta
    head = f"# {title}\nproject={proj} cwd={cwd} branch={branch} {start} → {end}\nshowing {offset}-{offset + len(rows)} of {total}\n"
    body = "\n\n".join(f"--- {role} [{(ts or '')[11:16]}]\n{text}" for ts, role, text in rows)
    return head + "\n" + body


if __name__ == "__main__":
    os.makedirs(DB_PATH.parent, exist_ok=True)
    mcp.run()
