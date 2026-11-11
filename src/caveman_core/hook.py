"""SessionStart context: who am I, where did we leave off, and the rules every
enabled plugin wants in force.

Plugins cannot ship a CLAUDE.md, so each one puts its always-on rules in
``rules/session.md``; this hook prints them (capped) at session start. The
hook must never fail a session: every error degrades to a one-line note.
"""

from __future__ import annotations

from . import __version__, ledger, paths, plugins

MAX_TOTAL = 8000          # Claude Code caps hook context at 10,000 characters
MAX_RULE_LINES = 25
MAX_FIELD = 600


def _clip(text: str | None, n: int = MAX_FIELD) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _ledger_lines(ws: paths.Workspace) -> list[str]:
    if not ws.ledger_path.exists():
        return ["Ledger: none yet (created on first `cavecore work ...` write)."]
    try:
        con = ledger.connect(ws, readonly=True)
    except Exception as e:  # noqa: BLE001 - a hook must not crash the session
        return [f"Ledger: unreadable ({e})."]
    try:
        active = con.execute("SELECT COUNT(*) FROM efforts WHERE status IN ('active','blocked')").fetchone()[0]
        last = con.execute(
            "SELECT s.ended_at, s.summary, s.outcome, e.slug, e.next_steps FROM sessions s "
            "JOIN efforts e ON e.id = s.effort_id WHERE s.status = 'completed' "
            "ORDER BY s.ended_at DESC LIMIT 1").fetchone()
        out = [f"Ledger: {active} active effort(s). `cavecore work status` lists them."]
        if last:
            out.append(f"Last session ({last['ended_at']}, effort `{last['slug']}`): {_clip(last['summary'], 400)}")
            if last["outcome"]:
                out.append(f"Outcome: {_clip(last['outcome'], 200)}")
            if last["next_steps"]:
                out.append(f"Next steps: {_clip(last['next_steps'])}")
        return out
    finally:
        con.close()


def _rules(ws: paths.Workspace) -> list[str]:
    out: list[str] = []
    for p in plugins.enabled(ws.root):
        f = p.file("rules", "session.md")
        if not f.is_file():
            continue
        lines = [ln.rstrip() for ln in f.read_text(encoding="utf-8").splitlines()]
        lines = [ln for ln in lines if ln.strip()][:MAX_RULE_LINES]
        out += [f"### {p.name}", *lines, ""]
    return out


def session_start_text() -> str:
    try:
        ws = paths.resolve()
    except paths.WorkspaceNotFound:
        return ("caveman-core: no workspace.yaml in this folder or its parents, so no ledger or workspace rules "
                "are loaded. Run /caveman-core:workspace-init to set this folder up as a workspace.")
    except Exception as e:  # noqa: BLE001
        return f"caveman-core: workspace config error: {e}"

    parts = [f"## Workspace: {ws.name} ({ws.kind}) - caveman-core {__version__}", f"Root: {ws.root}",
             f"Tasks tracked in: {ws.task_mode}. Handoff file: {ws.handoff_path.relative_to(ws.root)}."]
    try:
        parts += _ledger_lines(ws)
    except Exception as e:  # noqa: BLE001
        parts.append(f"Ledger: error ({e}).")
    try:
        rules = _rules(ws)
    except Exception as e:  # noqa: BLE001
        rules = [f"(could not read plugin rules: {e})"]
    if rules:
        parts += ["", "## Rules from enabled plugins", *rules]
    text = "\n".join(parts).strip()
    if len(text) > MAX_TOTAL:
        text = text[: MAX_TOTAL - 60].rstrip() + "\n…(truncated - see each plugin's rules/session.md)"
    return text
