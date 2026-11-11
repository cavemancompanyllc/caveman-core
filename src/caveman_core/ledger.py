"""The per-workspace SQLite ledger at ``<workspace>/data/ledger.db``.

- ``connect()`` sets WAL, a 30 s busy timeout and foreign keys, in autocommit
  mode, and makes sure the schema exists.
- ``write(con)`` wraps a ``BEGIN IMMEDIATE`` transaction. Never make network
  calls inside it: other sessions wait on the lock while you hold it.
- Core tables come from ``caveman_core/schema/core.sql``; every enabled plugin
  may add tables in its own ``schema/*.sql`` (``CREATE TABLE IF NOT EXISTS``),
  applied after core.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Iterator

from . import plugins as plugin_mod
from .paths import Workspace

SCHEMA_VERSION = 1


def core_schema() -> str:
    return resources.files("caveman_core").joinpath("schema", "core.sql").read_text(encoding="utf-8")


def plugin_schemas(ws: Workspace) -> list[tuple[str, Path]]:
    out = []
    for p in plugin_mod.enabled(ws.root):
        if p.name == "caveman-core":
            continue
        d = p.file("schema")
        if d.is_dir():
            out += [(p.name, f) for f in sorted(d.glob("*.sql"))]
    return out


def _open(path: Path, readonly: bool) -> sqlite3.Connection:
    if readonly:
        if not path.exists() or path.stat().st_size == 0:
            raise FileNotFoundError(f"no ledger at {path} - run `cavecore ledger init`")
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30, isolation_level=None)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path, timeout=30, isolation_level=None)
        con.execute("PRAGMA journal_mode=WAL")
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def connect(ws: Workspace | Path, readonly: bool = False, sync: bool = False) -> sqlite3.Connection:
    """Open the ledger. Core schema is applied on every writable open (it is
    idempotent); plugin schemas only when ``sync`` is true."""
    path = ws.ledger_path if isinstance(ws, Workspace) else Path(ws)
    con = _open(path, readonly)
    if not readonly:
        con.executescript(core_schema())
        if sync and isinstance(ws, Workspace):
            for _name, f in plugin_schemas(ws):
                con.executescript(f.read_text(encoding="utf-8"))
    return con


@contextmanager
def write(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
    except BaseException:
        con.execute("ROLLBACK")
        raise
    else:
        con.execute("COMMIT")


def schema_version(con: sqlite3.Connection) -> int:
    row = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return int(row[0]) if row else 0


def query_readonly(ws: Workspace, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    con = connect(ws, readonly=True)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()
