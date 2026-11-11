"""``cavecore``: the CLI every Caveman plugin calls.

    cavecore which                       which workspace, and which rule found it
    cavecore work ...                    projects / efforts / sessions / tasks / decisions
    cavecore ledger init|info|sql|import-legacy
    cavecore workspace init              scaffold a workspace (dry run unless --apply)
    cavecore doctor                      check machine + workspace setup
    cavecore backup                      snapshot data/*.db and config/
    cavecore usage                       token usage across transcripts
    cavecore eval ...                    subagent eval harness
    cavecore run <plugin> <script> ...   run a plugin script, whatever version is installed
    cavecore headless --prompt-file F    one unattended Claude run (Task Scheduler)
    cavecore hook session-start          SessionStart context (used by the plugin)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, backup, doctor, evals, guard, headless, hook, ledger, legacy, paths, runner, usage, work, workspace


def _ws(a) -> paths.Workspace:
    return paths.resolve(a.workspace)


def cmd_which(a) -> int:
    ws = _ws(a)
    print(f"workspace: {ws.name} ({ws.kind})\nroot:      {ws.root}\nrule:      {ws.rule}\n"
          f"ledger:    {ws.ledger_path}\nhandoff:   {ws.handoff_path}\ntasks:     {ws.task_mode}")
    if ws.retro_path:
        print(f"retro:     {ws.retro_path}")
    t = paths.latest_transcript(ws.root)
    print(f"transcript (newest): {t or '(none found)'}")
    return 0


def cmd_setting(a) -> int:
    """Non-secret per-workspace plugin settings from workspace.yaml `plugins:`."""
    ws = _ws(a)
    if a.setting_cmd == "get":
        plugin, _, key = a.name.partition(".")
        if not key:
            print("error: name must be <plugin>.<key>, e.g. concierge-git.secondary_repo_path", file=sys.stderr)
            return 2
        value = ws.setting(plugin, key)
        print("" if value is None else value)
        return 0
    for plugin, section in sorted((ws.config.get("plugins") or {}).items()):
        if a.plugin and plugin != a.plugin:
            continue
        for key, value in (section or {}).items():
            print(f"{plugin}.{key} = {value}")
    return 0


def cmd_ledger(a) -> int:
    ws = _ws(a)
    if a.ledger_cmd == "init":
        con = ledger.connect(ws, sync=True)
        print(f"ledger ready at {ws.ledger_path} (schema v{ledger.schema_version(con)})")
        for name, f in ledger.plugin_schemas(ws):
            print(f"  applied {name}: {f.name}")
        con.close()
    elif a.ledger_cmd == "info":
        con = ledger.connect(ws, readonly=True)
        print(f"{ws.ledger_path}  schema v{ledger.schema_version(con)}")
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for t in tables:
            n = con.execute('SELECT COUNT(*) FROM "' + t + '"').fetchone()[0]
            print(f"  {t:24} {n:>6} rows")
        for k, v in con.execute("SELECT key, value FROM meta WHERE key LIKE 'imported:%'"):
            print(f"  {k} at {v}")
        con.close()
    elif a.ledger_cmd == "sql":
        rows = ledger.query_readonly(ws, a.query)
        if rows:
            cols = rows[0].keys()
            print("\t".join(cols))
            for r in rows:
                print("\t".join("" if r[c] is None else str(r[c]) for c in cols))
        else:
            print("(no rows)")
    elif a.ledger_cmd == "import-legacy":
        con = ledger.connect(ws)
        try:
            for src in a.source:
                counts = legacy.run(con, Path(src), label=a.label if len(a.source) == 1 else None, apply=a.apply)
                kind = next(k.split(":", 1)[1] for k in counts if k.startswith("_kind:"))
                print(f"{src} ({kind} schema){'' if a.apply else '  [dry run - nothing written; add --apply]'}")
                for k, v in sorted(counts.items()):
                    if not k.startswith("_"):
                        print(f"  {k:32} {v}")
        finally:
            con.close()
    return 0


def cmd_workspace(a) -> int:
    root = Path(a.dir).resolve()
    steps = workspace.plan(root, a.name or root.name, a.kind, a.slug, a.tasks, a.retro_path)
    for action, path, _ in steps:
        print(f"  {action:6} {path.relative_to(root) if path != root else '.'}")
    if a.apply:
        workspace.apply(steps)
        print(f"workspace initialised at {root}. Next: review CLAUDE.md, then run bootstrap.ps1")
    else:
        print("dry run - add --apply to write these")
    return 0


def cmd_doctor(a) -> int:
    checks = doctor.run(a.workspace, a.plugin_root)
    print(doctor.format_checks(checks))
    return 1 if any(c.level == "FAIL" for c in checks) else 0


def cmd_backup(a) -> int:
    target, copied = backup.snapshot(_ws(a), a.dest, a.keep)
    print(f"backup written to {target}")
    for c in copied:
        print(f"  {c}")
    return 0


def cmd_usage(a) -> int:
    if a.transcript:
        p = paths.latest_transcript(_ws(a).root) if a.transcript == "auto" else Path(a.transcript)
        if not p:
            print("no transcript found", file=sys.stderr)
            return 1
        u = usage.transcript_tokens(p)
        print(f"{p}\n  input {u.input:,} (fresh {u.fresh_input:,}, cache read {u.cache_read:,}, "
              f"cache write {u.cache_write:,})\n  output {u.output:,}")
        return 0
    root = paths.transcript_dir(_ws(a).root) if a.workspace_only else Path.home() / ".claude" / "projects"
    print(usage.report(root, a.days))
    return 0


def cmd_run(a) -> int:
    try:
        return runner.run(_ws(a), a.plugin, a.script, a.args, Path(a.log) if a.log else None)
    except runner.RunError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


def cmd_headless(a) -> int:
    return headless.run(_ws(a), Path(a.prompt_file), a.allowed_tools, Path(a.log_dir) if a.log_dir else None,
                        a.before, a.delete_task)


def cmd_guard(a) -> int:
    repo = Path(a.repo).resolve()
    if a.guard_cmd == "install":
        print(f"installed {guard.install(repo)}")
        return 0
    if a.guard_cmd == "scan":
        hits = guard.scan_range(repo, a.range, guard.load_denylist())
        print("\n".join(hits) or "clean")
        return 1 if hits else 0
    return guard.pre_push(repo, sys.stdin.read())


def cmd_eval(a) -> int:
    return evals.main(_ws(a), a.eval_args)


def cmd_hook(a) -> int:
    if a.hook_cmd == "session-start":
        try:
            print(hook.session_start_text())
        except Exception as e:  # noqa: BLE001 - never fail a session start
            print(f"caveman-core: session-start hook error: {e}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cavecore", description="Caveman Core CLI")
    p.add_argument("--version", action="version", version=f"cavecore {__version__}")
    p.add_argument("--workspace", help="workspace root (default: resolved automatically; see `cavecore which`)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("which", help="Show the resolved workspace").set_defaults(run=cmd_which)

    st = sub.add_parser("setting", help="Per-workspace plugin settings (workspace.yaml `plugins:`)")
    st_sub = st.add_subparsers(dest="setting_cmd", required=True)
    x = st_sub.add_parser("get", help="Print one setting; empty if unset")
    x.add_argument("name", help="<plugin>.<key>")
    x.set_defaults(run=cmd_setting)
    x = st_sub.add_parser("list", help="List settings")
    x.add_argument("plugin", nargs="?")
    x.set_defaults(run=cmd_setting)

    work.add_parser(sub)

    lg = sub.add_parser("ledger", help="Ledger maintenance").add_subparsers(dest="ledger_cmd", required=True)
    lg.add_parser("init", help="Create the ledger and apply plugin schemas")
    lg.add_parser("info", help="Tables, row counts, imports")
    x = lg.add_parser("sql", help="Run a read-only query")
    x.add_argument("query")
    x = lg.add_parser("import-legacy", help="Import jarvis.db / cmc.db history (dry run unless --apply)")
    x.add_argument("source", nargs="+")
    x.add_argument("--label", help="legacy_ref label (default: source file stem)")
    x.add_argument("--apply", action="store_true")
    for name in ("init", "info", "sql", "import-legacy"):
        lg.choices[name].set_defaults(run=cmd_ledger)

    x = sub.add_parser("workspace", help="Workspace scaffolding").add_subparsers(dest="ws_cmd", required=True)
    y = x.add_parser("init", help="Scaffold the standard workspace layout (dry run unless --apply)")
    y.add_argument("--dir", default=".")
    y.add_argument("--name")
    y.add_argument("--slug")
    y.add_argument("--kind", choices=paths.KINDS, default="personal")
    y.add_argument("--tasks", choices=paths.TASK_MODES, default="ledger")
    y.add_argument("--retro-path", help="file retros are appended to, e.g. docs/lessons-learned.md")
    y.add_argument("--apply", action="store_true")
    y.set_defaults(run=cmd_workspace)

    x = sub.add_parser("doctor", help="Check machine and workspace setup")
    x.add_argument("--plugin-root", help="caveman-core plugin folder, to compare versions")
    x.set_defaults(run=cmd_doctor)

    x = sub.add_parser("backup", help="Snapshot data/*.db and config/")
    x.add_argument("--dest")
    x.add_argument("--keep", type=int, default=14)
    x.set_defaults(run=cmd_backup)

    x = sub.add_parser("usage", help="Token usage from Claude transcripts")
    x.add_argument("--days", type=int, default=7)
    x.add_argument("--workspace-only", action="store_true")
    x.add_argument("--transcript", help="one transcript path, or 'auto' for this workspace's newest")
    x.set_defaults(run=cmd_usage)

    x = sub.add_parser("run", help="Run a plugin's script by name, whatever version is installed")
    x.add_argument("--log", help="append output to this file (relative = under the workspace)")
    x.add_argument("plugin", help="plugin name, e.g. concierge-family")
    x.add_argument("script", help="file in the plugin's scripts/ folder, e.g. school_db.py")
    x.add_argument("args", nargs=argparse.REMAINDER, help="passed to the script")
    x.set_defaults(run=cmd_run)

    x = sub.add_parser("headless", help="Run one unattended Claude session from a prompt file (for Task Scheduler)")
    x.add_argument("--prompt-file", required=True)
    x.add_argument("--allowed-tools", help="comma-separated; default: <prompt-file>.allowedtools sidecar, else none")
    x.add_argument("--log-dir")
    x.add_argument("--before", help="command to run first, e.g. start an app an MCP server needs")
    x.add_argument("--delete-task", help="scheduled task to delete after the run (one-shot tasks)")
    x.set_defaults(run=cmd_headless)

    g = sub.add_parser("guard", help="Block personal data and secrets from public pushes")
    g.add_argument("--repo", default=".")
    gs = g.add_subparsers(dest="guard_cmd", required=True)
    gs.add_parser("install", help="install the pre-push hook in --repo").set_defaults(run=cmd_guard)
    x = gs.add_parser("scan", help="scan a revision range, e.g. origin/main..HEAD")
    x.add_argument("range")
    x.set_defaults(run=cmd_guard)
    x = gs.add_parser("pre-push", help="git pre-push hook entry point (reads refs on stdin)")
    x.add_argument("remote", nargs="?")
    x.add_argument("url", nargs="?")
    x.set_defaults(run=cmd_guard)

    x = sub.add_parser("eval", help="Subagent eval harness (cases, headless runs, grading, trends)",
                       add_help=False)
    x.add_argument("eval_args", nargs=argparse.REMAINDER, help="see `cavecore eval --help`")
    x.set_defaults(run=cmd_eval)

    x = sub.add_parser("hook", help="Hook entry points").add_subparsers(dest="hook_cmd", required=True)
    x.add_parser("session-start").set_defaults(run=cmd_hook)
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    argv = list(sys.argv[1:] if argv is None else argv)
    eval_rest = None
    # `cavecore [--workspace W] eval <anything>`: hand everything after `eval` to the
    # harness's own parser untouched (argparse REMAINDER drops a leading --help).
    i, takes_value = 0, False
    while i < len(argv):
        tok = argv[i]
        if takes_value:
            takes_value = False
        elif tok == "--workspace":
            takes_value = True
        elif tok.startswith("--workspace="):
            pass
        elif tok == "eval":
            argv, eval_rest = argv[: i + 1], argv[i + 1:]
            break
        else:
            break
        i += 1
    a = build_parser().parse_args(argv)
    if eval_rest is not None:
        a.eval_args = eval_rest
    try:
        if a.cmd == "work":
            return work.run(_ws(a), a)
        return a.run(a)
    except (paths.WorkspaceNotFound, FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
