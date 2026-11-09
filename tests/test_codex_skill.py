import re
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent / "codex" / "skills" / "handoff"


def test_skill_frontmatter():
    text = (SKILL / "SKILL.md").read_text()
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert m
    fm = dict(re.findall(r'^(\w+):\s*(.+)$', m.group(1), re.M))
    assert fm["name"] == "handoff"
    assert len(fm["description"].strip('"')) > 20
    assert "handoffs" in text and "/new" in text and "$ARGUMENTS" not in text


def test_openai_yaml():
    y = (SKILL / "agents" / "openai.yaml").read_text()
    assert re.search(r"allow_implicit_invocation:\s*false", y)
    assert 'default_prompt: "Use $handoff to checkpoint this session."' in y
