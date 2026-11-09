import os, re

def handoff_path(cwd):
    return os.path.expanduser(f"~/.claude/projects/{re.sub(r'[^A-Za-z0-9]', '-', cwd)}/handoff.md")
