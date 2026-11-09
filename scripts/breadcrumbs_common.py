import os
import re


def claude_dir():
    return os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude")


def codex_home():
    return os.path.expanduser(os.environ.get("CODEX_HOME") or "~/.codex")


def root():
    return os.path.expanduser("~/.breadcrumbs")


def slug(cwd):
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def handoff_path(cwd):
    return os.path.join(root(), "handoffs", slug(cwd) + ".md")


def state_path(tool, sid):
    return os.path.join(root(), "state", f"ctxguard-{tool}-{sid}")


def _under(p, d):
    rp, rd = os.path.realpath(p), os.path.realpath(d)
    return rp == rd or rp.startswith(rd + os.sep)


def detect_tool(d, script):
    t = os.environ.get("BREADCRUMBS_TOOL")
    if t in ("claude", "codex"):
        return t
    tp = d.get("transcript_path") or ""
    if tp:
        if os.path.basename(tp).startswith("rollout-") or _under(tp, codex_home()):
            return "codex"
        if _under(tp, claude_dir()):
            return "claude"
    if "turn_id" in d:
        return "codex"
    if _under(script, codex_home()):
        return "codex"
    if _under(script, claude_dir()):
        return "claude"
    return "claude"
