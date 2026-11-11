"""``cavecore run <plugin> <script> [args...]``: run a plugin's script by name.

Claude Code installs plugins into version-numbered cache folders and prunes old
versions on update, so a scheduled prompt, an ``--allowedTools`` rule or a
wrapper that names ``.../concierge-family/0.1.0/scripts/school_db.py`` breaks
the moment the plugin is updated. This resolves the plugin enabled for the
current workspace at run time and executes its script with the same Python, so
callers never name a version::

    python -m caveman_core run concierge-family school_db.py kv last_scan

stdin, stdout, stderr and the exit code pass straight through.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import plugins
from .paths import Workspace


class RunError(RuntimeError):
    pass


def find_script(ws: Workspace, plugin: str, script: str) -> Path:
    if Path(script).name != script:
        raise RunError(f"script must be a file name inside the plugin's scripts/ folder, not a path: {script}")
    matches = [p for p in plugins.enabled(ws.root) if p.name == plugin]
    if not matches:
        raise RunError(f"plugin '{plugin}' is not installed and enabled for workspace {ws.root} "
                       "(see `claude plugin list`)")
    path = matches[0].file("scripts", script)
    if not path.is_file():
        raise RunError(f"{plugin} {matches[0].version or ''} has no scripts/{script}")
    return path


def run(ws: Workspace, plugin: str, script: str, args: list[str], log: Path | None = None) -> int:
    """Run the script. With ``log``, append a timestamped header plus the script's
    stdout and stderr to that file (relative paths are under the workspace), so a
    scheduled task needs no wrapper script just to capture output."""
    path = find_script(ws, plugin, script)
    cmd = [sys.executable, str(path), *args]
    if log is None:
        return subprocess.call(cmd)
    log = log if log.is_absolute() else ws.root / log
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {plugin} {script} {' '.join(args)}\n")
        fh.flush()
        code = subprocess.call(cmd, cwd=ws.root, stdout=fh, stderr=subprocess.STDOUT,
                               env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        fh.write(f"[exit {code}]\n")
    return code
