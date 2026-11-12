"""Live token tracking for the current session, and the context-size warning.

Two plugin hooks drive this module:

- **Stop** (after every reply): ``record()`` reads the transcript lines written
  since the last call and updates this session's row in the ``session_tokens``
  ledger table: turns, tool calls, cumulative tokens, the current context size
  and the peak. Reading is incremental (a stored byte offset), so a long
  session costs the same per turn as a short one.
- **UserPromptSubmit** (before every prompt): ``guard()`` reads the tail of the
  transcript for the current context size and, past a workspace's limits,
  tells Claude and the user it is time for a handoff. Each limit warns once
  per session; a compacted session that grows back past it warns again.

"Context" is the size of the last request: fresh input + cache reads + cache
writes. Every turn re-reads the whole window, so it is what a long session
really costs and what decides when quality starts to slip.

Limits come from ``workspace.yaml``::

    tokens:
      soft: 300000   # finish the current thread, then hand off
      hard: 380000   # hand off now
                     # 0 turns a limit off

Hooks must never break a session: every public entry point here swallows its
own errors.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import ledger, paths

DEFAULT_SOFT = 300_000
DEFAULT_HARD = 380_000
TAIL_STEPS = (256 * 1024, 4 * 1024 * 1024, None)   # bytes from the end; None = whole file
REARM_RATIO = 0.8   # context below soft * this re-arms the warnings (after a compaction)
HANDOFF = "/caveman-core:handoff"


@dataclass(frozen=True)
class Limits:
    soft: int
    hard: int

    def band(self, context: int) -> int:
        """0 = fine, 1 = past soft, 2 = past hard."""
        if self.hard and context >= self.hard:
            return 2
        if self.soft and context >= self.soft:
            return 1
        return 0


def limits(ws: paths.Workspace | None) -> Limits:
    cfg = ((ws.config if ws else {}) or {}).get("tokens") or {}

    def _int(key: str, default: int) -> int:
        try:
            return max(0, int(cfg.get(key, default)))
        except (TypeError, ValueError):
            return default
    return Limits(_int("soft", DEFAULT_SOFT), _int("hard", DEFAULT_HARD))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _context(usage: dict) -> int:
    return ((usage.get("input_tokens") or 0) + (usage.get("cache_read_input_tokens") or 0)
            + (usage.get("cache_creation_input_tokens") or 0))


def _main_assistant(obj: dict) -> dict | None:
    """The message of a main-thread assistant entry that carries usage, else None."""
    if obj.get("type") != "assistant" or obj.get("isSidechain"):
        return None
    msg = obj.get("message") or {}
    return msg if msg.get("usage") else None


# ------------------------------------------------------------ current context

def current_context(transcript: Path) -> tuple[int, str | None]:
    """(tokens, model) of the last main-thread request, reading only the tail."""
    try:
        size = transcript.stat().st_size
    except OSError:
        return 0, None
    for step in TAIL_STEPS:
        start = 0 if step is None or step >= size else size - step
        with transcript.open("rb") as fh:
            fh.seek(start)
            data = fh.read()
        lines = data.split(b"\n")
        if start:
            lines = lines[1:]          # first line is probably cut in half
        for raw in reversed(lines):
            if b'"assistant"' not in raw:
                continue
            try:
                msg = _main_assistant(json.loads(raw))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if msg:
                return _context(msg["usage"]), msg.get("model")
        if start == 0:
            break
    return 0, None


# ------------------------------------------------------------ per-session row

def _row(con: sqlite3.Connection, session_id: str):
    return con.execute("SELECT * FROM session_tokens WHERE session_id = ?", (session_id,)).fetchone()


def record(ws: paths.Workspace, session_id: str, transcript: Path) -> dict | None:
    """Fold the transcript lines added since the last call into the session's row."""
    try:
        size = transcript.stat().st_size
    except OSError:
        return None
    con = ledger.connect(ws)
    try:
        with ledger.write(con):
            row = _row(con, session_id)
            if row is None or size < (row["scan_offset"] or 0):
                # new session, or the file was rewritten: count from the top
                con.execute("DELETE FROM session_tokens WHERE session_id = ?", (session_id,))
                con.execute("INSERT INTO session_tokens (session_id, transcript, started_at, updated_at) "
                            "VALUES (?, ?, ?, ?)", (session_id, str(transcript), _now(), _now()))
                row = _row(con, session_id)
            r = dict(row)
            with transcript.open("rb") as fh:
                fh.seek(r["scan_offset"])
                data = fh.read(size - r["scan_offset"])
            end = data.rfind(b"\n")
            if end < 0:
                return r
            last_id = r["last_message_id"]
            for raw in data[:end].split(b"\n"):
                if b'"assistant"' not in raw:
                    continue
                try:
                    obj = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if obj.get("type") != "assistant" or obj.get("isSidechain"):
                    continue
                msg = obj.get("message") or {}
                r["tool_calls"] += sum(1 for b in (msg.get("content") or [])
                                       if isinstance(b, dict) and b.get("type") == "tool_use")
                usage = msg.get("usage")
                mid = msg.get("id") or obj.get("requestId") or obj.get("uuid")
                if not usage or (mid and mid == last_id):
                    continue           # one message spans several lines; count its usage once
                last_id = mid
                ctx = _context(usage)
                r["turns"] += 1
                r["input_tokens"] += ctx
                r["output_tokens"] += usage.get("output_tokens") or 0
                r["cache_read_tokens"] += usage.get("cache_read_input_tokens") or 0
                r["cache_write_tokens"] += usage.get("cache_creation_input_tokens") or 0
                r["context_tokens"] = ctx
                r["peak_context_tokens"] = max(r["peak_context_tokens"], ctx)
                r["model"] = msg.get("model") or r["model"]
            r["scan_offset"] += end + 1
            r["last_message_id"] = last_id
            r["updated_at"] = _now()
            lim = limits(ws)
            if lim.soft and r["context_tokens"] < lim.soft * REARM_RATIO:
                r["warned_band"] = 0
            cols = ("transcript", "model", "updated_at", "turns", "tool_calls", "input_tokens", "output_tokens",
                    "cache_read_tokens", "cache_write_tokens", "context_tokens", "peak_context_tokens",
                    "warned_band", "scan_offset", "last_message_id")
            r["transcript"] = str(transcript)
            con.execute(f"UPDATE session_tokens SET {', '.join(f'{c} = ?' for c in cols)} WHERE session_id = ?",
                        (*(r[c] for c in cols), session_id))
            return r
    finally:
        con.close()


