"""import-legacy against synthetic databases that use the two real legacy schemas."""

import sqlite3

from caveman_core import ledger, paths

CONCIERGE_SCHEMA = """
CREATE TABLE projects (id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  description TEXT, status TEXT NOT NULL DEFAULT 'planned', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE efforts (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, slug TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL, description TEXT, status TEXT NOT NULL DEFAULT 'planned', current_state TEXT, next_steps TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, effort_id INTEGER NOT NULL, status TEXT NOT NULL,
  started_at TEXT NOT NULL, ended_at TEXT, summary TEXT, decisions TEXT, blockers TEXT, git_refs TEXT);
CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, effort_id INTEGER NOT NULL, description TEXT NOT NULL,
  status TEXT NOT NULL, created_session_id INTEGER, completed_session_id INTEGER, created_at TEXT NOT NULL, completed_at TEXT);
CREATE TABLE decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, effort_id INTEGER,
  decision TEXT NOT NULL, rationale TEXT, decided_at TEXT NOT NULL);
INSERT INTO projects VALUES (1,'general','General',NULL,'active','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z');
INSERT INTO projects VALUES (2,'garden','Garden','veg beds','active','2026-01-02T00:00:00Z','2026-01-03T00:00:00Z');
INSERT INTO efforts VALUES (1,2,'raised-beds','Raised beds',NULL,'done','built','none','2026-01-02T00:00:00Z','2026-01-05T00:00:00Z');
INSERT INTO efforts VALUES (2,1,'general-2026-01','General - January 2026',NULL,'active',NULL,NULL,'2026-01-01T00:00:00Z','2026-01-01T00:00:00Z');
INSERT INTO sessions VALUES (1,1,'completed','2026-01-04T10:00:00Z','2026-01-04T11:00:00Z','built beds',NULL,NULL,NULL);
INSERT INTO sessions VALUES (2,2,'abandoned','2026-01-06T10:00:00Z','2026-01-06T10:05:00Z',NULL,NULL,NULL,NULL);
INSERT INTO tasks VALUES (1,1,'buy soil','done',1,1,'2026-01-04T10:10:00Z','2026-01-04T10:50:00Z');
INSERT INTO tasks VALUES (2,2,'call plumber','open',NULL,NULL,'2026-01-06T10:01:00Z',NULL);
INSERT INTO decisions VALUES (1,2,1,'cedar not pine','lasts longer','2026-01-04T10:30:00Z');
"""

CMC_SCHEMA = """
CREATE TABLE projects (name TEXT PRIMARY KEY, display_name TEXT NOT NULL, description TEXT, status TEXT NOT NULL,
  last_updated TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE sessions_old (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
  project TEXT NOT NULL, developer TEXT NOT NULL, completed_work TEXT NOT NULL, next_steps TEXT NOT NULL, status TEXT NOT NULL);
CREATE TABLE efforts (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
  description TEXT, status TEXT NOT NULL DEFAULT 'active', created_at TEXT DEFAULT CURRENT_TIMESTAMP, completed_at TEXT);
CREATE TABLE sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
  effort_id INTEGER, project TEXT, developer TEXT NOT NULL, completed_work TEXT NOT NULL, next_steps TEXT NOT NULL,
  status TEXT NOT NULL, input_tokens INTEGER, output_tokens INTEGER);
INSERT INTO projects VALUES ('widgets','Widgets',NULL,'Active','2026-02-01 09:00:00');
INSERT INTO projects VALUES ('gadgets','Gadgets','g','Planning',NULL);
INSERT INTO projects VALUES ('platform','Platform',NULL,'some long free-text status',NULL);
INSERT INTO efforts VALUES (1,'widgets-v2','Widgets v2',NULL,'active','2026-02-01 09:00:00',NULL);
INSERT INTO efforts VALUES (2,'tidy-up','Tidy up',NULL,'completed','2026-02-02 09:00:00','2026-02-03 09:00:00');
INSERT INTO sessions VALUES (1,'2026-02-01 10:00:00',1,'widgets','agent','built a','do b','committed',1000,50);
INSERT INTO sessions VALUES (2,'2026-02-02 10:00:00',1,'widgets','agent','built b','do c','pushed',2000,60);
INSERT INTO sessions VALUES (3,'2026-02-02 11:00:00',2,NULL,'agent','tidied','nothing','done',NULL,NULL);
INSERT INTO efforts VALUES (3,'orphan-work','Orphan work',NULL,'active','2026-02-04 09:00:00',NULL);
INSERT INTO sessions VALUES (4,'2026-02-04 10:00:00',3,'ghost-app','agent','ghost','g','ok',NULL,NULL);
INSERT INTO sessions_old VALUES (1,'2026-01-15 08:00:00','widgets','agent','early work','more','Active');
"""


def make_db(path, script):
    con = sqlite3.connect(path)
    con.executescript(script)
    con.commit()
    con.close()
    return path


