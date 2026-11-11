"""Token accounting from the JSONL transcripts Claude Code writes under
``~/.claude/projects``.

Claude Code can write one assistant message across several transcript lines
(one per content block), each repeating the same usage block, so usage is
de-duplicated by message id before summing.

``input`` here includes cache reads and cache writes, matching what the
legacy CMC session log recorded. These are estimates for trend-watching, not
billing figures: subscription limits are computed server-side and weight
cache reads far below fresh input.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator


@dataclass
class Usage:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    fresh_input: int = 0

    def add(self, u: dict) -> None:
        fresh = u.get("input_tokens") or 0
        cr = u.get("cache_read_input_tokens") or 0
        cw = u.get("cache_creation_input_tokens") or 0
        self.fresh_input += fresh
        self.cache_read += cr
        self.cache_write += cw
        self.input += fresh + cr + cw
        self.output += u.get("output_tokens") or 0


def iter_events(path: Path) -> Iterator[tuple[datetime | None, str, dict]]:
    """Yield (timestamp, model, usage) once per assistant message in one transcript."""
    seen: set[str] = set()
    try:
        fh = path.open(encoding="utf-8")
    except OSError:
        return
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "assistant":
                continue
            msg = obj.get("message") or {}
            usage = msg.get("usage")
            if not usage:
                continue
            key = msg.get("id") or obj.get("requestId") or obj.get("uuid")
            if key:
                if key in seen:
                    continue
                seen.add(key)
            ts = obj.get("timestamp")
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None
            yield dt, msg.get("model", "unknown"), usage


def transcript_tokens(path: Path) -> Usage:
    total = Usage()
    for _dt, _model, u in iter_events(path):
        total.add(u)
    return total


def report(root: Path, days: int = 7) -> str:
    """Per-day, per-model totals across every transcript under ``root``."""
    since: date = (datetime.now(timezone.utc) - timedelta(days=days - 1)).date()
    by_day: dict[date, dict[str, Usage]] = defaultdict(lambda: defaultdict(Usage))
    for f in root.glob("**/*.jsonl"):
        for dt, model, u in iter_events(f):
            if dt and dt.date() >= since:
                by_day[dt.date()][model].add(u)
    lines = [f"Token usage, last {days} day(s) (estimate; input includes cache):", ""]
    grand = Usage()
    for d in sorted(by_day):
        day = Usage()
        for model, u in sorted(by_day[d].items()):
            lines.append(f"  {d}  {model:32} in {u.input:>12,}  out {u.output:>10,}")
            day.input += u.input
            day.output += u.output
        grand.input += day.input
        grand.output += day.output
    lines += ["", f"  total{'':37} in {grand.input:>12,}  out {grand.output:>10,}"]
    return "\n".join(lines)