# ------------------------------------------------------------ warning

def warning_text(context: int, band: int, lim: Limits) -> str:
    k = round(context / 1000)
    if band >= 2:
        return (f"Context is at ~{k}k tokens, past this workspace's hard limit ({lim.hard // 1000}k). "
                f"Run {HANDOFF} now, then start a fresh session: every further turn re-reads the whole window.")
    return (f"Context is at ~{k}k tokens, past this workspace's soft limit ({lim.soft // 1000}k). "
            f"Finish the current thread, then run {HANDOFF} and start a fresh session. "
            f"The handoff itself needs room, so don't leave it until the hard limit.")


def guard(ws: paths.Workspace, session_id: str, transcript: Path) -> str | None:
    """The warning to show before this prompt, or None. Each band warns once per session."""
    lim = limits(ws)
    if not (lim.soft or lim.hard):
        return None
    context, model = current_context(transcript)
    band = lim.band(context)
    if band == 0:
        return None
    con = ledger.connect(ws)
    try:
        with ledger.write(con):
            row = _row(con, session_id)
            if row is None:
                con.execute("INSERT INTO session_tokens (session_id, transcript, model, started_at, updated_at, "
                            "context_tokens, peak_context_tokens) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (session_id, str(transcript), model, _now(), _now(), context, context))
                warned = 0
            else:
                warned = row["warned_band"] or 0
            if warned >= band:
                return None
            con.execute("UPDATE session_tokens SET warned_band = ? WHERE session_id = ?", (band, session_id))
    finally:
        con.close()
    return warning_text(context, band, lim)


# ------------------------------------------------------------ hook entry points

def _hook_workspace(payload: dict) -> paths.Workspace | None:
    try:
        return paths.resolve()
    except paths.WorkspaceNotFound:
        cwd = payload.get("cwd")
        if cwd:
            try:
                return paths.resolve(cwd=Path(cwd))
            except paths.WorkspaceNotFound:
                return None
    return None


def _payload(stdin_text: str) -> tuple[dict, paths.Workspace | None, str | None, Path | None]:
    payload = json.loads(stdin_text or "{}")
    tp = payload.get("transcript_path")
    transcript = Path(tp) if tp else None
    sid = payload.get("session_id") or (transcript.stem if transcript else None)
    return payload, _hook_workspace(payload), sid, transcript


def hook_prompt(stdin_text: str) -> str:
    """UserPromptSubmit: JSON for Claude Code, or '' to stay silent."""
    try:
        _p, ws, sid, transcript = _payload(stdin_text)
        if not (ws and sid and transcript and transcript.is_file()):
            return ""
        msg = guard(ws, sid, transcript)
        if not msg:
            return ""
        return json.dumps({"systemMessage": f"caveman-core: {msg}",
                           "hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": msg}})
    except Exception:  # noqa: BLE001 - never block a prompt
        return ""


def hook_stop(stdin_text: str) -> None:
    """Stop: update this session's token row. Silent."""
    try:
        _p, ws, sid, transcript = _payload(stdin_text)
        if ws and sid and transcript and transcript.is_file():
            record(ws, sid, transcript)
    except Exception:  # noqa: BLE001 - never fail a session stop
        pass


# ------------------------------------------------------------ CLI

def report(ws: paths.Workspace, session_id: str | None = None, limit: int = 10) -> str:
    lim = limits(ws)
    head = (f"Limits for {ws.name}: soft {lim.soft:,} / hard {lim.hard:,} "
            f"(workspace.yaml `tokens:`; 0 = off)")
    if not ws.ledger_path.exists():
        return head + "\n(no ledger yet)"
    con = ledger.connect(ws, readonly=True)
    try:
        try:
            if session_id:
                rows = con.execute("SELECT * FROM session_tokens WHERE session_id LIKE ? ORDER BY updated_at DESC",
                                   (session_id + "%",)).fetchall()
            else:
                rows = con.execute("SELECT * FROM session_tokens ORDER BY updated_at DESC LIMIT ?",
                                   (limit,)).fetchall()
        except sqlite3.OperationalError:
            rows = []
    finally:
        con.close()
    lines = [head, ""]
    if not rows:
        lines.append("(no sessions recorded yet - the Stop hook fills this in after each reply)")
    for r in rows:
        band = lim.band(r["context_tokens"])
        flag = {0: "", 1: "  << past soft", 2: "  << past hard"}[band]
        lines.append(f"{r['session_id'][:8]}  {r['updated_at']}  {r['model'] or '?':18}  turns {r['turns']:>4}  "
                     f"context {r['context_tokens']:>9,}  peak {r['peak_context_tokens']:>9,}  "
                     f"out {r['output_tokens']:>9,}{flag}")
    return "\n".join(lines)
