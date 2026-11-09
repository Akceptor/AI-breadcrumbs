import pytest

U1 = "019aaaaa-0000-7000-8000-000000000001"
SID = "aaaa1111-0000-0000-0000-000000000001"


def test_t1_search_source_filter(cserver):
    both = cserver.search_sessions("flash OR flicker")
    assert SID in both and U1 in both
    codex = cserver.search_sessions("flash OR flicker", source="codex")
    assert U1 in codex and SID not in codex
    claude = cserver.search_sessions("flash OR flicker", source="claude")
    assert SID in claude and U1 not in claude
    assert "source=codex" in codex


def test_t2_list_source_filter(cserver):
    out = cserver.list_sessions()
    assert "source=claude" in out and "source=codex" in out
    out = cserver.list_sessions(source="codex")
    assert U1 in out and SID not in out
    assert "Renderer flicker" in out


def test_t3_read_header_has_source(cserver):
    assert "source=codex" in cserver.read_session(U1[:8])
    assert "source=claude" in cserver.read_session(SID[:8])


@pytest.mark.parametrize("fn,args", [("search_sessions", ("x",)), ("list_sessions", ())])
def test_t4_invalid_source(cserver, fn, args):
    out = getattr(cserver, fn)(*args, source="gemini")
    assert "Invalid source" in out and "claude" in out and "codex" in out


def test_docstrings_mention_codex(cserver):
    for fn in (cserver.search_sessions, cserver.list_sessions, cserver.read_session):
        assert "Codex" in fn.__doc__ or "codex" in fn.__doc__
