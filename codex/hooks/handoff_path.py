import os, re

def codex_home():
    return os.path.expanduser(os.environ.get("CODEX_HOME") or "~/.codex")

def handoff_dir():
    return os.path.join(codex_home(), "handoffs")

def handoff_path(cwd):
    return os.path.join(handoff_dir(), re.sub(r"[^A-Za-z0-9]", "-", cwd) + ".md")
