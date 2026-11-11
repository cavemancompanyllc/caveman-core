"""``cavecore workspace init``: scaffold the ADR-0004 workspace layout.

Never overwrites: existing files are left alone, ``.gitignore`` only gains
missing lines, and ``.claude/settings.json`` only gains the caveman-core
marketplace and plugin. Without ``--apply`` it prints the plan.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from . import paths

CORE_MARKETPLACE = {"source": {"source": "github", "repo": "cavemancompanyllc/caveman-core"}}
CORE_PLUGIN = "caveman-core@caveman-core"

# Root-anchored (leading /) so a workspace that is also a code repo with
# nested data/ or config/ folders (a monorepo's apps) is not affected.
GITIGNORE_LINES = [
    "# caveman-core workspace (ADR-0004): data, local notes and secrets stay out of git",
    "/data/",
    "/data-backups/",
    "/local/",
    ".env",
    ".claude/settings.local.json",
    "/config/*.json",
    "!/config/*.example.json",
]

CLAUDE_MD = """# {name}

<One or two sentences: what this workspace is for and who it works for.>

## Rules for this workspace

- <Only rules that are specific to this workspace. Rules that come with a plugin
  belong in that plugin's rules/session.md and load automatically at session start.>

Session history lives in the ledger (`cavecore work status`); end sessions with
`/caveman-core:handoff`.
"""

ENV_EXAMPLE = """# Copy to .env (gitignored) and fill in. Workspace-specific values only:
# anything that differs between workspaces (vault paths, ports, account IDs)
# belongs here, not in user-level plugin settings.
"""

BOOTSTRAP = r"""# Install this workspace's plugins on a new machine. Safe to re-run.
# Usage: pwsh -NoProfile -File bootstrap.ps1
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$settings = Get-Content .claude/settings.json -Raw | ConvertFrom-Json
$known = (claude plugin marketplace list 2>$null) -join "`n"
foreach ($m in $settings.extraKnownMarketplaces.PSObject.Properties) {
    $src = $m.Value.source
    if ($known -notmatch [regex]::Escape($m.Name)) {
        if ($src.source -eq 'github') { claude plugin marketplace add $src.repo }
        elseif ($src.url) { claude plugin marketplace add $src.url }
        else { Write-Warning "marketplace $($m.Name): add it by hand ($($src.source))" }
    }
}
claude plugin marketplace update
foreach ($p in $settings.enabledPlugins.PSObject.Properties) {
    if ($p.Value) { claude plugin install $p.Name --scope project }
}
if (Get-Command cavecore -ErrorAction SilentlyContinue) { cavecore doctor }
else { Write-Warning 'cavecore is not on PATH - install the caveman-core package (see its README), then run: cavecore doctor' }
"""


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "workspace"


def plan(root: Path, name: str, kind: str, slug: str | None = None, tasks: str = "ledger",
         retro_path: str | None = None) -> list[tuple[str, Path, str | None]]:
    """Return (action, path, new_content) tuples. action: create | append | merge | mkdir | skip."""
    if kind not in paths.KINDS:
        raise ValueError(f"kind must be one of {', '.join(paths.KINDS)}")
    if tasks not in paths.TASK_MODES:
        raise ValueError(f"tasks must be one of {', '.join(paths.TASK_MODES)}")
    steps: list[tuple[str, Path, str | None]] = []

    cfg = {"name": name, "slug": slug or _slugify(name), "kind": kind, "tasks": tasks}
    if retro_path:
        cfg["retro"] = {"path": retro_path}
    files = {
        root / paths.WORKSPACE_FILE: yaml.safe_dump(cfg, sort_keys=False),
        root / "CLAUDE.md": CLAUDE_MD.format(name=name),
        root / ".env.example": ENV_EXAMPLE,
        root / "bootstrap.ps1": BOOTSTRAP,
        root / "config" / ".gitkeep": "",
    }
    for f, content in files.items():
        steps.append(("skip" if f.exists() else "create", f, None if f.exists() else content))

    gi = root / ".gitignore"
    existing = gi.read_text(encoding="utf-8").splitlines() if gi.exists() else []
    have = {e.strip() for e in existing}

    def has_own_rules(folder: str) -> bool:
        # an existing workspace that already manages data/ or config/ selectively
        # (e.g. tracks prompt templates under data/) keeps its own rules
        return any(e.lstrip("/!").startswith(folder + "/") for e in have if not e.startswith("#"))

    def bare(rule: str) -> str:
        return rule.lstrip("!").lstrip("/")

    have_bare = {bare(e) for e in have}
    missing = [ln for ln in GITIGNORE_LINES[1:] if bare(ln) not in have_bare
               and not (bare(ln).startswith(("data/", "data-backups/")) and has_own_rules("data"))
               and not (bare(ln).startswith("config/") and has_own_rules("config"))]
    if missing:
        steps.append(("append" if gi.exists() else "create", gi, "\n".join([GITIGNORE_LINES[0], *missing]) + "\n"))

    st = root / ".claude" / "settings.json"
    data = json.loads(st.read_text(encoding="utf-8")) if st.exists() else {}
    changed = False
    mk = data.setdefault("extraKnownMarketplaces", {})
    if "caveman-core" not in mk:
        mk["caveman-core"] = CORE_MARKETPLACE
        changed = True
    ep = data.setdefault("enabledPlugins", {})
    if CORE_PLUGIN not in ep:
        ep[CORE_PLUGIN] = True
        changed = True
    if changed:
        steps.append(("merge" if st.exists() else "create", st, json.dumps(data, indent=2) + "\n"))

    for d in ("data", "local"):
        if not (root / d).exists():
            steps.append(("mkdir", root / d, None))
    return steps


def apply(steps: list[tuple[str, Path, str | None]]) -> None:
    for action, path, content in steps:
        if action == "skip":
            continue
        if action == "mkdir":
            path.mkdir(parents=True, exist_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if action == "append":
            prev = path.read_text(encoding="utf-8")
            sep = "" if prev.endswith("\n") or not prev else "\n"
            path.write_text(prev + sep + "\n" + content, encoding="utf-8")
        else:
            path.write_text(content or "", encoding="utf-8")
