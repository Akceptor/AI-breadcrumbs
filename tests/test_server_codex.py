import json
import os
import shutil

import pytest

U1 = "019aaaaa-0000-7000-8000-000000000001"
U2 = "019aaaaa-0000-7000-8000-000000000002"
REL = f"sessions/2026/01/02/rollout-2026-01-02T03-04-05-{U1}.jsonl"
REL2 = f"sessions/2026/01/02/rollout-2026-01-02T03-05-00-{U2}.jsonl"


def _msgs(con, sid=U1):
    return con.execute("SELECT role, text FROM msgs WHERE session=? ORDER BY rowid", (sid,)).fetchall()


def test_x1_session_row(cserver):
    con = cserver._sync()
    row = con.execute(
        "SELECT source, project, cwd, branch, started, ended, n FROM sessions WHERE id=?", (U1,)
    ).fetchone()
    assert row == (
        "codex", "-tmp-cproj", "/tmp/cproj", "feat/x",
        "2026-01-02T03:04:05.000Z", "2026-01-02T03:04:18.000Z", 3,
    )


def test_x2_roles_and_texts(cserver):
    con = cserver._sync()
    roles_texts = _msgs(con)
    assert [r for r, _ in roles_texts] == [
        "user", "assistant", "tool_use", "tool_result", "tool_use", "tool_result", "assistant",
    ]
    assert roles_texts[0][1] == "Please investigate the quasar flicker in the renderer"
    assert roles_texts[1][1] == "Looking at the renderer now."
    assert roles_texts[-1][1] == "The flicker comes from a stale buffer."
    all_text = "\n".join(t for _, t in roles_texts)
    for absent in ("environment_context", "AGENTS.md", "secret developer", "opaque", "duplicate", "compacted"):
        assert absent not in all_text


def test_x3_first_prompt(cserver):
    con = cserver._sync()
    fp = con.execute("SELECT first_prompt FROM sessions WHERE id=?", (U1,)).fetchone()[0]
    assert fp == "Please investigate the quasar flicker in the renderer"


def test_x4_tool_texts(cserver, monkeypatch):
    con = cserver._sync()
    m = _msgs(con)
    assert ("tool_use", "exec: ls -la") in m
    assert ("tool_use", 'fs.read_file: {"path": "/tmp/cproj/a.txt"}') in m
    assert ("tool_result", "total 0 listing") in m
    assert ("tool_result", "file body text") in m
    monkeypatch.setattr(cserver, "TOOL_INPUT_MAX", 5)
    monkeypatch.setattr(cserver, "TOOL_RESULT_MAX", 4)
    cserver.DB_PATH.unlink()
    m = _msgs(cserver._sync())
    assert ("tool_use", "exec: ls -l") in m
    assert ("tool_result", "tota") in m


def test_x5_subagent_skipped_but_offset_recorded(cserver):
    con = cserver._sync()
    assert con.execute("SELECT count(*) FROM sessions WHERE id=?", (U2,)).fetchone()[0] == 0
    assert not _msgs(con, U2)
    assert not [t for _, t in con.execute("SELECT role, text FROM msgs") if "subagent reply" in t]
    off = con.execute("SELECT offset FROM files WHERE path LIKE ?", (f"%{U2}.jsonl",)).fetchone()[0]
    assert off == (cserver.CODEX_HOME / REL2).stat().st_size


def test_x6_incremental_append(cserver):
    con = cserver._sync()
    before = len(_msgs(con))
    path = cserver.CODEX_HOME / REL
    with open(path, "a") as f:
        f.write(json.dumps({
            "timestamp": "2026-01-02T03:09:00.000Z", "type": "response_item",
            "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "follow up"}]},
        }) + "\n")
    con = cserver._sync()
    m = _msgs(con)
    assert len(m) == before + 1 and m[-1] == ("user", "follow up")
    row = con.execute("SELECT cwd, project, ended, n FROM sessions WHERE id=?", (U1,)).fetchone()
    assert row == ("/tmp/cproj", "-tmp-cproj", "2026-01-02T03:09:00.000Z", 4)


def _compress(path):
    import zstandard

    data = path.read_bytes()
    z = path.with_name(path.name + ".zst")
    z.write_bytes(zstandard.ZstdCompressor().compress(data))
    return z


def test_x7_zst_matches_plain(cserver, tmp_path):
    plain = _msgs(cserver._sync())
    cserver.DB_PATH.unlink()
    p = cserver.CODEX_HOME / REL
    _compress(p)
    p.unlink()
    con = cserver._sync()
    assert _msgs(con) == plain
    assert con.execute("SELECT count(*) FROM sessions WHERE id=?", (U1,)).fetchone()[0] == 1


def test_x8_plain_to_zst_no_duplicates(cserver):
    import zstandard

    con = cserver._sync()
    before = _msgs(con)
    p = cserver.CODEX_HOME / REL
    z = _compress(p)
    p.unlink()
    assert _msgs(cserver._sync()) == before
    line = json.dumps({
        "timestamp": "2026-01-02T03:09:00.000Z", "type": "response_item",
        "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "after zst"}]},
    }) + "\n"
    z.write_bytes(zstandard.ZstdCompressor().compress(zstandard.ZstdDecompressor().decompress(z.read_bytes()) + line.encode()))
    os.utime(z, (z.stat().st_atime, z.stat().st_mtime + 10))
    assert _msgs(cserver._sync()) == before + [("user", "after zst")]


def test_x9_titles(cserver):
    con = cserver._sync()
    assert con.execute("SELECT title FROM sessions WHERE id=?", (U1,)).fetchone()[0] == "Renderer flicker"
    con.execute("UPDATE sessions SET title='manual' WHERE id=?", (U1,))
    con = cserver._sync()
    assert con.execute("SELECT title FROM sessions WHERE id=?", (U1,)).fetchone()[0] == "manual"
    idx = cserver.CODEX_HOME / "session_index.jsonl"
    with open(idx, "a") as f:
        f.write(json.dumps({"id": U1, "thread_name": "Third", "updated_at": "x"}) + "\n")
    os.utime(idx, (idx.stat().st_atime, idx.stat().st_mtime + 5))
    con = cserver._sync()
    assert con.execute("SELECT title FROM sessions WHERE id=?", (U1,)).fetchone()[0] == "Third"


def test_x9b_title_before_rollout(cserver):
    rollout = cserver.CODEX_HOME / REL
    held = rollout.read_bytes()
    rollout.unlink()
    cserver._sync()
    rollout.write_bytes(held)
    con = cserver._sync()
    assert con.execute("SELECT title FROM sessions WHERE id=?", (U1,)).fetchone()[0] == "Renderer flicker"


def test_x10_no_codex_home(server):
    shutil.rmtree(server.CODEX_HOME)
    con = server._sync()
    assert con.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1
    assert con.execute("SELECT source FROM sessions").fetchone()[0] == "claude"


def test_codex_home_env(monkeypatch, tmp_path):
    import importlib.util
    from pathlib import Path

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "ch"))
    spec = importlib.util.spec_from_file_location(
        "s2", Path(__file__).resolve().parent.parent / "sessions-mcp" / "server.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.CODEX_HOME == tmp_path / "ch"
