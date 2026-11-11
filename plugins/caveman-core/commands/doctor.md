---
description: Check that this machine and workspace are set up correctly for Caveman plugins.
---

Run:

```bash
cavecore doctor --plugin-root "${CLAUDE_PLUGIN_ROOT}"
```

If `cavecore` isn't found, the caveman-core Python package isn't installed or its venv isn't on
`PATH`. Point the user to https://github.com/cavemancompanyllc/caveman-core#install.

Otherwise explain each `WARN` and `FAIL` line in plain words, with the fix. Offer to make fixes
that only touch this workspace's files (for example a `.gitignore` line). Don't change user-level
settings (`~/.claude/settings.json`) or install anything without asking first.
