import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def server(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    spec = importlib.util.spec_from_file_location("sessions_server", ROOT / "sessions-mcp" / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    projects = tmp_path / "claude" / "projects"
    shutil.copytree(FIXTURES / "claude" / "projects", projects)
    codex = tmp_path / "codex"
    codex.mkdir()
    (tmp_path / "idx").mkdir()
    monkeypatch.setattr(mod, "PROJECTS", projects)
    monkeypatch.setattr(mod, "DB_PATH", tmp_path / "idx" / "index.db")
    monkeypatch.setattr(mod, "CODEX_HOME", codex, raising=False)
    mod.tmp = tmp_path
    return mod


@pytest.fixture
def cserver(server):
    shutil.copytree(FIXTURES / "codex" / "sessions", server.CODEX_HOME / "sessions")
    shutil.copy(FIXTURES / "codex" / "session_index.jsonl", server.CODEX_HOME / "session_index.jsonl")
    return server
