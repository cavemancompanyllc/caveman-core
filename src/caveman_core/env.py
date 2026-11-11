"""The one ``.env`` loader every plugin script uses."""

from __future__ import annotations

import os
from pathlib import Path


def parse(text: str) -> dict[str, str]:
    """Parse dotenv text: ``KEY=value``, optional ``export``, quotes, ``#`` comments."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            quote = value[0]
            value = value[1:-1]
            if quote == '"':
                value = value.replace("\\n", "\n").replace('\\"', '"')
        else:
            # strip an inline comment only when preceded by whitespace
            for i, ch in enumerate(value):
                if ch == "#" and i > 0 and value[i - 1].isspace():
                    value = value[:i].rstrip()
                    break
        if key:
            out[key] = value
    return out


def load(workspace_root: Path, override: bool = False) -> dict[str, str]:
    """Load ``<workspace>/.env`` into ``os.environ``.

    Variables already set to a non-empty value win unless ``override`` is
    true, so a value passed by Claude Code (e.g. from plugin userConfig) is
    never clobbered by the file. Variables that exist but are *empty* are
    filled from the file: an optional userConfig field left blank reaches the
    process as an empty string, and the workspace's own .env should win then.
    Returns what the file contained.
    """
    f = Path(workspace_root) / ".env"
    if not f.is_file():
        return {}
    values = parse(f.read_text(encoding="utf-8-sig"))
    for k, v in values.items():
        if override or not os.environ.get(k):
            os.environ[k] = v
    return values
