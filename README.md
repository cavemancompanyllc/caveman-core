# Caveman Core

The base layer for a family of [Claude Code](https://claude.com/claude-code) plugins.
Every Caveman plugin declares `caveman-core` as a dependency and gets, for free:

- **A workspace model** — a folder with a `workspace.yaml` is a workspace. Plugins
  find it the same way every time and keep their data in it, never inside the plugin.
- **The ledger** — one SQLite file per workspace (`data/ledger.db`) holding
  projects, efforts, sessions, tasks and decisions. Plugins can add their own tables.
- **One session handoff** — `/caveman-core:handoff` records what happened, what's
  next and how many tokens it took, updates a living handoff note, optionally
  appends a retro, and commits.
- **Session rules** — plugins can't ship a `CLAUDE.md`, so each one puts its
  always-on rules in `rules/session.md`, and core loads them at session start.
- **Shared code** — `.env` loading, local-LLM (Ollama) calls, `doctor`, `backup`
  and token `usage`, in the `caveman_core` Python package and `cavecore` CLI.

This repo is both the **`caveman-core` marketplace** and the **`caveman-core`
Python package**.

## Install

1. **The package** (Python 3.11+). Install it into the same Python that runs your
   plugins' scripts — the `python` on your `PATH` — so hooks and plugin scripts can
   import it alongside their other dependencies:

   ```bash
   python -m pip install --user "caveman-core @ git+https://github.com/cavemancompanyllc/caveman-core@v1.5.0"
   python -m caveman_core --version
   ```

   Install a tagged release, not an editable checkout: a workspace should never
   run code you haven't released. For the short `cavecore` command, put pip's
   user `Scripts` (Windows) or `bin` folder on your `PATH`; until then
   `python -m caveman_core` works everywhere `cavecore` does.

2. **The plugin**, from inside a workspace:

   ```bash
   claude plugin marketplace add cavemancompanyllc/caveman-core
   claude plugin install caveman-core@caveman-core --scope project
   ```

   Plugins that depend on core install it automatically.

3. **A workspace**, if the folder isn't one yet:

   ```bash
   cavecore workspace init --name "My Workspace" --kind personal          # dry run
   cavecore workspace init --name "My Workspace" --kind personal --apply
   cavecore doctor
   ```

## Workspace layout

```
<workspace>/
  CLAUDE.md                    persona + workspace-only rules; keep it short
  workspace.yaml               name, slug, kind, tasks, retro.path
  bootstrap.ps1                installs the workspace's plugins on a new machine
  .claude/settings.json        committed: marketplaces + enabled plugins
  .claude/settings.local.json  ignored: machine-specific permissions
  .env.example                 committed; .env is ignored
  config/                      *.example.* committed; real files ignored
  data/                        ignored: ledger.db, plugin databases, tokens
  local/                       ignored: handoff.md, plans, scratch
```

`workspace.yaml`:

```yaml
name: My Workspace
slug: my-workspace
kind: personal        # personal | business | kids | dev | sandbox
tasks: ledger         # ledger | ado | none - where tasks are tracked
retro:
  path: docs/lessons-learned.md   # optional: handoff appends retros here
plugins:                          # optional: non-secret, workspace-specific plugin settings
  concierge-memory-obsidian:
    vault_path: D:/vaults/home
```

**Where settings live.** Claude Code stores plugin `userConfig` values per
*user*, so they apply in every workspace on the machine. Use them only for
values that really are machine-wide (a binary path, the Ollama host).
Everything that differs between workspaces goes in the workspace:

| Kind of value | Where | Read with |
|---|---|---|
| Secrets (API keys, client secrets, tokens) | `.env` (gitignored) | `caveman_core.env.load()`; MCP servers get them from the environment |
| Non-secret plugin settings (vault path, IDs, labels) | `workspace.yaml` → `plugins:` | `cavecore setting get <plugin>.<key>` or `Workspace.setting()` |
| Larger per-workspace data (routing tables, rules) | `config/*.json` (real files gitignored, `*.example.json` committed) | the plugin's own code |

`env.load()` also fills variables that exist but are empty, so an optional
`userConfig` field left blank falls through to the workspace's `.env`.

## The `cavecore` CLI

```
cavecore which                         which workspace, and which rule found it
cavecore setting get <plugin>.<key>    a workspace.yaml plugin setting (empty if unset)
cavecore run [--log F] <plugin> <script> [args]   run a plugin's script, whatever version is installed
cavecore headless --prompt-file <f>    one unattended Claude run (Task Scheduler / cron); prompt path may be
                                       workspace-relative; tools from <f>.allowedtools or default.allowedtools
cavecore guard install                 pre-push hook: deny-list + gitleaks before public pushes
cavecore work status                   active efforts
cavecore work project|effort|task|decision ...
cavecore work session start|end|log|list ...
cavecore ledger init|info|sql|import-legacy
cavecore workspace init                scaffold a workspace (dry run unless --apply)
cavecore doctor                        check machine + workspace setup
cavecore backup --dest <dir>           snapshot data/*.db and config/
cavecore usage [--days N]              token usage from Claude transcripts
```

Workspace resolution order: `--workspace`, `CAVEMAN_HOME`, `CONCIERGE_HOME`
(legacy), then a walk up from `CLAUDE_PROJECT_DIR` or the current directory to
the nearest `workspace.yaml`.

## Writing a plugin on top of core

- Declare the dependency in `.claude-plugin/plugin.json`:
  `"dependencies": ["caveman-core@caveman-core"]`, and in your marketplace's
  `marketplace.json` add `"allowCrossMarketplaceDependenciesOn": ["caveman-core"]`.
- Put always-on rules in `rules/session.md` (short: the first 25 non-blank
  lines are loaded).
- Put ledger tables in `schema/*.sql`, using `CREATE TABLE IF NOT EXISTS`.
- List required CLIs and env vars in `requires.json`
  (`{"cli": ["git"], "env": ["MY_API_KEY"]}`) so `cavecore doctor` can check them.
  It sits beside the manifest because `claude plugin validate --strict`
  rejects unknown fields in `plugin.json`.
- In Python: `from caveman_core import paths, env, ledger, llm`.
- Anything outside Claude Code that calls your script (scheduled prompts,
  `--allowedTools` rules, wrappers) should use
  `python -m caveman_core run <plugin> <script> ...`, never a path into the
  plugin cache: those folders are versioned and old versions get pruned.

## Keeping personal data out of public repos

In the local clone of any public repo, run `cavecore guard install`. Every push
then checks the commits being pushed against a deny-list kept **outside every
repo** (`~/.caveman/denylist.txt`, or `CAVEMAN_DENYLIST`; one term per line,
`re:` for a regex) and, when it is installed, gitleaks. A match blocks the push
and names the commit, file and term.

## Developing core

```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"
.venv/Scripts/python -m pytest
claude plugin validate . --strict
```

Try the plugin without installing it: `claude --plugin-dir ./plugins/caveman-core`
from a throwaway workspace, then `/reload-plugins` after edits.

**Release:** bump `version` in `plugins/caveman-core/.claude-plugin/plugin.json`
and `__version__` in `src/caveman_core/__init__.py` together, run the tests and
`claude plugin validate . --strict`, commit, then tag `caveman-core--v<version>`
and `v<version>`.

## License

MIT
