import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALL = ROOT / "codex" / "install.sh"


def make_env(tmp_path):
    home = tmp_path / "home"
    bin_ = tmp_path / "bin"
    home.mkdir()
    bin_.mkdir()
    log = tmp_path / "calls.log"
    (bin_ / "codex").write_text(
        f'#!/bin/sh\necho "codex $*" >> {log}\n'
        f'if [ "$1 $2" = "mcp get" ]; then [ -f {tmp_path}/registered ]; exit $?; fi\n'
        f'if [ "$1 $2" = "mcp add" ]; then touch {tmp_path}/registered; fi\nexit 0\n'
    )
    (bin_ / "uv").write_text(f'#!/bin/sh\necho "uv $*" >> {log}\nexit 0\n')
    for f in bin_.iterdir():
        f.chmod(0o755)
    env = {
        "PATH": f"{bin_}:/usr/bin:/bin",
        "HOME": str(home),
        "CODEX_HOME": str(home / ".codex"),
    }
    return env, home, log


def run_install(env):
    return subprocess.run(["bash", str(INSTALL)], env=env, capture_output=True, text=True, timeout=60)


def test_install_idempotent(tmp_path):
    env, home, log = make_env(tmp_path)
    ch = home / ".codex"
    ch.mkdir()
    (ch / "AGENTS.md").write_text("")

    r1 = run_install(env)
    assert r1.returncode == 0, r1.stderr + r1.stdout
    assert "/hooks" in r1.stdout
    r2 = run_install(env)
    assert r2.returncode == 0, r2.stderr + r2.stdout

    hooks = json.loads((ch / "hooks.json").read_text())["hooks"]
    assert len(hooks["SessionStart"]) == 1 and len(hooks["UserPromptSubmit"]) == 1
    ss = hooks["SessionStart"][0]
    assert ss["matcher"] == "startup|clear"
    cmd = ss["hooks"][0]["command"]
    assert str(ch / "hooks" / "handoff-load.py") in cmd and "~" not in cmd
    assert ss["hooks"][0]["timeout"] == 10
    assert str(ch / "hooks" / "ctx-guard.py") in hooks["UserPromptSubmit"][0]["hooks"][0]["command"]

    assert (ch / "AGENTS.md").read_text().count("## Past sessions") == 1
    assert (ch / "skills" / "handoff" / "SKILL.md").exists()
    assert (ch / "skills" / "handoff" / "agents" / "openai.yaml").exists()
    for f in ("handoff_path.py", "handoff-load.py", "ctx-guard.py"):
        assert (ch / "hooks" / f).exists()
    assert (home / ".claude" / "session-index" / "server.py").read_bytes() == (
        ROOT / "sessions-mcp" / "server.py"
    ).read_bytes()

    calls = log.read_text()
    assert calls.count("codex mcp add sessions") == 1
    assert str(home / ".claude" / "session-index" / "server.py") in calls
    assert "--sync" in calls
    assert "/hooks" not in r2.stdout


def test_install_preserves_existing_hooks(tmp_path):
    env, home, _ = make_env(tmp_path)
    ch = home / ".codex"
    ch.mkdir()
    existing = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}}
    (ch / "hooks.json").write_text(json.dumps(existing))
    assert run_install(env).returncode == 0
    h = json.loads((ch / "hooks.json").read_text())["hooks"]["SessionStart"]
    assert len(h) == 2 and h[0] == existing["hooks"]["SessionStart"][0]
    assert (ch / "hooks.json.bak").exists()


def test_install_aborts_on_toml_hooks(tmp_path):
    env, home, log = make_env(tmp_path)
    ch = home / ".codex"
    ch.mkdir()
    (ch / "config.toml").write_text("[[hooks.SessionStart]]\n")
    r = run_install(env)
    assert r.returncode != 0 and "config.toml" in (r.stdout + r.stderr)
    assert not (ch / "hooks.json").exists()
    assert not log.exists() or "mcp add" not in log.read_text()


def test_install_requires_codex(tmp_path):
    env, home, _ = make_env(tmp_path)
    (tmp_path / "bin" / "codex").unlink()
    r = run_install(env)
    assert r.returncode != 0 and "codex" in (r.stdout + r.stderr)
