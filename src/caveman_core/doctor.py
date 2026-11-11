"""``cavecore doctor``: is this machine and workspace set up the way ADR-0004 says?

Each check returns OK, WARN or FAIL with a fix. Exit status is 1 if anything FAILs.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import __version__, env, ledger, paths, plugins


@dataclass
class Check:
    level: str   # OK | WARN | FAIL | INFO
    message: str


def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _git_ignored(root: Path, rel: str) -> bool | None:
    if not (root / ".git").exists():
        return None
    r = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", rel], capture_output=True)
    return r.returncode == 0


def _git_tracked(root: Path, rel: str) -> bool | None:
    if not (root / ".git").exists():
        return None
    r = subprocess.run(["git", "-C", str(root), "ls-files", "--error-unmatch", rel], capture_output=True)
    return r.returncode == 0


def run(explicit_ws: str | None = None, plugin_root: str | None = None, home: Path | None = None) -> list[Check]:
    home = home or Path.home()
    out: list[Check] = [Check("INFO", f"caveman-core package {__version__}")]

    if plugin_root:
        v = _read_json(Path(plugin_root) / ".claude-plugin" / "plugin.json").get("version")
        if v and v.split(".")[:2] != __version__.split(".")[:2]:
            out.append(Check("WARN", f"caveman-core plugin is {v} but the package is {__version__} - reinstall "
                                     f"the package from tag v{v} into the runtime venv"))
        elif v:
            out.append(Check("OK", f"plugin {v} matches package {__version__}"))

    try:
        ws = paths.resolve(explicit_ws)
    except (paths.WorkspaceNotFound, ValueError) as e:
        out.append(Check("FAIL", str(e)))
        return out
    out.append(Check("OK", f"workspace '{ws.name}' at {ws.root} (via {ws.rule})"))
    if not ws.has_config_file:
        out.append(Check("WARN", "no workspace.yaml - run `cavecore workspace init --apply` to add one"))
    if ws.kind not in paths.KINDS:
        out.append(Check("FAIL", f"workspace.yaml kind '{ws.kind}' is not one of {', '.join(paths.KINDS)}"))
    if ws.task_mode not in paths.TASK_MODES:
        out.append(Check("FAIL", f"workspace.yaml tasks '{ws.task_mode}' is not one of {', '.join(paths.TASK_MODES)}"))

    # representative files, not folders: a workspace may track some files under
    # data/ (prompt templates) while ignoring databases and tokens
    for rel in ("data/ledger.db", "data/example.db", "local/handoff.md", ".env"):
        ign = _git_ignored(ws.root, rel)
        if ign is False:
            out.append(Check("FAIL", f"{rel} would be committed - make sure .gitignore covers it"))
    if (ws.root / ".claude" / "settings.json").exists() and _git_tracked(ws.root, ".claude/settings.json") is False:
        out.append(Check("WARN", ".claude/settings.json is not committed - commit it so the plugin list travels "
                                 "with the workspace (and remove it from .gitignore if listed)"))

    if ws.ledger_path.exists():
        try:
            con = ledger.connect(ws, readonly=True)
            try:
                v = ledger.schema_version(con)
            finally:
                con.close()
            level = "OK" if v == ledger.SCHEMA_VERSION else "WARN"
            out.append(Check(level, f"ledger schema v{v} at {ws.ledger_path.relative_to(ws.root)}"))
        except Exception as e:  # noqa: BLE001
            out.append(Check("FAIL", f"ledger: {e}"))
    else:
        out.append(Check("INFO", "no ledger yet - created on first write"))

    env.load(ws.root)
    enabled = plugins.enabled(ws.root, home)
    out.append(Check("INFO", "enabled plugins: " + (", ".join(p.id for p in enabled) or "(none found)")))
    for p in enabled:
        req = _read_json(p.file("requires.json"))
        for cli in req.get("cli", []):
            if not shutil.which(cli):
                out.append(Check("WARN", f"{p.name} needs `{cli}` on PATH"))
        for var in req.get("env", []):
            if not os.environ.get(var):
                out.append(Check("WARN", f"{p.name} needs {var} (set it in {ws.root / '.env'})"))

    sources: dict[str, dict] = {}
    for f in (home / ".claude" / "settings.json", ws.root / ".claude" / "settings.json"):
        for name, spec in (_read_json(f).get("extraKnownMarketplaces") or {}).items():
            sources[name] = (spec or {}).get("source") or {}
    for name, spec in (_read_json(home / ".claude" / "plugins" / "known_marketplaces.json")).items():
        sources.setdefault(name, (spec or {}).get("source") or {})
    for name, src in sorted(sources.items()):
        if src.get("source") == "directory":
            level = "INFO" if ws.kind in ("dev", "sandbox") else "WARN"
            out.append(Check(level, f"marketplace '{name}' installs from a local folder ({src.get('path')}) - "
                                    "workspaces should install from GitHub so edits aren't live before release"))

    user_cfg = _read_json(home / ".claude" / "settings.json").get("pluginConfigs") or {}
    shared = sorted(k for k in user_cfg if k.split("@")[0] in {p.name for p in enabled})
    if shared:
        out.append(Check("INFO", "user-scope plugin settings apply to every workspace: " + ", ".join(shared)
                         + " - keep workspace-specific values (vault paths, ports, IDs) in this workspace's .env"))
    return out


def format_checks(checks: list[Check]) -> str:
    return "\n".join(f"[{c.level:4}] {c.message}" for c in checks)
