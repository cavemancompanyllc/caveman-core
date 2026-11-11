import json
import sys
from pathlib import Path

from caveman_core import headless, paths, usage


def test_allowed_tools_sidecar_and_default(tmp_path):
    p = tmp_path / "scan_prompts" / "weekly.txt"
    p.parent.mkdir()
    p.write_text("hi")
    assert headless.allowed_tools(p, None) == []                      # never a broad default
    (p.parent / "weekly.txt.allowedtools").write_text("WebSearch\n# comment\n\nRead\n")
    assert headless.allowed_tools(p, None) == ["WebSearch", "Read"]
    assert headless.allowed_tools(p, "A, B") == ["A", "B"]


def test_headless_runs_fake_claude(ws, tmp_path):
    fake = tmp_path / "fake_claude.py"
    fake.write_text("import sys, os\nprint('ARGS', sys.argv[1:])\nprint('PROMPT', sys.stdin.read())\n"
                    "print('HOME', os.environ.get('CAVEMAN_HOME'))\n")
    launcher = tmp_path / ("fake_claude.cmd" if sys.platform == "win32" else "fake_claude.sh")
    if sys.platform == "win32":
        launcher.write_text(f'@"{sys.executable}" "{fake}" %*\n')
    else:
        launcher.write_text(f'#!/bin/sh\n"{sys.executable}" "{fake}" "$@"\n')
        launcher.chmod(0o755)
    prompts = ws / "data" / "scan_prompts"
    prompts.mkdir(parents=True)
    pf = prompts / "weekly.txt"
    pf.write_text("say \"hi\" — please", encoding="utf-8")
    w = paths.resolve(explicit=ws)
    code = headless.run(w, pf, tools="Read", claude=str(launcher))
    assert code == 0
    log = next((ws / "data" / "scan_logs").glob("*_weekly.log")).read_text(encoding="utf-8")
    assert "--allowedTools" in log and "'Read'" in log
    assert 'say "hi" — please' in log
    assert str(w.root) in log


def test_usage_report_dedups(tmp_path):
    proj = tmp_path / "projects" / "x"
    proj.mkdir(parents=True)
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    line = {"type": "assistant", "timestamp": ts,
            "message": {"id": "a", "model": "m", "usage": {"input_tokens": 3, "output_tokens": 2}}}
    (proj / "s.jsonl").write_text("\n".join([json.dumps(line)] * 3) + "\nnot json\n")
    out = usage.report(tmp_path / "projects", days=1)
    assert "in            3" in out and "out          2" in out


def test_headless_relative_prompt_workspace_before_and_folder_default(ws, tmp_path):
    fake = tmp_path / "fake2.py"
    fake.write_text("import sys\nprint('ARGS', sys.argv[1:])\n")
    launcher = tmp_path / ("fake2.cmd" if sys.platform == "win32" else "fake2.sh")
    if sys.platform == "win32":
        launcher.write_text(f'@"{sys.executable}" "{fake}" %*\n')
    else:
        launcher.write_text(f'#!/bin/sh\n"{sys.executable}" "{fake}" "$@"\n')
        launcher.chmod(0o755)
    (ws / "workspace.yaml").write_text('name: Acme\nheadless:\n  before: "echo prepared"\n')
    d = ws / "data" / "pre_prompts"
    d.mkdir(parents=True)
    (d / "one.txt").write_text("hi", encoding="utf-8")
    (d / "default.allowedtools").write_text("WebSearch\n", encoding="utf-8")
    w = paths.resolve(explicit=ws)
    assert headless.run(w, Path("data/pre_prompts/one.txt"), claude=str(launcher)) == 0
    log = next((ws / "data" / "pre_logs").glob("*_one.log")).read_text(encoding="utf-8")
    assert log.startswith("[headless]") and "'WebSearch'" in log and "[before] exit 0: prepared" in log


def test_run_with_log(ws, home, tmp_path):
    from conftest import write_plugin
    from caveman_core import cli
    plug = write_plugin(tmp_path / "cache" / "p" / "1.0.0", "p", **{"scripts__s.py": "print('out line')\n"})
    (home / ".claude" / "plugins" / "installed_plugins.json").write_text(json.dumps({"plugins": {
        "p@m": [{"scope": "project", "projectPath": str(ws), "installPath": str(plug)}]}}))
    (ws / ".claude").mkdir()
    (ws / ".claude" / "settings.json").write_text(json.dumps({"enabledPlugins": {"p@m": True}}))
    assert cli.main(["--workspace", str(ws), "run", "--log", "data/x.log", "p", "s.py", "--flag"]) == 0
    text = (ws / "data" / "x.log").read_text(encoding="utf-8")
    assert "p s.py --flag" in text and "out line" in text and "[exit 0]" in text