def test_dry_run_writes_nothing(run, ws, tmp_path):
    src = make_db(tmp_path / "jarvis.db", CONCIERGE_SCHEMA)
    code, out, err = run("ledger", "import-legacy", src)
    assert code == 0, err
    assert "dry run" in out and "sessions" in out
    con = ledger.connect(paths.resolve(explicit=ws), readonly=True)
    assert con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_concierge_import_counts_and_links(run, ws, tmp_path):
    src = make_db(tmp_path / "jarvis.db", CONCIERGE_SCHEMA)
    assert run("ledger", "import-legacy", src, "--apply")[0] == 0
    con = ledger.connect(paths.resolve(explicit=ws), readonly=True)
    counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("projects", "efforts", "sessions", "tasks", "decisions")}
    assert counts == {"projects": 2, "efforts": 2, "sessions": 2, "tasks": 2, "decisions": 1}
    t = con.execute("SELECT t.*, e.slug FROM tasks t JOIN efforts e ON e.id=t.effort_id WHERE t.description='buy soil'").fetchone()
    assert t["slug"] == "raised-beds" and t["created_session_id"] is not None
    e = con.execute("SELECT * FROM efforts WHERE slug='raised-beds'").fetchone()
    assert e["completed_at"] == "2026-01-05T00:00:00Z" and e["current_state"] == "built"
    # idempotent
    code, out, _ = run("ledger", "import-legacy", src, "--apply")
    assert "already imported" in out
    assert con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 2


def test_cmc_import_maps_shapes(run, ws, tmp_path):
    src = make_db(tmp_path / "cmc.db", CMC_SCHEMA)
    code, out, err = run("ledger", "import-legacy", src, "--apply")
    assert code == 0, err
    con = ledger.connect(paths.resolve(explicit=ws), readonly=True)
    status = dict(con.execute("SELECT slug, status FROM projects").fetchall())
    assert status["widgets"] == "active" and status["gadgets"] == "planned" and status["platform"] == "active"
    e = con.execute("SELECT e.*, p.slug AS p FROM efforts e JOIN projects p ON p.id=e.project_id "
                    "WHERE e.slug='widgets-v2'").fetchone()
    assert e["p"] == "widgets"                      # inferred from its sessions
    assert e["next_steps"] == "do c" and e["current_state"] == "pushed"
    tidy = con.execute("SELECT e.status, p.slug AS p FROM efforts e JOIN projects p ON p.id=e.project_id "
                       "WHERE e.slug='tidy-up'").fetchone()
    assert (tidy["status"], tidy["p"]) == ("done", "general")   # no project on its sessions
    s = con.execute("SELECT * FROM sessions WHERE summary='built b'").fetchone()
    assert (s["input_tokens"], s["outcome"], s["ended_at"]) == (2000, "pushed", "2026-02-02T10:00:00Z")
    old = con.execute("SELECT summary FROM sessions WHERE legacy_ref='cmc:sessions_old:1'").fetchone()
    assert old["summary"] == "[widgets] early work"
    assert con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 5
    ghost = con.execute("SELECT p.slug, p.name FROM efforts e JOIN projects p ON p.id=e.project_id "
                        "WHERE e.slug='orphan-work'").fetchone()
    assert tuple(ghost) == ("ghost-app", "Ghost App")   # project missing from legacy projects table


def test_both_sources_share_general_project(run, ws, tmp_path):
    a = make_db(tmp_path / "jarvis.db", CONCIERGE_SCHEMA)
    b = make_db(tmp_path / "cmc.db", CMC_SCHEMA)
    assert run("ledger", "import-legacy", a, b, "--apply")[0] == 0
    con = ledger.connect(paths.resolve(explicit=ws), readonly=True)
    assert con.execute("SELECT COUNT(*) FROM projects WHERE slug LIKE 'general%'").fetchone()[0] == 1


EVAL_SCHEMA = """
CREATE TABLE eval_test_cases (id INTEGER PRIMARY KEY AUTOINCREMENT, agent TEXT NOT NULL, case_id TEXT NOT NULL,
  prompt TEXT NOT NULL, assertion_type TEXT NOT NULL, assertion_spec TEXT NOT NULL, notes TEXT,
  created_at TEXT NOT NULL, UNIQUE(agent, case_id));
CREATE TABLE eval_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, test_case_id INTEGER NOT NULL, run_at TEXT NOT NULL,
  trigger TEXT NOT NULL, pass_fail TEXT NOT NULL, judge_score REAL, duration_s REAL, tool_call_count INTEGER,
  tokens_in INTEGER, tokens_out INTEGER, trace_path TEXT, notes TEXT);
INSERT INTO eval_test_cases VALUES (1,'git','refuses-secret','commit this','tool_trace','{}',NULL,'2026-01-01T00:00:00Z');
INSERT INTO eval_runs VALUES (1,1,'2026-01-02T00:00:00Z','manual','pass',NULL,12.5,3,100,20,'t.jsonl',NULL);
INSERT INTO eval_runs VALUES (2,1,'2026-01-03T00:00:00Z','git_commit','fail',NULL,9.0,2,90,10,'u.jsonl',NULL);
"""


def test_import_carries_eval_tables(run, ws, tmp_path):
    src = make_db(tmp_path / "jarvis.db", CONCIERGE_SCHEMA + EVAL_SCHEMA)
    assert run("ledger", "import-legacy", src, "--apply")[0] == 0
    code, out, _ = run("ledger", "import-legacy", src, "--apply")
    assert "eval_runs (already imported)" in out
    con = ledger.connect(paths.resolve(explicit=ws), readonly=True)
    assert con.execute("SELECT COUNT(*) FROM eval_test_cases").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM eval_runs").fetchone()[0] == 2
    code, out, _ = run("eval", "run", "list")
    assert code == 0 and "refuses-secret" in out
