import json
import sqlite3

from caveman_core import ledger, paths


def test_project_effort_session_task_flow(run, ws):
    assert run("work", "project", "add", "widgets", "Widget Line", "--status", "active")[0] == 0
    assert run("work", "effort", "add", "widgets", "widgets-v2", "Widgets v2")[0] == 0
    code, out, _ = run("work", "session", "start", "widgets-v2")
    assert code == 0 and "started session #1" in out
    assert run("work", "task", "add", "widgets-v2", "Ship it", "--external-ref", "ado:42")[0] == 0
    code, out, _ = run("work", "effort", "show", "widgets-v2")
    assert "Ship it [ado:42]" in out
    code, out, _ = run("work", "session", "end", "widgets-v2", "--summary", "did things",
                       "--current-state", "half done", "--next-steps", "finish")
    assert code == 0
    code, out, _ = run("work", "task", "done", "1")
    assert "closed task #1" in out
    con = ledger.connect(paths.resolve(explicit=ws), readonly=True)
    e = con.execute("SELECT * FROM efforts WHERE slug='widgets-v2'").fetchone()
    assert (e["status"], e["current_state"], e["next_steps"]) == ("active", "half done", "finish")
    assert con.execute("SELECT status FROM sessions WHERE id=1").fetchone()[0] == "completed"


def test_session_log_one_shot_with_transcript_tokens(run, ws, tmp_path):
    run("work", "project", "add", "p", "P")
    run("work", "effort", "add", "p", "e1", "E1")
    t = tmp_path / "t.jsonl"
    msg = {"type": "assistant", "message": {"id": "m1", "model": "x",
           "usage": {"input_tokens": 10, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 5, "output_tokens": 7}}}
    t.write_text("\n".join(json.dumps(m) for m in (msg, msg)) + "\n")   # same message twice: counted once
    code, out, err = run("work", "session", "log", "e1", "--summary", "s", "--current-state", "c",
                         "--next-steps", "n", "--outcome", "pushed", "--status", "done", "--transcript", t)
    assert code == 0, err
    assert "input 115 / output 7" in out
    con = ledger.connect(paths.resolve(explicit=ws), readonly=True)
    s = con.execute("SELECT * FROM sessions").fetchone()
    assert (s["input_tokens"], s["output_tokens"], s["outcome"]) == (115, 7, "pushed")
    assert con.execute("SELECT completed_at FROM efforts WHERE slug='e1'").fetchone()[0]


def test_general_bucket_auto_created(run, ws):
    code, out, _ = run("work", "task", "add", "general", "buy milk")
    assert code == 0
    code, out, _ = run("work", "task", "list")
    assert "(general-" in out and "buy milk" in out


def test_errors_are_clean(run):
    code, _, err = run("work", "effort", "show", "nope")
    assert code == 1 and "no effort with slug 'nope'" in err
    run("work", "project", "add", "dup", "Dup")
    code, _, err = run("work", "project", "add", "dup", "Dup")
    assert code == 1 and "UNIQUE" in err


def test_ledger_sql_is_readonly(run):
    run("work", "project", "add", "p", "P")
    code, out, _ = run("ledger", "sql", "SELECT slug FROM projects")
    assert code == 0 and "p" in out.split()
    try:
        code, _, err = run("ledger", "sql", "DELETE FROM projects")
    except sqlite3.OperationalError:
        code = 1
    assert code != 0


def test_write_rolls_back(ws):
    w = paths.resolve(explicit=ws)
    con = ledger.connect(w)
    try:
        with ledger.write(con):
            con.execute("INSERT INTO projects (slug, name) VALUES ('x', 'X')")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert con.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0


def test_status_open_counts_and_stale_tasks(run, ws):
    from caveman_core import ledger, paths
    run("work", "project", "add", "home", "Home")
    run("work", "effort", "add", "home", "garden", "Garden")
    run("work", "effort", "update", "garden", "--status", "active")
    run("work", "task", "add", "garden", "old task")
    run("work", "task", "add", "garden", "new task")
    con = ledger.connect(paths.resolve(ws))
    con.execute("UPDATE tasks SET created_at = '2020-01-01T00:00:00Z' WHERE description = 'old task'")
    con.close()

    code, out, _ = run("work", "status")
    assert code == 0 and "2 open task(s)" in out and "1 task(s) open for 30+ days" in out

    code, out, _ = run("work", "task", "list", "--stale")
    assert "old task" in out and "new task" not in out and "open since 2020-01-01" in out
    code, out, _ = run("work", "task", "list", "--stale", "100000")
    assert "(no stale tasks)" in out
