import json
from pathlib import Path

import pytest

from caveman_core import cli


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    """Fake home and no inherited workspace env vars for every test."""
    home = tmp_path / "home"
    (home / ".claude" / "plugins").mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    for var in ("CAVEMAN_HOME", "CONCIERGE_HOME", "CLAUDE_PROJECT_DIR", "CAVEMAN_PLUGIN_DIRS",
                "CAVEMAN_BACKUP_ROOT", "OLLAMA_HOST", "CAVEMAN_LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)
    return home


@pytest.fixture
def home(isolated):
    return isolated


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "acme-workspace"
    root.mkdir()
    (root / "workspace.yaml").write_text("name: Acme\nslug: acme\nkind: business\ntasks: ledger\n", encoding="utf-8")
    return root


@pytest.fixture
def run(ws, capsys):
    """Run the CLI against the test workspace; return (exit_code, stdout, stderr)."""
    def _run(*args, workspace=ws):
        argv = (["--workspace", str(workspace)] if workspace else []) + [str(a) for a in args]
        code = cli.main(argv)
        out = capsys.readouterr()
        return code, out.out, out.err
    return _run


def write_plugin(root: Path, name: str, version="1.0.0", **files) -> Path:
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": name, "version": version}), encoding="utf-8")
    for rel, content in files.items():
        f = root / rel.replace("__", "/")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
    return root
