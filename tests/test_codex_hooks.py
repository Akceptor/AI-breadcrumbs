import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / "codex" / "hooks"
ALLOWED_TOP = {"continue", "stopReason", "suppressOutput", "systemMessage", "hookSpecificOutput", "decision", "reason"}
ALLOWED_HSO = {"hookEventName", "additionalContext"}
CWD = "/tmp/some proj"
SLUG = re.sub(r"[^A-Za-z0-9]", "-", CWD)


def run(script, stdin, tmp_path):
    env = {**os.environ, "HOME": str(tmp_path / "home"), "CODEX_HOME": str(tmp_path / "ch")}
    r = subprocess.run(
        [sys.executable, str(HOOKS / script)], input=json.dumps(stdin), env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout


def check_schema(out, event):
    d = json.loads(out)
    assert set(d) <= ALLOWED_TOP
    assert set(d["hookSpecificOutput"]) <= ALLOWED_HSO
    assert d["hookSpecificOutput"]["hookEventName"] == event
    return d


def handoff(tmp_path, body="goal: ship it", age_days=0):
    p = tmp_path / "ch" / "handoffs" / f"{SLUG}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    if age_days:
        t = time.time() - age_days * 86400
        os.utime(p, (t, t))
    return p


def test_h1_fresh_handoff_loaded_and_consumed(tmp_path):
    p = handoff(tmp_path)
    for source in ("startup", "clear"):
        p.write_text("goal: ship it")
        out = run("handoff-load.py", {"cwd": CWD, "source": source, "hook_event_name": "SessionStart"}, tmp_path)
        d = check_schema(out, "SessionStart")
        assert "goal: ship it" in d["hookSpecificOutput"]["additionalContext"]
        assert not p.exists()
        assert p.with_name(f"{SLUG}.prev.md").read_text() == "goal: ship it"


def test_h2_no_output_cases(tmp_path):
    p = handoff(tmp_path, age_days=4)
    assert run("handoff-load.py", {"cwd": CWD, "source": "startup"}, tmp_path) == ""
    assert p.exists()
    p = handoff(tmp_path)
    for source in ("resume", "compact", "fork"):
        assert run("handoff-load.py", {"cwd": CWD, "source": source}, tmp_path) == ""
        assert p.exists()
    p.unlink()
    assert run("handoff-load.py", {"cwd": CWD, "source": "startup"}, tmp_path) == ""


def test_h3_second_run_silent(tmp_path):
    handoff(tmp_path)
    assert run("handoff-load.py", {"cwd": CWD, "source": "startup"}, tmp_path)
    assert run("handoff-load.py", {"cwd": CWD, "source": "startup"}, tmp_path) == ""


def token_line(inp, window=258400, info=True):
    payload = {"type": "token_count", "info": None}
    if info:
        payload["info"] = {
            "last_token_usage": {"input_tokens": inp, "cached_input_tokens": inp // 2, "output_tokens": 5},
            "model_context_window": window,
        }
    return json.dumps({"timestamp": "2026-01-02T03:04:05.000Z", "type": "event_msg", "payload": payload})


def rollout(tmp_path, lines, sid="s-1", name=None):
    p = tmp_path / "ch" / "sessions" / "2026" / "01" / "02" / (name or f"rollout-2026-01-02T03-04-05-{sid}.jsonl")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n")
    return p


def guard(tmp_path, tp, sid="s-1", **extra):
    return run(
        "ctx-guard.py",
        {"cwd": CWD, "session_id": sid, "transcript_path": str(tp) if tp else None,
         "hook_event_name": "UserPromptSubmit", **extra},
        tmp_path,
    )


def test_h4_bands(tmp_path):
    p = rollout(tmp_path, [token_line(100_000)])
    assert guard(tmp_path, p) == ""
    p = rollout(tmp_path, [token_line(100_000), token_line(140_000)])
    out = guard(tmp_path, p)
    d = check_schema(out, "UserPromptSubmit")
    assert "$handoff" in d["hookSpecificOutput"]["additionalContext"] and "/new" in d["systemMessage"]
    assert guard(tmp_path, p) == ""
    p = rollout(tmp_path, [token_line(100_000), token_line(190_000)])
    assert "190k" in guard(tmp_path, p)
    assert guard(tmp_path, p) == ""
    p = rollout(tmp_path, [token_line(230_000)])
    assert "230k" in guard(tmp_path, p)
    state = tmp_path / "ch" / "handoffs" / ".ctxguard-s-1"
    assert state.exists()


def test_h4_window_missing_uses_absolute_fallback(tmp_path):
    p = rollout(tmp_path, [token_line(150_000, window=None)])
    assert guard(tmp_path, p) == ""
    p = rollout(tmp_path, [token_line(210_000, window=None)])
    assert "210k" in guard(tmp_path, p)


def test_h5_transcript_path_null_glob_fallback(tmp_path):
    rollout(tmp_path, [token_line(150_000)], sid="abc-123")
    out = guard(tmp_path, None, sid="abc-123")
    assert "150k" in out


def test_h6_info_null_silent(tmp_path):
    p = rollout(tmp_path, [token_line(0, info=False), token_line(0, info=False)])
    assert guard(tmp_path, p) == ""
    assert guard(tmp_path, tmp_path / "missing.jsonl") == ""


def test_h6_skips_trailing_null_info(tmp_path):
    p = rollout(tmp_path, [token_line(200_000), token_line(0, info=False)])
    assert "200k" in guard(tmp_path, p)


def test_h7_subagent_silent(tmp_path):
    p = rollout(tmp_path, [token_line(240_000)])
    assert guard(tmp_path, p, agent_id="sub-1") == ""
    assert not (tmp_path / "ch" / "handoffs" / ".ctxguard-s-1").exists()
