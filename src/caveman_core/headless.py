"""``cavecore headless``: run one unattended Claude Code session from a prompt file.

Made for Windows Task Scheduler (or cron). It lives in the package, not the
plugin, because scheduled tasks need a command path that doesn't change when
a plugin is updated - plugin folders are versioned, ``cavecore`` on PATH isn't.

- The prompt is piped over stdin (command-line quoting mangles prompts).
- An unattended run has nobody to approve tool prompts, so the caller must
  pre-authorise exactly the tools the prompt needs: ``--allowed-tools`` or a
  ``<prompt-file>.allowedtools`` sidecar (one entry per line). The default is
  no tools - never a broad grant, since a page or email read during the run
  could try to steer it.
- Output goes to ``<log-dir>/<timestamp>_<prompt name>.log``; the default
  log dir mirrors the prompt dir (``data/x_prompts`` -> ``data/x_logs``).
- ``--before`` runs a preparatory command first (e.g. start an app an MCP
  server needs); its failure is logged, not fatal. Without ``--before``, the
  workspace.yaml ``headless: {before: "..."}`` command is used, if set.
- A relative ``--prompt-file`` is resolved against the workspace root, which
  keeps scheduled-task commands under Windows' 261-character limit.
- If a prompt has no ``.allowedtools`` sidecar, ``default.allowedtools`` in the
  same folder applies (one list for a folder of generated prompts).
- ``--delete-task`` removes a one-shot scheduled task after it fires.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from .paths import Workspace


def allowed_tools(prompt_file: Path, explicit: str | None) -> list[str]:
    if explicit:
        return [t.strip() for t in explicit.split(",") if t.strip()]
    # per-prompt sidecar first, then a folder default shared by every prompt in it
    for sidecar in (prompt_file.with_name(prompt_file.name + ".allowedtools"),
                    prompt_file.with_name("default.allowedtools")):
        if sidecar.is_file():
            return [ln.strip() for ln in sidecar.read_text(encoding="utf-8").splitlines()
                    if ln.strip() and not ln.lstrip().startswith("#")]
    return []


def default_log_dir(ws: Workspace, prompt_file: Path) -> Path:
    parent = prompt_file.parent
    if parent.name.endswith("_prompts"):
        return parent.with_name(parent.name[: -len("_prompts")] + "_logs")
    return ws.root / "data" / "headless_logs"


def run(ws: Workspace, prompt_file: Path, tools: str | None = None, log_dir: Path | None = None,
        before: str | None = None, delete_task: str | None = None, claude: str | None = None) -> int:
    if not prompt_file.is_absolute():
        prompt_file = ws.root / prompt_file          # short task commands: paths relative to the workspace
    prompt_file = prompt_file.resolve()
    prompt = prompt_file.read_text(encoding="utf-8-sig")
    if before is None:
        before = (ws.config.get("headless") or {}).get("before")
    log_dir = log_dir or default_log_dir(ws, prompt_file)
    log_dir.mkdir(parents=True, exist_ok=True)
    log = log_dir / f"{datetime.now():%Y%m%d_%H%M%S}_{prompt_file.stem}.log"
    exe = claude or shutil.which("claude")
    if not exe:
        log.write_text("[headless] `claude` not found on PATH\n", encoding="utf-8")
        return 127

    notes: list[str] = []
    if before:
        r = subprocess.run(before, shell=True, cwd=ws.root, capture_output=True, text=True)
        notes.append(f"[before] exit {r.returncode}: {(r.stdout + r.stderr).strip()[:500]}")

    cmd = [exe, "-p", "--output-format", "text"]
    granted = allowed_tools(prompt_file, tools)
    if granted:
        cmd += ["--allowedTools", ",".join(granted)]
    env = {**os.environ, "CAVEMAN_HOME": str(ws.root)}
    with log.open("w", encoding="utf-8") as fh:
        fh.write(f"[headless] {datetime.now().isoformat(timespec='seconds')} prompt={prompt_file} "
                 f"tools={','.join(granted) or '(none)'}\n")
        fh.flush()                                   # header before the child's output
        proc = subprocess.run(cmd, input=prompt, text=True, encoding="utf-8", cwd=ws.root,
                              stdout=fh, stderr=subprocess.STDOUT, env=env)
        for n in notes:
            fh.write(f"\n{n}\n")
        fh.write(f"\n[headless] claude exit {proc.returncode}\n")

    if delete_task and sys.platform == "win32":
        subprocess.run(["schtasks", "/delete", "/tn", delete_task, "/f"], capture_output=True)
    return proc.returncode
