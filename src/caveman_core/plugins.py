"""Find the plugins enabled for a workspace and where Claude Code installed them.

Used to collect each plugin's ``schema/*.sql`` (ledger), ``rules/session.md``
(SessionStart rules) and ``requires.json`` (doctor). Plugins loaded with
``claude --plugin-dir`` are not recorded anywhere Claude Code writes, so list
their folders in ``CAVEMAN_PLUGIN_DIRS`` (os.pathsep-separated) while developing.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Plugin:
    id: str          # "name@marketplace", or "name@dev" for CAVEMAN_PLUGIN_DIRS entries
    name: str
    root: Path
    version: str | None

    def file(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)


def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def enabled_ids(workspace_root: Path, home: Path | None = None) -> dict[str, bool]:
    """Merge enabledPlugins: user < project < local (later wins), like Claude Code."""
    home = home or Path.home()
    merged: dict[str, bool] = {}
    for f in (home / ".claude" / "settings.json",
              workspace_root / ".claude" / "settings.json",
              workspace_root / ".claude" / "settings.local.json"):
        ep = _read_json(f).get("enabledPlugins")
        if isinstance(ep, dict):
            merged.update({k: bool(v) for k, v in ep.items()})
    return merged


def _same_path(a: str, b: Path) -> bool:
    try:
        return Path(a).resolve() == b.resolve()
    except OSError:
        return False


def installed(workspace_root: Path, home: Path | None = None) -> dict[str, Path]:
    """plugin id -> install dir, for installs visible from this workspace."""
    home = home or Path.home()
    data = _read_json(home / ".claude" / "plugins" / "installed_plugins.json")
    out: dict[str, Path] = {}
    for pid, entries in (data.get("plugins") or {}).items():
        best = None
        for e in entries if isinstance(entries, list) else []:
            scope = e.get("scope")
            if scope in ("project", "local") and not _same_path(e.get("projectPath", ""), workspace_root):
                continue
            # project/local installs for this workspace beat user installs
            if best is None or scope in ("project", "local"):
                best = e
        if best and best.get("installPath"):
            out[pid] = Path(best["installPath"])
    return out


def _manifest(root: Path) -> dict:
    return _read_json(root / ".claude-plugin" / "plugin.json")


def enabled(workspace_root: Path, home: Path | None = None) -> list[Plugin]:
    found: dict[str, Plugin] = {}
    for d in filter(None, os.environ.get("CAVEMAN_PLUGIN_DIRS", "").split(os.pathsep)):
        root = Path(d)
        m = _manifest(root)
        if m.get("name"):
            found[m["name"]] = Plugin(f"{m['name']}@dev", m["name"], root, m.get("version"))
    inst = installed(workspace_root, home)
    for pid, on in enabled_ids(workspace_root, home).items():
        name = pid.split("@", 1)[0]
        if not on or name in found or pid not in inst:
            continue
        root = inst[pid]
        found[name] = Plugin(pid, name, root, _manifest(root).get("version"))
    # caveman-core first, then alphabetical: schemas apply core-first
    return sorted(found.values(), key=lambda p: (p.name != "caveman-core", p.name))
