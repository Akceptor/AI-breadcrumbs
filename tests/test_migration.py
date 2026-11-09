import sqlite3

OLD_DDL = """
CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, offset INTEGER, mtime REAL);
CREATE TABLE IF NOT EXISTS sessions(
    id TEXT PRIMARY KEY, project TEXT, cwd TEXT, branch TEXT, title TEXT,
    first_prompt TEXT, started TEXT, ended TEXT, n INTEGER DEFAULT 0);
CREATE VIRTUAL TABLE IF NOT EXISTS msgs USING fts5(
    text, session UNINDEXED, project UNINDEXED, ts UNINDEXED, role UNINDEXED,
    tokenize='porter unicode61');
"""


def _cols(con):
    return [r[1] for r in con.execute("PRAGMA table_info(sessions)")]


def test_m1_old_db_migrates_without_reindex(server):
    con = sqlite3.connect(server.DB_PATH)
    con.executescript(OLD_DDL)
    con.execute("INSERT INTO sessions(id, project, n) VALUES('old1', '-p', 1)")
    con.execute("INSERT INTO msgs(text, session, project, ts, role) VALUES('hello', 'old1', '-p', 't', 'user')")
    con.execute("INSERT INTO files VALUES('/x.jsonl', 42, 1.5)")
    con.commit()
    con.close()

    con = server._db()
    assert con.execute("SELECT source FROM sessions WHERE id='old1'").fetchone() == ("claude",)
    assert con.execute("SELECT count(*) FROM msgs").fetchone() == (1,)
    assert con.execute("SELECT * FROM files").fetchall() == [("/x.jsonl", 42, 1.5)]
    assert con.execute("PRAGMA user_version").fetchone() == (1,)


def test_m2_idempotent(server):
    server._db().close()
    con = server._db()
    assert _cols(con).count("source") == 1
    assert con.execute("PRAGMA user_version").fetchone() == (1,)


def test_m3_fresh_db_has_source(server):
    con = server._db()
    assert "source" in _cols(con)
