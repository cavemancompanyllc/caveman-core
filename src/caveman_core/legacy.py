"""``cavecore ledger import-legacy``: bring old session databases into the ledger.

Two legacy shapes are understood:

- **concierge** (``jarvis.db`` from concierge-core's session-tracker):
  projects / efforts / sessions / tasks / decisions with slugs - maps almost 1:1.
- **cmc** (``.data/cmc.db`` from the CMC monorepo ``tools/session_db.py``):
  ``projects(name PK)``, ``efforts(name slug)`` with no project link,
  ``sessions`` with free-text ``status`` and token counts, plus a pre-effort
  ``sessions_old`` table.

Every imported row gets ``legacy_ref = "<label>:<table>:<id>"``, so running the
import twice inserts nothing new. Source files are opened read-only. Without
``--apply`` the import runs inside a transaction that is rolled back, so the
printed counts are exactly what ``--apply`` would write.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from pathlib import Path

from . import ledger

CMC_PROJECT_STATUS = {"active": "active", "planned": "planned", "planning": "planned"}


def detect(con: sqlite3.Connection) -> str:
    cols = {r[1] for r in con.execute("PRAGMA table_info(projects)")}
    if "slug" in cols:
        return "concierge"
    if "display_name" in cols:
        return "cmc"
    raise ValueError("unrecognised legacy schema (no projects.slug or projects.display_name)")


def _ts(value: str | None) -> str | None:
    """'2026-07-03 16:39:26' (sqlite CURRENT_TIMESTAMP, UTC) -> '2026-07-03T16:39:26Z'."""
    if not value:
        return value
    v = value.strip()
    if "T" not in v:
        v = v.replace(" ", "T", 1)
    return v if v.endswith("Z") else v + "Z"


class Importer:
    def __init__(self, dst: sqlite3.Connection, label: str):
        self.dst = dst
        self.label = label
        self.counts: Counter = Counter()

    def ref(self, table: str, rid) -> str:
        return f"{self.label}:{table}:{rid}"

    def insert(self, table: str, row: dict) -> int | None:
        """Insert unless legacy_ref already exists; return the new or existing id."""
        existing = self.dst.execute(f"SELECT id FROM {table} WHERE legacy_ref = ?", (row["legacy_ref"],)).fetchone()
        if existing:
            self.counts[f"{table} (already imported)"] += 1
            return existing[0]
        # None means "not recorded": let column defaults (timestamps) or NULL apply
        row = {k: v for k, v in row.items() if v is not None}
        cols = ", ".join(row)
        cur = self.dst.execute(f"INSERT INTO {table} ({cols}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
        self.counts[table] += 1
        return cur.lastrowid

    def project_id(self, slug: str, name: str, **extra) -> int:
        row = self.dst.execute("SELECT id FROM projects WHERE slug = ?", (slug,)).fetchone()
        if row:
            return row[0]
        return self.insert("projects", {"slug": slug, "name": name, "legacy_ref": self.ref("projects", slug), **extra})

    def unique_slug(self, table: str, slug: str, legacy_ref: str) -> str:
        """Keep the slug unless another row (not this import) already owns it."""
        row = self.dst.execute(f"SELECT legacy_ref FROM {table} WHERE slug = ?", (slug,)).fetchone()
        if not row or row[0] == legacy_ref:
            return slug
        return f"{slug}-{self.label}"


def import_concierge(src: sqlite3.Connection, imp: Importer) -> None:
    pmap, emap, smap = {}, {}, {}
    for p in src.execute("SELECT * FROM projects ORDER BY id"):
        ref = imp.ref("projects", p["id"])
        pmap[p["id"]] = imp.insert("projects", {
            "slug": imp.unique_slug("projects", p["slug"], ref), "name": p["name"], "description": p["description"],
            "status": p["status"], "legacy_ref": ref, "created_at": p["created_at"], "updated_at": p["updated_at"]})
    for e in src.execute("SELECT * FROM efforts ORDER BY id"):
        ref = imp.ref("efforts", e["id"])
        done = e["updated_at"] if e["status"] in ("done", "abandoned") else None
        emap[e["id"]] = imp.insert("efforts", {
            "project_id": pmap[e["project_id"]], "slug": imp.unique_slug("efforts", e["slug"], ref), "name": e["name"],
            "description": e["description"], "status": e["status"], "current_state": e["current_state"],
            "next_steps": e["next_steps"], "legacy_ref": ref, "created_at": e["created_at"],
            "updated_at": e["updated_at"], "completed_at": done})
    for s in src.execute("SELECT * FROM sessions ORDER BY id"):
        smap[s["id"]] = imp.insert("sessions", {
            "effort_id": emap[s["effort_id"]], "status": s["status"], "started_at": s["started_at"],
            "ended_at": s["ended_at"], "summary": s["summary"], "decisions": s["decisions"],
            "blockers": s["blockers"], "git_refs": s["git_refs"], "legacy_ref": imp.ref("sessions", s["id"])})
    for t in src.execute("SELECT * FROM tasks ORDER BY id"):
        imp.insert("tasks", {
            "effort_id": emap[t["effort_id"]], "description": t["description"], "status": t["status"],
            "created_session_id": smap.get(t["created_session_id"]),
            "completed_session_id": smap.get(t["completed_session_id"]),
            "legacy_ref": imp.ref("tasks", t["id"]), "created_at": t["created_at"], "completed_at": t["completed_at"]})
    for d in src.execute("SELECT * FROM decisions ORDER BY id"):
        imp.insert("decisions", {
            "project_id": pmap[d["project_id"]], "effort_id": emap.get(d["effort_id"]), "decision": d["decision"],
            "rationale": d["rationale"], "legacy_ref": imp.ref("decisions", d["id"]), "decided_at": d["decided_at"]})


def import_cmc(src: sqlite3.Connection, imp: Importer) -> None:
    pmap = {}
    for p in src.execute("SELECT * FROM projects"):
        status = CMC_PROJECT_STATUS.get((p["status"] or "").strip().lower(), "active")
        ref = imp.ref("projects", p["name"])
        pmap[p["name"]] = imp.insert("projects", {
            "slug": imp.unique_slug("projects", p["name"], ref), "name": p["display_name"],
            "description": p["description"], "status": status, "legacy_ref": ref,
            "created_at": _ts(p["last_updated"]), "updated_at": _ts(p["last_updated"])})

    def project_for(name: str) -> int:
        # the legacy schema never enforced its foreign keys: sessions can name a
        # project that has no projects row, so create it rather than drop history
        if name not in pmap:
            pmap[name] = imp.project_id(name, name.replace("-", " ").title(), status="active")
        return pmap[name]

    sessions = src.execute("SELECT * FROM sessions ORDER BY id").fetchall()
    # cmc efforts have no project: use the project most of their sessions named
    by_effort: dict[int, Counter] = {}
    for s in sessions:
        if s["project"]:
            by_effort.setdefault(s["effort_id"], Counter())[s["project"]] += 1
    general = None
    emap = {}
    for e in src.execute("SELECT * FROM efforts ORDER BY id"):
        common = by_effort.get(e["id"])
        if common:
            pid = project_for(common.most_common(1)[0][0])
        else:
            general = general or imp.project_id("general", "General", status="active",
                                                description="Work that doesn't belong to a specific project.")
            pid = general
        status = {"completed": "done"}.get(e["status"], e["status"])
        if status not in ("planned", "active", "blocked", "paused", "done", "abandoned"):
            status = "active"
        ref = imp.ref("efforts", e["id"])
        emap[e["id"]] = imp.insert("efforts", {
            "project_id": pid, "slug": imp.unique_slug("efforts", e["name"], ref), "name": e["title"],
            "description": e["description"], "status": status, "legacy_ref": ref,
            "created_at": _ts(e["created_at"]), "updated_at": _ts(e["created_at"]),
            "completed_at": _ts(e["completed_at"])})

    latest: dict[int, sqlite3.Row] = {}
    for s in sessions:
        eid = emap[s["effort_id"]]
        imp.insert("sessions", {
            "effort_id": eid, "status": "completed", "developer": s["developer"],
            "started_at": _ts(s["timestamp"]), "ended_at": _ts(s["timestamp"]), "summary": s["completed_work"],
            "outcome": s["status"], "next_steps": s["next_steps"], "input_tokens": s["input_tokens"],
            "output_tokens": s["output_tokens"], "legacy_ref": imp.ref("sessions", s["id"])})
        latest[eid] = s
    # living fields: the newest session per effort, only if not already set
    for eid, s in latest.items():
        imp.dst.execute("UPDATE efforts SET current_state = COALESCE(current_state, ?), "
                        "next_steps = COALESCE(next_steps, ?) WHERE id = ?", (s["status"], s["next_steps"], eid))

    has_old = src.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sessions_old'").fetchone()
    if has_old:
        old = src.execute("SELECT * FROM sessions_old ORDER BY id").fetchall()
        if old:
            general = general or imp.project_id("general", "General", status="active",
                                                description="Work that doesn't belong to a specific project.")
            hist = imp.insert("efforts", {
                "project_id": general, "slug": imp.unique_slug("efforts", "legacy-sessions", imp.ref("efforts", "old")),
                "name": "Sessions logged before efforts existed", "status": "done", "legacy_ref": imp.ref("efforts", "old")})
            for s in old:
                imp.insert("sessions", {
                    "effort_id": hist, "status": "completed", "developer": s["developer"],
                    "started_at": _ts(s["timestamp"]), "ended_at": _ts(s["timestamp"]),
                    "summary": f"[{s['project']}] {s['completed_work']}", "outcome": s["status"],
                    "next_steps": s["next_steps"], "legacy_ref": imp.ref("sessions_old", s["id"])})


def run(dst: sqlite3.Connection, source: Path, label: str | None = None, apply: bool = False) -> Counter:
    src = sqlite3.connect(f"file:{Path(source).as_posix()}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    try:
        kind = detect(src)
        imp = Importer(dst, label or Path(source).stem)
        dst.execute("BEGIN IMMEDIATE")
        try:
            (import_concierge if kind == "concierge" else import_cmc)(src, imp)
            if apply:
                dst.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, strftime('%Y-%m-%dT%H:%M:%SZ','now'))",
                            (f"imported:{imp.label}",))
                dst.execute("COMMIT")
            else:
                dst.execute("ROLLBACK")
        except BaseException:
            dst.execute("ROLLBACK")
            raise
        imp.counts["_kind:" + kind] = 1
        return imp.counts
    finally:
        src.close()


__all__ = ["run", "detect", "ledger"]
