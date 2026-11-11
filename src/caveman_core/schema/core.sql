-- caveman-core ledger schema, version 1.
-- Project -> Effort -> (Session | Task), plus Decisions.
--   Project  = a long-running initiative (Epic-equivalent)
--   Effort   = a scoped chunk of work within a project (Feature-equivalent);
--              current_state / next_steps are living fields overwritten at
--              every session end - the fast path for "pick up where we left off"
--   Session  = one Claude Code conversation working an effort (append-only)
--   Task     = a next-action within an effort; can outlive one session
-- Times are ISO 8601 UTC with an explicit Z.
-- legacy_ref records where an imported row came from (e.g. "jarvis.db:sessions:12")
-- so `cavecore ledger import-legacy` is idempotent.

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'planned'
        CHECK (status IN ('planned','active','paused','done','archived')),
    legacy_ref TEXT UNIQUE,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS efforts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'planned'
        CHECK (status IN ('planned','active','blocked','paused','done','abandoned')),
    current_state TEXT,
    next_steps TEXT,
    legacy_ref TEXT UNIQUE,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS IX_efforts_project_id ON efforts(project_id);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    effort_id INTEGER NOT NULL REFERENCES efforts(id),
    status TEXT NOT NULL DEFAULT 'in_progress'
        CHECK (status IN ('in_progress','completed','abandoned')),
    developer TEXT,
    started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    ended_at TEXT,
    summary TEXT,
    outcome TEXT,
    next_steps TEXT,
    decisions TEXT,
    blockers TEXT,
    git_refs TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    transcript TEXT,
    legacy_ref TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS IX_sessions_effort_id ON sessions(effort_id);
CREATE INDEX IF NOT EXISTS IX_sessions_ended_at ON sessions(ended_at);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    effort_id INTEGER NOT NULL REFERENCES efforts(id),
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open','done','cancelled')),
    external_ref TEXT,
    created_session_id INTEGER REFERENCES sessions(id),
    completed_session_id INTEGER REFERENCES sessions(id),
    legacy_ref TEXT UNIQUE,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS IX_tasks_effort_id ON tasks(effort_id);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    effort_id INTEGER REFERENCES efforts(id),
    decision TEXT NOT NULL,
    rationale TEXT,
    legacy_ref TEXT UNIQUE,
    decided_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS IX_decisions_project_id ON decisions(project_id);

-- updated_at is maintained by triggers so no UPDATE can forget it.
CREATE TRIGGER IF NOT EXISTS trg_projects_updated_at
AFTER UPDATE OF slug, name, description, status ON projects
BEGIN
    UPDATE projects SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_efforts_updated_at
AFTER UPDATE OF slug, name, description, status, current_state, next_steps, project_id ON efforts
BEGIN
    UPDATE efforts SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = NEW.id;
END;

INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', '1');
