"""``cavecore work ...``: projects, efforts, sessions, tasks and decisions.

Port of the concierge ``session-tracker`` (``jarvis_db.py``) onto the shared
ledger, plus ``session log``: a one-shot start+end used by the handoff, which
also records token counts from the session transcript.

The effort slug ``general`` routes to a rolling monthly effort
(``general-YYYY-MM``) under a ``general`` project, created on first use, for
one-off work that does not belong to a real effort.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import ledger, paths, usage
from .paths import Workspace

NOW = "strftime('%Y-%m-%dT%H:%M:%SZ','now')"
GENERIC = "general"
PROJECT_STATUSES = ("planned", "active", "paused", "done", "archived")
EFFORT_STATUSES = ("planned", "active", "blocked", "paused", "done", "abandoned")
STALE_DAYS = 30


class WorkError(RuntimeError):
    pass


def _one(con, sql, params, what):
    row = con.execute(sql, params).fetchone()
    if not row:
        raise WorkError(f"no {what}")
    return row


def find_project(con, slug):
    return _one(con, "SELECT * FROM projects WHERE slug = ?", (slug,), f"project with slug '{slug}'")


def find_effort(con, slug):
    return _one(con, "SELECT * FROM efforts WHERE slug = ?", (slug,), f"effort with slug '{slug}'")


def find_task(con, task_id):
    return _one(con, "SELECT * FROM tasks WHERE id = ?", (task_id,), f"task #{task_id}")


def ensure_generic_effort(con, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    slug = f"{GENERIC}-{now:%Y-%m}"
    with ledger.write(con):
        con.execute(
            "INSERT OR IGNORE INTO projects (slug, name, description, status) VALUES (?, 'General', ?, 'active')",
            (GENERIC, "Day-to-day and one-off requests that don't belong to a specific project."),
        )
        pid = find_project(con, GENERIC)["id"]
        con.execute(
            "INSERT OR IGNORE INTO efforts (project_id, slug, name, description, status) VALUES (?, ?, ?, ?, 'active')",
            (pid, slug, f"General - {now:%B %Y}", "Miscellaneous one-off requests this month."),
        )
    return slug


def _effort_slug(con, slug):
    return ensure_generic_effort(con) if slug == GENERIC else slug


# ------------------------------------------------------------------ project

def project_add(con, a):
    with ledger.write(con):
        con.execute("INSERT INTO projects (slug, name, description, status) VALUES (?, ?, ?, ?)",
                    (a.slug, a.name, a.description, a.status))
    print(f"created project '{a.slug}'")


def project_list(con, a):
    rows = con.execute("SELECT p.*, (SELECT COUNT(*) FROM efforts e WHERE e.project_id = p.id) AS n "
                       "FROM projects p ORDER BY p.status = 'archived', p.updated_at DESC").fetchall()
    if not rows:
        print("(no projects yet)")
    for p in rows:
        print(f"[{p['status']:8}] {p['slug']:24} {p['name']}  ({p['n']} effort(s))")


def project_show(con, a):
    p = find_project(con, a.slug)
    print(f"# {p['name']}  ({p['slug']})\nstatus: {p['status']}")
    if p["description"]:
        print(f"\n{p['description']}")
    efforts = con.execute("SELECT * FROM efforts WHERE project_id = ? ORDER BY updated_at DESC", (p["id"],)).fetchall()
    print(f"\n## Efforts ({len(efforts)})")
    for e in efforts:
        print(f"  [{e['status']:8}] {e['slug']:24} {e['name']}")


def project_update(con, a):
    p = find_project(con, a.slug)
    sets = {k: v for k, v in (("status", a.status), ("description", a.description), ("name", a.name)) if v is not None}
    if not sets:
        raise WorkError("nothing to update - pass --status, --description or --name")
    with ledger.write(con):
        con.execute(f"UPDATE projects SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", (*sets.values(), p["id"]))
    print(f"updated project '{a.slug}'")


# ------------------------------------------------------------------- effort

def effort_add(con, a):
    p = find_project(con, a.project)
    with ledger.write(con):
        con.execute("INSERT INTO efforts (project_id, slug, name, description, status) VALUES (?, ?, ?, ?, ?)",
                    (p["id"], a.slug, a.name, a.description, a.status))
    print(f"created effort '{a.slug}' under project '{p['slug']}'")


def effort_list(con, a):
    sql = ("SELECT e.*, p.slug AS project_slug FROM efforts e JOIN projects p ON p.id = e.project_id WHERE 1=1")
    params: list = []
    if a.project:
        sql += " AND p.slug = ?"
        params.append(a.project)
    if a.status:
        sql += " AND e.status = ?"
        params.append(a.status)
    rows = con.execute(sql + " ORDER BY e.updated_at DESC", params).fetchall()
    if not rows:
        print("(no efforts)")
    for e in rows:
        print(f"[{e['status']:9}] {e['slug']:28} {e['name']}  ({e['project_slug']})")


def print_effort_context(con, e) -> None:
    p = con.execute("SELECT * FROM projects WHERE id = ?", (e["project_id"],)).fetchone()
    print(f"# {e['name']}  ({e['slug']})\nproject: {p['name']} ({p['slug']})\nstatus: {e['status']}")
    if e["description"]:
        print(f"\n{e['description']}")
    print(f"\n## Current state\n{e['current_state'] or '(none recorded yet)'}")
    print(f"\n## Next steps\n{e['next_steps'] or '(none recorded yet)'}")
    last = con.execute("SELECT * FROM sessions WHERE effort_id = ? AND status = 'completed' "
                       "ORDER BY ended_at DESC LIMIT 1", (e["id"],)).fetchone()
    if last:
        print(f"\n## Last session ({last['ended_at']})\n{last['summary'] or '(no summary recorded)'}")
    n = con.execute("SELECT COUNT(*) FROM sessions WHERE effort_id = ?", (e["id"],)).fetchone()[0]
    print(f"\n({n} session(s) total - `cavecore work session list --effort {e['slug']}` for history)")
    tasks = con.execute("SELECT id, description, external_ref FROM tasks WHERE effort_id = ? AND status = 'open' "
                        "ORDER BY created_at", (e["id"],)).fetchall()
    if tasks:
        print(f"\n## Open tasks ({len(tasks)})")
        for t in tasks:
            ref = f" [{t['external_ref']}]" if t["external_ref"] else ""
            print(f"  #{t['id']:<4} {t['description']}{ref}")


def effort_show(con, a):
    print_effort_context(con, find_effort(con, _effort_slug(con, a.slug)))


def effort_update(con, a):
    e = find_effort(con, a.slug)
    sets = {k: v for k, v in (("status", a.status), ("current_state", a.current_state),
                              ("next_steps", a.next_steps), ("description", a.description)) if v is not None}
    if not sets:
        raise WorkError("nothing to update - pass --status/--current-state/--next-steps/--description")
    with ledger.write(con):
        con.execute(f"UPDATE efforts SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", (*sets.values(), e["id"]))
        if a.status in ("done", "abandoned"):
            con.execute(f"UPDATE efforts SET completed_at = {NOW} WHERE id = ? AND completed_at IS NULL", (e["id"],))
    print(f"updated effort '{a.slug}'")


# ----------------------------------------------------------------- decision

def decision_add(con, a):
    p = find_project(con, a.project)
    eid = find_effort(con, a.effort)["id"] if a.effort else None
    with ledger.write(con):
        cur = con.execute("INSERT INTO decisions (project_id, effort_id, decision, rationale) VALUES (?, ?, ?, ?)",
                          (p["id"], eid, a.decision, a.rationale))
    print(f"logged decision #{cur.lastrowid} for project '{p['slug']}'")


def decision_list(con, a):
    sql = ("SELECT d.*, p.slug AS project_slug, e.slug AS effort_slug FROM decisions d "
           "JOIN projects p ON p.id = d.project_id LEFT JOIN efforts e ON e.id = d.effort_id WHERE 1=1")
    params: list = []
    if a.project:
        sql += " AND p.slug = ?"
        params.append(a.project)
    if a.effort:
        sql += " AND e.slug = ?"
        params.append(a.effort)
    rows = con.execute(sql + " ORDER BY d.decided_at DESC", params).fetchall()
    if not rows:
        print("(no decisions logged yet)")
    for d in rows:
        scope = f"{d['project_slug']}/{d['effort_slug']}" if d["effort_slug"] else d["project_slug"]
        print(f"#{d['id']:<4} [{d['decided_at']}] ({scope}) {d['decision']}")
        if d["rationale"]:
            print(f"       why: {d['rationale']}")


# ------------------------------------------------------------------ session

def _abandon_stale(con, effort_id) -> int:
    n = con.execute(f"UPDATE sessions SET status = 'abandoned', ended_at = {NOW} "
                    "WHERE effort_id = ? AND status = 'in_progress'", (effort_id,)).rowcount
    return n


def session_start(con, a):
    e = find_effort(con, _effort_slug(con, a.effort))
    with ledger.write(con):
        n = _abandon_stale(con, e["id"])
        if e["status"] == "planned":
            con.execute("UPDATE efforts SET status = 'active' WHERE id = ?", (e["id"],))
        cur = con.execute("INSERT INTO sessions (effort_id, developer) VALUES (?, ?)", (e["id"], a.developer))
    if n:
        print(f"(marked {n} stale in-progress session(s) as abandoned)\n")
    print(f"started session #{cur.lastrowid} for effort '{e['slug']}'\n")
    print_effort_context(con, find_effort(con, e["slug"]))


def _tokens(ws: Workspace | None, transcript: str | None):
    if not transcript:
        return None, None, None
    path = paths.latest_transcript(ws.root) if transcript == "auto" and ws else Path(transcript)
    if not path or not path.is_file():
        print(f"warning: transcript not found ({transcript}); token counts not recorded", file=sys.stderr)
        return None, None, None
    u = usage.transcript_tokens(path)
    return u.input, u.output, str(path)


def _close(con, ws, a, session_id, effort) -> None:
    tin, tout, tpath = _tokens(ws, getattr(a, "transcript", None))
    con.execute(
        f"UPDATE sessions SET status = 'completed', ended_at = {NOW}, summary = ?, outcome = ?, next_steps = ?, "
        "decisions = ?, blockers = ?, git_refs = ?, input_tokens = ?, output_tokens = ?, transcript = ? WHERE id = ?",
        (a.summary, a.outcome, a.next_steps, a.decisions, a.blockers, a.git_refs, tin, tout, tpath, session_id))
    sets = {"current_state": a.current_state, "next_steps": a.next_steps}
    if a.status:
        sets["status"] = a.status
    con.execute(f"UPDATE efforts SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", (*sets.values(), effort["id"]))
    if a.status in ("done", "abandoned"):
        con.execute(f"UPDATE efforts SET completed_at = {NOW} WHERE id = ? AND completed_at IS NULL", (effort["id"],))
    if tin is not None:
        print(f"tokens: input {tin:,} / output {tout:,}")


def session_end(con, a, ws=None):
    e = find_effort(con, _effort_slug(con, a.effort))
    s = con.execute("SELECT * FROM sessions WHERE effort_id = ? AND status = 'in_progress' "
                    "ORDER BY started_at DESC LIMIT 1", (e["id"],)).fetchone()
    if not s:
        raise WorkError(f"no in-progress session for effort '{e['slug']}' - use `session log` for a one-shot entry")
    with ledger.write(con):
        _close(con, ws, a, s["id"], e)
    print(f"closed session #{s['id']} for effort '{e['slug']}'")


def session_log(con, a, ws=None):
    """One-shot: close the open session for the effort, or create and close one."""
    e = find_effort(con, _effort_slug(con, a.effort))
    with ledger.write(con):
        s = con.execute("SELECT id FROM sessions WHERE effort_id = ? AND status = 'in_progress' "
                        "ORDER BY started_at DESC LIMIT 1", (e["id"],)).fetchone()
        sid = s["id"] if s else con.execute("INSERT INTO sessions (effort_id, developer) VALUES (?, ?)",
                                            (e["id"], a.developer)).lastrowid
        if e["status"] == "planned":
            con.execute("UPDATE efforts SET status = 'active' WHERE id = ?", (e["id"],))
        _close(con, ws, a, sid, e)
    print(f"logged session #{sid} for effort '{e['slug']}'")


def session_list(con, a):
    if a.effort:
        e = find_effort(con, _effort_slug(con, a.effort))
        rows = con.execute("SELECT * FROM sessions WHERE effort_id = ? ORDER BY started_at DESC", (e["id"],)).fetchall()
    else:
        rows = con.execute("SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", (a.limit,)).fetchall()
    if not rows:
        print("(no sessions yet)")
    for s in rows:
        tok = f"  tokens {s['input_tokens']:,}/{s['output_tokens']:,}" if s["input_tokens"] is not None else ""
        print(f"#{s['id']:<4} [{s['status']:11}] started {s['started_at']}  ended {s['ended_at'] or '-'}{tok}")
        if s["summary"]:
            print(f"       {s['summary']}")


# --------------------------------------------------------------------- task

def task_add(con, a):
    e = find_effort(con, _effort_slug(con, a.effort))
    with ledger.write(con):
        cur = con.execute("INSERT INTO tasks (effort_id, description, external_ref, created_session_id) VALUES (?, ?, ?, ?)",
                          (e["id"], a.description, a.external_ref, a.session_id))
    print(f"added task #{cur.lastrowid} to effort '{e['slug']}'")


def task_list(con, a):
    sql = "SELECT t.*, e.slug AS effort_slug FROM tasks t JOIN efforts e ON e.id = t.effort_id WHERE 1=1"
    params: list = []
    if a.effort:
        sql += " AND e.slug = ?"
        params.append(a.effort)
    if a.status:
        sql += " AND t.status = ?"
        params.append(a.status)
    elif not a.all or a.stale is not None:
        sql += " AND t.status = 'open'"
    if a.stale is not None:
        sql += " AND t.created_at <= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?)"
        params.append(f"-{a.stale} days")
    rows = con.execute(sql + " ORDER BY t.created_at " + ("ASC" if a.stale is not None else "DESC"), params).fetchall()
    if not rows:
        print("(no stale tasks)" if a.stale is not None else "(no tasks)")
    for t in rows:
        ref = f" [{t['external_ref']}]" if t["external_ref"] else ""
        age = f" (open since {t['created_at'][:10]})" if a.stale is not None else ""
        print(f"#{t['id']:<4} [{t['status']:9}] ({t['effort_slug']}) {t['description']}{ref}{age}")


def _task_close(con, a, status):
    t = find_task(con, a.id)
    with ledger.write(con):
        con.execute(f"UPDATE tasks SET status = ?, completed_at = {NOW}, completed_session_id = ? WHERE id = ?",
                    (status, a.session_id, t["id"]))
    print(f"{'closed' if status == 'done' else 'cancelled'} task #{t['id']}")


def task_done(con, a):
    _task_close(con, a, "done")


def task_cancel(con, a):
    _task_close(con, a, "cancelled")


def task_show(con, a):
    t = find_task(con, a.id)
    e = con.execute("SELECT slug FROM efforts WHERE id = ?", (t["effort_id"],)).fetchone()
    print(f"# Task #{t['id']}  ({e['slug']})\nstatus: {t['status']}\ncreated: {t['created_at']}")
    if t["external_ref"]:
        print(f"ref: {t['external_ref']}")
    if t["completed_at"]:
        print(f"completed: {t['completed_at']}")
    print(f"\n{t['description']}")


# --------------------------------------------------------------------- misc

def status(con, a):
    print("## Active efforts\n")
    rows = con.execute("SELECT e.*, p.name AS project_name, "
                       "EXISTS(SELECT 1 FROM sessions s WHERE s.effort_id = e.id AND s.status = 'in_progress') AS open_s, "
                       "(SELECT COUNT(*) FROM tasks t WHERE t.effort_id = e.id AND t.status = 'open') AS open_t "
                       "FROM efforts e JOIN projects p ON p.id = e.project_id "
                       "WHERE e.status IN ('active','blocked') ORDER BY e.updated_at DESC").fetchall()
    if not rows:
        print("(none)")
    for e in rows:
        tasks = f" - {e['open_t']} open task(s)" if e["open_t"] else ""
        print(f"[{e['status']:8}] {e['project_name']} / {e['name']} ({e['slug']}){tasks}"
              f"{' [session open]' if e['open_s'] else ''}")
    stale = con.execute("SELECT COUNT(*) FROM tasks WHERE status = 'open' AND "
                        "created_at <= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?)", (f"-{STALE_DAYS} days",)).fetchone()[0]
    if stale:
        print(f"\n{stale} task(s) open for {STALE_DAYS}+ days: `cavecore work task list --stale` - close, cancel or recommit them.")


def add_parser(sub) -> None:
    w = sub.add_parser("work", help="Projects, efforts, sessions, tasks, decisions").add_subparsers(dest="work_cmd", required=True)
    w.add_parser("status", help="Active efforts at a glance").set_defaults(func=status)

    pj = w.add_parser("project").add_subparsers(dest="sub", required=True)
    x = pj.add_parser("add"); x.add_argument("slug"); x.add_argument("name"); x.add_argument("--description")
    x.add_argument("--status", choices=PROJECT_STATUSES, default="planned"); x.set_defaults(func=project_add)
    pj.add_parser("list").set_defaults(func=project_list)
    x = pj.add_parser("show"); x.add_argument("slug"); x.set_defaults(func=project_show)
    x = pj.add_parser("update"); x.add_argument("slug"); x.add_argument("--status", choices=PROJECT_STATUSES)
    x.add_argument("--description"); x.add_argument("--name"); x.set_defaults(func=project_update)

    ef = w.add_parser("effort").add_subparsers(dest="sub", required=True)
    x = ef.add_parser("add"); x.add_argument("project"); x.add_argument("slug"); x.add_argument("name")
    x.add_argument("--description"); x.add_argument("--status", choices=EFFORT_STATUSES, default="planned")
    x.set_defaults(func=effort_add)
    x = ef.add_parser("list"); x.add_argument("--project"); x.add_argument("--status", choices=EFFORT_STATUSES)
    x.set_defaults(func=effort_list)
    x = ef.add_parser("show"); x.add_argument("slug", help="effort slug, or 'general'"); x.set_defaults(func=effort_show)
    x = ef.add_parser("update"); x.add_argument("slug"); x.add_argument("--status", choices=EFFORT_STATUSES)
    x.add_argument("--current-state"); x.add_argument("--next-steps"); x.add_argument("--description")
    x.set_defaults(func=effort_update)

    dc = w.add_parser("decision").add_subparsers(dest="sub", required=True)
    x = dc.add_parser("add"); x.add_argument("project"); x.add_argument("--effort")
    x.add_argument("--decision", required=True); x.add_argument("--rationale"); x.set_defaults(func=decision_add)
    x = dc.add_parser("list"); x.add_argument("--project"); x.add_argument("--effort"); x.set_defaults(func=decision_list)

    tk = w.add_parser("task").add_subparsers(dest="sub", required=True)
    x = tk.add_parser("add"); x.add_argument("effort", help="effort slug, or 'general'"); x.add_argument("description")
    x.add_argument("--external-ref", help="e.g. ado:850 or gh:owner/repo#12"); x.add_argument("--session-id", type=int)
    x.set_defaults(func=task_add)
    x = tk.add_parser("list"); x.add_argument("--effort"); x.add_argument("--status", choices=("open", "done", "cancelled"))
    x.add_argument("--all", action="store_true")
    x.add_argument("--stale", type=int, nargs="?", const=STALE_DAYS, metavar="DAYS",
                   help=f"only open tasks created at least DAYS ago (default {STALE_DAYS})")
    x.set_defaults(func=task_list)
    for name, fn in (("done", task_done), ("cancel", task_cancel)):
        x = tk.add_parser(name); x.add_argument("id", type=int); x.add_argument("--session-id", type=int); x.set_defaults(func=fn)
    x = tk.add_parser("show"); x.add_argument("id", type=int); x.set_defaults(func=task_show)

    se = w.add_parser("session").add_subparsers(dest="sub", required=True)
    x = se.add_parser("start"); x.add_argument("effort", help="effort slug, or 'general'"); x.add_argument("--developer")
    x.set_defaults(func=session_start)
    for name, fn, help_ in (("end", session_end, "close the open session for an effort"),
                            ("log", session_log, "one-shot: record a whole session (used by the handoff)")):
        x = se.add_parser(name, help=help_)
        x.add_argument("effort", help="effort slug, or 'general'")
        x.add_argument("--summary", required=True, help="what got done this session")
        x.add_argument("--current-state", required=True, help="living snapshot of where the effort stands now")
        x.add_argument("--next-steps", required=True, help="what to pick up next time")
        x.add_argument("--outcome", help="one-line state of the work at handoff (committed? pushed? verified?)")
        x.add_argument("--decisions"); x.add_argument("--blockers"); x.add_argument("--git-refs")
        x.add_argument("--status", choices=EFFORT_STATUSES, help="new effort status, if it changed")
        x.add_argument("--transcript", help="path to the session .jsonl, or 'auto' for this workspace's newest")
        x.add_argument("--developer")
        x.set_defaults(func=fn)
    x = se.add_parser("list"); x.add_argument("--effort"); x.add_argument("--limit", type=int, default=20)
    x.set_defaults(func=session_list)


def run(ws: Workspace, a: argparse.Namespace) -> int:
    con = ledger.connect(ws)
    try:
        if a.func in (session_end, session_log):
            a.func(con, a, ws)
        else:
            a.func(con, a)
        return 0
    except (WorkError, sqlite3.IntegrityError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        con.close()
