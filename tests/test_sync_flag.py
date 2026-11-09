import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_sync_flag_indexes_and_exits(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    shutil.copytree(FIXTURES / "claude" / "projects", home / ".claude" / "projects")
    shutil.copytree(FIXTURES / "codex" / "sessions", tmp_path / "codex" / "sessions")
    env = {**os.environ, "HOME": str(home), "CODEX_HOME": str(tmp_path / "codex")}
    r = subprocess.run(
        [sys.executable, str(ROOT / "sessions-mcp" / "server.py"), "--sync"],
        env=env, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
    )
    assert r.returncode == 0, r.stderr
    con = sqlite3.connect(home / ".claude" / "session-index" / "index.db")
    assert sorted(x[0] for x in con.execute("SELECT source FROM sessions")) == ["claude", "codex"]
