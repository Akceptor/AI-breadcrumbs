import os

SID = "aaaa1111-0000-0000-0000-000000000001"


def _session(con):
    return con.execute(
        "SELECT project, cwd, branch, title, first_prompt, started, ended, n FROM sessions WHERE id=?", (SID,)
    ).fetchone()


def _texts(con):
    return [r for r in con.execute("SELECT role, text FROM msgs ORDER BY rowid")]


def test_c1_sync_indexes_text_and_metadata(server):
    con = server._sync()
    proj, cwd, branch, title, first, started, ended, n = _session(con)
    assert (proj, cwd, branch, title) == ("-tmp-proj", "/tmp/proj", "main", "Flash the board")
    assert first == "How do I flash the esp32-c3 board?"
    assert started == "2026-01-02T03:04:05.000Z"
    assert ended == "2026-01-02T03:04:12.000Z"
    assert n == 3
    assert ("user", "How do I flash the esp32-c3 board?") in _texts(con)
    assert ("assistant", "Use esptool to flash the firmware.") in _texts(con)
    assert ("tool_result", "flash complete") in _texts(con)
    assert any(r == "tool_use" and t.startswith("Bash: ") for r, t in _texts(con))


def test_c2_sidechain_and_partial_lines(server):
    con = server._sync()
    assert not [t for _, t in _texts(con) if "sidechain secret" in t]
    path = server.PROJECTS / "-tmp-proj" / "s1.jsonl"
    partial = (
        '{"type":"user","sessionId":"%s","timestamp":"2026-01-02T03:05:00.000Z",'
        '"message":{"role":"user","content":"late line"}}' % SID
    )
    with open(path, "a") as f:
        f.write(partial)
    con = server._sync()
    assert not [t for _, t in _texts(con) if t == "late line"]
    with open(path, "a") as f:
        f.write("\n")
    con = server._sync()
    assert [t for _, t in _texts(con) if t == "late line"] == ["late line"]


def test_c3_incremental_and_shrink(server):
    server._sync()
    before = len(_texts(server._sync()))
    path = server.PROJECTS / "-tmp-proj" / "s1.jsonl"
    with open(path, "a") as f:
        f.write(
            '{"type":"user","sessionId":"%s","timestamp":"2026-01-02T03:06:00.000Z",'
            '"message":{"role":"user","content":"appended"}}\n' % SID
        )
    con = server._sync()
    assert len(_texts(con)) == before + 1
    path.write_text(
        '{"type":"user","sessionId":"bbbb2222","timestamp":"2026-01-03T00:00:00.000Z",'
        '"message":{"role":"user","content":"rewritten"}}\n'
    )
    os.utime(path, (1, 1))
    con = server._sync()
    assert ("user", "rewritten") in _texts(con)


def test_c4_search_fallback_and_filters(server):
    out = server.search_sessions("esp32-c3")
    assert f"session={SID}" in out and "Flash the board" in out
    assert "No matches." in server.search_sessions("flash", project="nomatch")
    assert SID in server.search_sessions("flash", project="tmp/proj")
    assert "No matches." in server.search_sessions("flash", since="2027-01-01")
    assert SID in server.search_sessions("flash", since="2026-01-01")
    assert SID in server.list_sessions(project="proj")


def test_c5_read_session(server):
    out = server.read_session(SID[:8])
    assert "# Flash the board" in out and "esptool" in out
    assert "flash complete" not in out and "Bash:" not in out
    out = server.read_session(SID[:8], include_tools=True)
    assert "flash complete" in out and "Bash:" in out
    out = server.read_session(SID, grep="ESPTOOL")
    assert "esptool" in out and "How do I flash" not in out
    assert "Unknown session" in server.read_session("zzzz")


def test_s1_concurrent_syncs_do_not_duplicate(server):
    import threading

    for _ in range(8):
        server.DB_PATH.unlink(missing_ok=True)
        errors = []
        barrier = threading.Barrier(2)

        def run():
            try:
                barrier.wait()
                server._sync().close()
            except Exception as e:
                errors.append(e)

        ts = [threading.Thread(target=run) for _ in range(2)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert not errors
        con = server._db()
        assert con.execute("SELECT count(*) FROM msgs").fetchone()[0] == 4
        con.close()
