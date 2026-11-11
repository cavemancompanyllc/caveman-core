"""``cavecore guard pre-push``: stop personal data and secrets reaching a public repo.

Install as a git pre-push hook in the local clone of a public repo::

    cavecore guard install            # writes .git/hooks/pre-push

On every push it checks the commits being pushed (added lines and commit
messages) against:

- a **deny-list** kept outside every repo, at ``CAVEMAN_DENYLIST`` or
  ``~/.caveman/denylist.txt``: one term per line, matched case-insensitively;
  prefix a line with ``re:`` for a regular expression; ``#`` starts a comment.
  It holds the very words that must never be published (family names, league
  names, local paths), so it must never be committed anywhere itself.
- **gitleaks**, when it is installed, for secrets.

Matches are reported by commit and file with the term, not the full line.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ZERO = "0" * 40
HOOK = """#!/bin/sh
# Installed by `cavecore guard install`: deny-list + secret scan before every push.
exec python -m caveman_core guard pre-push "$@"
"""


def denylist_path() -> Path:
    return Path(os.environ.get("CAVEMAN_DENYLIST") or Path.home() / ".caveman" / "denylist.txt")


def load_denylist(path: Path | None = None) -> list[tuple[str, re.Pattern]]:
    path = path or denylist_path()
    if not path.is_file():
        return []
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("re:"):
            out.append((line, re.compile(line[3:], re.IGNORECASE)))
        else:
            out.append((line, re.compile(re.escape(line), re.IGNORECASE)))
    return out


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=True).stdout


def ranges_from_stdin(repo: Path, stdin_text: str) -> list[str]:
    """Turn git's pre-push stdin (<local ref> <local sha> <remote ref> <remote sha>) into rev ranges."""
    out = []
    for ln in stdin_text.splitlines():
        parts = ln.split()
        if len(parts) != 4 or parts[1] == ZERO:      # deleting a ref pushes no content
            continue
        local, remote = parts[1], parts[3]
        out.append(f"{remote}..{local}" if remote != ZERO else f"{local} --not --remotes")
    return out


def scan_range(repo: Path, rng: str, deny: list[tuple[str, re.Pattern]]) -> list[str]:
    args = rng.split()
    hits: list[str] = []
    patch = _git(repo, "log", "-p", "--no-color", "--format=@@COMMIT %h", *args)
    commit, path = "?", "?"
    for ln in patch.splitlines():
        if ln.startswith("@@COMMIT "):
            commit = ln.split()[1]
        elif ln.startswith("+++ "):
            path = ln[6:] if ln.startswith("+++ b/") else ln[4:]
        elif ln.startswith("+") and not ln.startswith("+++"):
            for term, rx in deny:
                if rx.search(ln):
                    hits.append(f"{commit} {path}: matches deny-list term {term!r}")
    messages = _git(repo, "log", "--format=@@COMMIT %h%n%B", *args)
    for ln in messages.splitlines():
        if ln.startswith("@@COMMIT "):
            commit = ln.split()[1]
            continue
        for term, rx in deny:
            if rx.search(ln):
                hits.append(f"{commit} commit message: matches deny-list term {term!r}")
    return sorted(set(hits))


def gitleaks_exe() -> str | None:
    exe = shutil.which("gitleaks")
    if exe:
        return exe
    root = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    if root.is_dir():
        found = next(root.rglob("gitleaks.exe"), None)
        return str(found) if found else None
    return None


def pre_push(repo: Path, stdin_text: str) -> int:
    deny = load_denylist()
    if not deny:
        print(f"guard: no deny-list at {denylist_path()} - personal-data check skipped", file=sys.stderr)
    problems: list[str] = []
    ranges = ranges_from_stdin(repo, stdin_text)
    for rng in ranges:
        problems += scan_range(repo, rng, deny)
    gl = gitleaks_exe()
    if gl:
        for rng in ranges:
            r = subprocess.run([gl, "git", str(repo), f"--log-opts={rng}", "--no-banner", "--redact",
                                "--log-level", "error"], capture_output=True, text=True)
            if r.returncode != 0:
                problems.append(f"gitleaks found possible secrets in {rng}:\n{(r.stdout + r.stderr).strip()[:800]}")
    else:
        print("guard: gitleaks not installed - secret scan skipped", file=sys.stderr)
    if problems:
        print("guard: push blocked - personal data or secrets in the commits being pushed:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        print("Remove them (rewrite the commits) and push again. Bypass only if certain: git push --no-verify",
              file=sys.stderr)
        return 1
    return 0


def install(repo: Path) -> Path:
    hooks = Path(_git(repo, "rev-parse", "--git-path", "hooks").strip())
    if not hooks.is_absolute():
        hooks = repo / hooks
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-push"
    hook.write_text(HOOK, encoding="utf-8", newline="\n")
    try:
        hook.chmod(0o755)
    except OSError:
        pass
    return hook
