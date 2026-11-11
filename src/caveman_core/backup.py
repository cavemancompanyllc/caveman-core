"""``cavecore backup``: dated snapshots of a workspace's data and config.

- Every ``data/**/*.db`` file is copied with SQLite's online backup API, so a
  database in use is captured consistently.
- ``config/`` and ``workspace.yaml`` are copied as files.
- Browser profiles, ``.env`` files and OAuth token files are never copied:
  secrets belong in the password manager, not in backup folders.

Destination: ``--dest``, else ``backup.root`` in workspace.yaml, else
``CAVEMAN_BACKUP_ROOT``. Snapshots land in ``<root>/<workspace-slug>/<YYYY-MM-DD_HHMMSS>``
and only the newest ``--keep`` are kept.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from .paths import Workspace

SKIP_DIRS = {"playwright-profile", "browser-profile", "__pycache__", ".playwright-mcp"}
SKIP_FILE_HINTS = ("token", "credential", "secret", "cookie")


def backup_root(ws: Workspace, dest: str | None) -> Path:
    root = dest or (ws.config.get("backup") or {}).get("root") or os.environ.get("CAVEMAN_BACKUP_ROOT")
    if not root:
        raise ValueError("no backup destination: pass --dest, set backup.root in workspace.yaml, or CAVEMAN_BACKUP_ROOT")
    return Path(os.path.expandvars(os.path.expanduser(root)))


def _skip(p: Path) -> bool:
    return any(part in SKIP_DIRS for part in p.parts) or any(h in p.name.lower() for h in SKIP_FILE_HINTS)


def snapshot(ws: Workspace, dest: str | None = None, keep: int = 14) -> tuple[Path, list[str]]:
    root = backup_root(ws, dest) / ws.slug
    target = root / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    target.mkdir(parents=True, exist_ok=False)
    copied: list[str] = []

    data = ws.root / "data"
    if data.is_dir():
        for db in sorted(data.rglob("*.db")):
            rel = db.relative_to(ws.root)
            if _skip(rel):
                continue
            out = target / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            src = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
            dst = sqlite3.connect(out)
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
            copied.append(str(rel))

    for rel in [Path("workspace.yaml")] + [p.relative_to(ws.root) for p in sorted((ws.root / "config").rglob("*"))
                                            if (ws.root / "config").is_dir() and p.is_file()]:
        src = ws.root / rel
        if src.is_file() and not _skip(rel):
            out = target / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out)
            copied.append(str(rel))

    snaps = sorted((d for d in root.iterdir() if d.is_dir()), key=lambda d: d.name, reverse=True)
    for old in snaps[keep:]:
        shutil.rmtree(old)
    return target, copied
