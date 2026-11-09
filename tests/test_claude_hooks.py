import json
import os
import re
import subprocess
import sys
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / "handoff" / "hooks"
CWD = "/tmp/claude proj"


def run(script, stdin, home):
    r = subprocess.run(
        [sys.executable, str(HOOKS / script)], input=json.dumps(stdin),
        env={**os.environ, "HOME": str(home)}, capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_handoff_load_injects_and_consumes(tmp_path):
    d = tmp_path / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", CWD)
    d.mkdir(parents=True)
    (d / "handoff.md").write_text("claude body")
    out = json.loads(run("handoff-load.py", {"cwd": CWD}, tmp_path))
    assert "claude body" in out["hookSpecificOutput"]["additionalContext"]
    assert (d / "handoff.prev.md").exists() and not (d / "handoff.md").exists()
    assert run("handoff-load.py", {"cwd": CWD}, tmp_path) == ""


def test_ctx_guard_bands(tmp_path):
    t = tmp_path / "t.jsonl"
    rec = {"type": "assistant", "message": {"usage": {"input_tokens": 10, "cache_read_input_tokens": 210_000}}}
    t.write_text(json.dumps(rec) + "\n")
    out = json.loads(run("ctx-guard.py", {"transcript_path": str(t), "session_id": "s"}, tmp_path))
    assert "/handoff" in out["systemMessage"]
    assert run("ctx-guard.py", {"transcript_path": str(t), "session_id": "s"}, tmp_path) == ""
