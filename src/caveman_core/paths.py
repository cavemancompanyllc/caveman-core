"""One resolver for "which workspace am I in?".

Order (first match wins):

1. explicit ``--workspace`` value
2. ``CAVEMAN_HOME`` environment variable
3. ``CONCIERGE_HOME`` environment variable (legacy, read-only fallback)
4. walk up from ``CLAUDE_PROJECT_DIR`` looking for ``workspace.yaml``
5. walk up from the current directory looking for ``workspace.yaml``

``cavecore which`` prints the result and the rule that fired, so a wrong
answer is easy to diagnose.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

WORKSPACE_FILE = "workspace.yaml"
LEDGER_RELATIVE = Path("data") / "ledger.db"
HANDOFF_RELATIVE = Path("local") / "handoff.md"

KINDS = ("personal", "business", "kids", "dev", "sandbox")
TASK_MODES = ("ledger", "ado", "none")


class WorkspaceNotFound(RuntimeError):
    pass


@dataclass(frozen=True)
class Workspace:
    root: Path
    rule: str
    config: dict

    @property
    def name(self) -> str:
        return self.config.get("name") or self.root.name

    @property
    def slug(self) -> str:
        return self.config.get("slug") or self.root.name.lower()

    @property
    def kind(self) -> str:
        return self.config.get("kind", "personal")

    @property
    def task_mode(self) -> str:
        return self.config.get("tasks", "ledger")

    @property
    def ledger_path(self) -> Path:
        return self.root / LEDGER_RELATIVE

    @property
    def handoff_path(self) -> Path:
        rel = (self.config.get("handoff") or {}).get("path")
        return self.root / (rel or HANDOFF_RELATIVE)

    @property
    def retro_path(self) -> Path | None:
        rel = (self.config.get("retro") or {}).get("path")
        return self.root / rel if rel else None

    @property
    def has_config_file(self) -> bool:
        return (self.root / WORKSPACE_FILE).exists()


def load_config(root: Path) -> dict:
    f = root / WORKSPACE_FILE
    if not f.exists():
        return {}
    data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{f}: expected a mapping at the top level")
    return data


def _walk_up(start: Path) -> Path | None:
    start = start.resolve()
    for d in (start, *start.parents):
        if (d / WORKSPACE_FILE).is_file():
            return d
    return None


def resolve(explicit: str | os.PathLike | None = None, cwd: Path | None = None) -> Workspace:
    """Resolve the active workspace or raise WorkspaceNotFound with the fix."""
    candidates: list[tuple[str, Path | None]] = []
    if explicit:
        candidates.append(("--workspace", Path(explicit)))
    for var in ("CAVEMAN_HOME", "CONCIERGE_HOME"):
        if os.environ.get(var):
            candidates.append((var, Path(os.environ[var])))
    for rule, path in candidates:
        if not path.is_dir():
            raise WorkspaceNotFound(f"{rule} points at {path}, which is not a directory")
        root = path.resolve()
        return Workspace(root=root, rule=rule, config=load_config(root))

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir:
        found = _walk_up(Path(project_dir))
        if found:
            return Workspace(root=found, rule="CLAUDE_PROJECT_DIR walk-up", config=load_config(found))
    found = _walk_up(cwd or Path.cwd())
    if found:
        return Workspace(root=found, rule="cwd walk-up", config=load_config(found))
    raise WorkspaceNotFound(
        "no workspace found: no --workspace, CAVEMAN_HOME or CONCIERGE_HOME, and no "
        f"{WORKSPACE_FILE} in this directory or any parent. Create one with "
        "`cavecore workspace init --name <name> --kind <kind> --apply`."
    )


def workspace_root(explicit: str | os.PathLike | None = None) -> Path:
    """The workspace root for a plugin script, never raising.

    Same order as :func:`resolve`, but falls back to the current directory when
    no workspace is found - the behaviour plugin scripts had before workspaces
    carried a ``workspace.yaml``, since Claude Code runs Bash in the project root.
    """
    try:
        return resolve(explicit).root
    except WorkspaceNotFound:
        return Path.cwd().resolve()


def transcript_dir(root: Path, home: Path | None = None) -> Path:
    """Claude Code's transcript folder for a project root.

    Claude Code names it after the absolute path with every character that is
    not a letter or digit replaced by '-', e.g. ``E:\\projects\\cmc`` ->
    ``E--projects-cmc``.
    """
    slug = "".join(ch if ch.isalnum() else "-" for ch in str(root.resolve()))
    return (home or Path.home()) / ".claude" / "projects" / slug


def latest_transcript(root: Path, home: Path | None = None) -> Path | None:
    d = transcript_dir(root, home)
    if not d.is_dir():
        return None
    files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None
