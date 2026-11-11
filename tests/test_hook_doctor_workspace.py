import json
import sqlite3

from conftest import write_plugin

from caveman_core import hook, paths, workspace


def test_hook_without_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert "no workspace.yaml" in hook.session_start_text()


def test_hook_shows_last_session_and_plugin_rules(run, ws, tmp_path, monkeypatch):
    run("work", "project", "add", "p", "P")
    run("work", "effort", "add", "p", "e1", "E1")
    run("work", "session", "log", "e1", "--summary", "did X", "--current-state", "c", "--next-steps", "do Y next",
        "--outcome", "pushed")
    plug = write_plugin(tmp_path / "plug", "demo-plugin", **{"rules__session.md": "- Always do the demo thing.\n"})
    monkeypatch.setenv("CAVEMAN_PLUGIN_DIRS", str(plug))
    monkeypatch.setenv("CAVEMAN_HOME", str(ws))
    text = hook.session_start_text()
    assert "Workspace: Acme (business)" in text
    assert "did X" in text and "do Y next" in text and "Outcome: pushed" in text
    assert "### demo-plugin" in text and "demo thing" in text
    assert len(text) <= hook.MAX_TOTAL


def test_hook_caps_long_rules(ws, tmp_path, monkeypatch):
    rules = "\n".join(f"- rule {i} " + "x" * 400 for i in range(100))
    plugs = [write_plugin(tmp_path / f"p{i}", f"plug{i}", **{"rules__session.md": rules}) for i in range(5)]
    monkeypatch.setenv("CAVEMAN_PLUGIN_DIRS", ";".join(map(str, plugs)) if __import__("os").pathsep == ";"
                       else ":".join(map(str, plugs)))
    monkeypatch.setenv("CAVEMAN_HOME", str(ws))
    text = hook.session_start_text()
    assert len(text) <= hook.MAX_TOTAL and text.endswith("rules/session.md)")


def test_installed_plugin_schema_applied(run, ws, home, tmp_path):
    plug = write_plugin(tmp_path / "cache" / "garden" / "1.0.0", "garden",
                        **{"schema__garden.sql": "CREATE TABLE IF NOT EXISTS beds (id INTEGER PRIMARY KEY);"})
    (home / ".claude" / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {
        "garden@acme": [{"scope": "project", "projectPath": str(ws), "installPath": str(plug), "version": "1.0.0"}],
        "other@acme": [{"scope": "project", "projectPath": str(tmp_path / "elsewhere"), "installPath": str(plug)}],
    }}))
    (ws / ".claude").mkdir()
    (ws / ".claude" / "settings.json").write_text(json.dumps({"enabledPlugins": {"garden@acme": True, "other@acme": True}}))
    code, out, err = run("ledger", "init")
    assert code == 0, err
    assert "applied garden: garden.sql" in out
    con = sqlite3.connect(ws / "data" / "ledger.db")
    assert con.execute("SELECT name FROM sqlite_master WHERE name='beds'").fetchone()


def test_doctor(run, ws, home):
    (home / ".claude" / "settings.json").write_text(json.dumps({"extraKnownMarketplaces": {
        "local-mp": {"source": {"source": "directory", "path": "C:/dev/mp"}}}}))
    code, out, _ = run("doctor")
    assert "workspace 'Acme'" in out
    assert "installs from a local folder" in out and "[WARN]" in out


def test_doctor_fails_without_workspace(run, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, out, _ = run("doctor", workspace=None)
    assert code == 1 and "[FAIL]" in out


def test_workspace_init_dry_run_then_apply(tmp_path, run):
    root = tmp_path / "newws"
    root.mkdir()
    (root / ".gitignore").write_text("node_modules/\ndata/\n")
    (root / ".claude").mkdir()
    (root / ".claude" / "settings.json").write_text(json.dumps({"enabledPlugins": {"x@y": True}}))
    code, out, _ = run("workspace", "init", "--dir", root, "--name", "New WS", "--kind", "kids", workspace=None)
    assert code == 0 and "dry run" in out and not (root / "workspace.yaml").exists()
    code, out, _ = run("workspace", "init", "--dir", root, "--name", "New WS", "--kind", "kids", "--apply", workspace=None)
    assert code == 0
    w = paths.resolve(explicit=root)
    assert (w.slug, w.kind) == ("new-ws", "kids")
    gi = (root / ".gitignore").read_text()
    assert gi.count("data/") == 1 and "local/" in gi and "node_modules/" in gi
    st = json.loads((root / ".claude" / "settings.json").read_text())
    assert st["enabledPlugins"] == {"x@y": True, "caveman-core@caveman-core": True}
    assert st["extraKnownMarketplaces"]["caveman-core"]["source"]["repo"] == "cavemancompanyllc/caveman-core"
    # second run changes nothing
    steps = workspace.plan(root, "New WS", "kids")
    assert all(a == "skip" for a, _, _ in steps)


def test_backup(run, ws, tmp_path):
    run("work", "project", "add", "p", "P")
    (ws / "config").mkdir()
    (ws / "config" / "school.json").write_text("{}")
    (ws / "data" / "google_token.json").write_text("secret")
    (ws / "data" / "playwright-profile").mkdir()
    sqlite3.connect(ws / "data" / "playwright-profile" / "cookies.db").close()
    dest = tmp_path / "bk"
    code, out, err = run("backup", "--dest", dest, "--keep", "1")
    assert code == 0, err
    snap = next((dest / "acme").iterdir())
    assert (snap / "data" / "ledger.db").exists() and (snap / "config" / "school.json").exists()
    assert not (snap / "data" / "playwright-profile").exists()
    assert not list(snap.rglob("*token*"))
