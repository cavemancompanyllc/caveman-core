from pathlib import Path

import pytest

from caveman_core import env, paths


def test_walk_up_finds_workspace(ws):
    deep = ws / "a" / "b"
    deep.mkdir(parents=True)
    w = paths.resolve(cwd=deep)
    assert w.root == ws.resolve()
    assert w.rule == "cwd walk-up"
    assert (w.name, w.slug, w.kind) == ("Acme", "acme", "business")


def test_env_vars_win_in_order(ws, tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("CONCIERGE_HOME", str(other))
    assert paths.resolve(cwd=ws).rule == "CONCIERGE_HOME"
    monkeypatch.setenv("CAVEMAN_HOME", str(ws))
    assert paths.resolve(cwd=other).rule == "CAVEMAN_HOME"
    assert paths.resolve(explicit=other).rule == "--workspace"


def test_claude_project_dir(ws, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(ws))
    assert paths.resolve(cwd=tmp_path).rule == "CLAUDE_PROJECT_DIR walk-up"


def test_not_found_message(tmp_path):
    with pytest.raises(paths.WorkspaceNotFound, match="workspace init"):
        paths.resolve(cwd=tmp_path)


def test_custom_handoff_and_retro(ws):
    (ws / "workspace.yaml").write_text("name: X\nretro:\n  path: docs/lessons.md\nhandoff:\n  path: scratch/h.md\n")
    w = paths.resolve(explicit=ws)
    assert w.retro_path == w.root / "docs/lessons.md"
    assert w.handoff_path == w.root / "scratch/h.md"


def test_transcript_dir_slug(home):
    d = paths.transcript_dir(Path("C:/projects/my cmc"), home)
    assert d.name.endswith("-projects-my-cmc")
    assert d.parent == home / ".claude" / "projects"


def test_env_parse():
    text = '''
# comment
export A=1
B="two words"
C='single # not a comment'
D=value # trailing comment
E="line\\nbreak"
NOEQUALS
'''
    assert env.parse(text) == {"A": "1", "B": "two words", "C": "single # not a comment",
                               "D": "value", "E": "line\nbreak"}


def test_env_load_does_not_override(ws, monkeypatch):
    (ws / ".env").write_text("CC_TEST_A=file\nCC_TEST_B=file\n")
    monkeypatch.setenv("CC_TEST_A", "process")
    monkeypatch.delenv("CC_TEST_B", raising=False)
    env.load(ws)
    import os
    assert os.environ["CC_TEST_A"] == "process"
    assert os.environ["CC_TEST_B"] == "file"


def test_workspace_root_falls_back_to_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert paths.workspace_root() == tmp_path.resolve()


def test_llm_chat_body(monkeypatch):
    from caveman_core import llm
    sent = {}

    def fake_post(url, body, timeout):
        sent.update(url=url, body=body)
        return {"message": {"content": '{"category": "Food"}'}}

    monkeypatch.setattr(llm, "_post", fake_post)
    monkeypatch.setenv("OLLAMA_HOST", "box:11434")
    out = llm.chat_json([{"role": "user", "content": "x"}], "gpt-oss:20b", think="auto")
    assert out == {"category": "Food"}
    assert sent["url"] == "http://box:11434/api/chat"
    assert sent["body"]["think"] == "low" and sent["body"]["format"] == "json" and sent["body"]["stream"] is False
    llm.chat([{"role": "user", "content": "x"}], "qwen3:8b", think="auto")
    assert sent["body"]["think"] is False and "format" not in sent["body"]


def test_env_fills_empty_but_keeps_nonempty(ws, monkeypatch):
    import os
    (ws / ".env").write_text("CC_T_EMPTY=from-file\nCC_T_SET=from-file\n")
    monkeypatch.setenv("CC_T_EMPTY", "")
    monkeypatch.setenv("CC_T_SET", "from-process")
    env.load(ws)
    assert os.environ["CC_T_EMPTY"] == "from-file"
    assert os.environ["CC_T_SET"] == "from-process"


def test_ollama_host_normalisation(monkeypatch):
    from caveman_core import llm
    assert llm.host() == "http://localhost:11434"
    monkeypatch.setenv("OLLAMA_HOST", "0.0.0.0")
    assert llm.host() == "http://127.0.0.1:11434"
    monkeypatch.setenv("OLLAMA_HOST", "0.0.0.0:11500")
    assert llm.host() == "http://127.0.0.1:11500"
    assert llm.host("https://gpu-box:8443/") == "https://gpu-box:8443"


def test_workspace_plugin_settings(run, ws):
    (ws / "workspace.yaml").write_text(
        "name: Acme\nplugins:\n  concierge-git:\n    secondary_repo_path: D:/notes\n    secondary_repo_label: notes\n")
    code, out, _ = run("setting", "get", "concierge-git.secondary_repo_path")
    assert code == 0 and out.strip() == "D:/notes"
    code, out, _ = run("setting", "get", "concierge-git.missing")
    assert code == 0 and out.strip() == ""
    code, out, _ = run("setting", "list")
    assert "concierge-git.secondary_repo_label = notes" in out
    assert run("setting", "get", "nodot")[0] == 2
