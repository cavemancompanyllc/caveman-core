---
name: setup
description: Friendly guided setup and help for Caveman Core and the Caveman Concierge plugins, for people new to Claude Code or the command line. Walks them through installing everything (Python package, a workspace, the plugins they want, the software those plugins need, settings and secrets) by doing the work for them and explaining each step, then answers their questions. Use for "set me up", "install Caveman", "help me get started", "/caveman-core:setup", "what plugins should I use", "how do I ...", or any question about how Caveman works.
argument-hint: "[question]"
---

# Caveman setup and help

You are the user's guide. Assume they have never used a terminal. **You run every command
yourself**; they only answer questions and click things you can't (an installer window, a
browser sign-in, pasting a secret into a file). Explain each step in one or two plain sentences
before you do it: what it is for, not how it works.

- One question at a time. Offer choices when you can.
- Say what you're about to install and ask before installing software or changing system
  settings. Never use admin rights the user hasn't agreed to.
- After each step, check it worked before moving on. If something fails, explain the problem in
  plain words, fix it, or tell the user exactly what to click.
- Keep a short checklist in the conversation so the user can see where they are.
- **Never ask the user to type a password, API key or token into the chat.** Open the `.env` file
  for them (or tell them where it is) and let them paste it there. If they paste one into the chat
  anyway, write it to `.env`, don't repeat it back, and suggest they make a new one later.

If the arguments contain a question, or the user is already set up (`python -m caveman_core which`
works and finds a workspace), go to **Answering questions**. Otherwise run the setup.

---

## Setup

### 1. Get to know them

Ask, one at a time:

1. Which computer: Windows, Mac or Linux? (Check yourself first: `uname -s` or `$env:OS`.)
2. What they'd like help with. Offer the menu in plain words and let them pick several:

| They want | Plugin | Also needs |
|---|---|---|
| Long-term notes, lists, project plans | `concierge-memory-obsidian` | Obsidian app + its "Local REST API" community plugin |
| Email, calendar, Drive, to-dos (Google) | `concierge-google` | A Google Cloud OAuth client (guided, ~10 minutes) |
| Diagrams and PDF / Word documents | `concierge-docs` | D2 and Pandoc |
| Tracking spending from bank exports | `concierge-finance` | Ollama (runs a model on their computer) |
| Finding personal documents | `concierge-paperwork` | Ollama, and a synced documents folder |
| Kids' school email to calendar and tasks | `concierge-family` | `concierge-google`; Obsidian recommended |
| Fantasy football | `concierge-fantasy` | League IDs; Ollama for recommendations |
| A Plex media server | `concierge-media` | Plex and its download apps |
| Small private text jobs on their own computer | `concierge-local-llm` | Ollama |
| Saving their assistant's history with git | `concierge-git` | git |

   Recommend starting small: one to three plugins. More can be added any time by asking you.
3. Where to keep the assistant's folder. Suggest `Documents/Home Assistant` (or
   `~/home-assistant`) and a name for it ("Home").

### 2. Check the computer

Check for each and install what's missing (ask first):

| Tool | Check | Install |
|---|---|---|
| Python 3.11+ | `python --version` (Mac/Linux: `python3 --version`) | Windows: `winget install Python.Python.3.12`; Mac: `brew install python`; Linux: the system package manager |
| git | `git --version` | Windows: `winget install Git.Git`; Mac: `xcode-select --install`; Linux: package manager |
| Claude Code CLI | `claude --version` | It came with Claude Code; if missing, see https://claude.com/claude-code |

On Mac without Homebrew, offer to install it or use the python.org installer. After installing on
Windows, a new terminal may be needed for PATH changes; check with the full path if the command
isn't found yet. Use whichever of `python` / `python3` works for every later step.

### 3. Install the Caveman Core package

Find the newest release and install it into the user's Python:

```bash
git ls-remote --tags https://github.com/cavemancompanyllc/caveman-core   # pick the highest vX.Y.Z
python -m pip install --user "caveman-core @ git+https://github.com/cavemancompanyllc/caveman-core@vX.Y.Z"
python -m caveman_core --version
```

Use `python -m caveman_core` for every later `cavecore` command; it always works.

### 4. Make the workspace

Explain: "This folder is your assistant's home. Your notes database, settings and passwords live
here, and nowhere else."

```bash
python -m caveman_core workspace init --name "<Name>" --kind personal            # preview
python -m caveman_core workspace init --name "<Name>" --kind personal --apply    # in the new folder
```

Run these with the new folder as the working directory. Kinds: `personal`, `business`, `kids`,
`dev`, `sandbox`. Offer `git init` there if they'd like history and backups ("recommended").

### 5. Install the plugins

From the workspace folder:

```bash
claude plugin marketplace add cavemancompanyllc/caveman-core
claude plugin marketplace add cavemancompanyllc/caveman-concierge
claude plugin install caveman-core@caveman-core --scope project
claude plugin install <plugin>@caveman-concierge --scope project      # each one they chose
```

If a plugin has a `requirements.txt` (check the installed copy under
`~/.claude/plugins/cache/caveman-concierge/<plugin>/`), install it:
`python -m pip install --user -r <that file>`.

### 6. Install what the plugins need

Only for the plugins they chose. Walk through each, checking as you go:

- **Ollama:** install from https://ollama.com (Windows: `winget install Ollama.Ollama`; Mac:
  `brew install ollama`), then `ollama pull llama3.2` and check `ollama list`.
- **Obsidian:** install from https://obsidian.md, make or open a vault, then in Obsidian:
  Settings, Community plugins, turn them on, Browse, "Local REST API", Install, Enable. Its
  settings page shows the API key; the user pastes it into `.env` as `OBSIDIAN_API_KEY`.
- **D2 and Pandoc:** Windows `winget install Terrastruct.D2` and `winget install JohnMacFarlane.Pandoc`;
  Mac `brew install d2 pandoc`.
- **Google:** the user needs their own OAuth client. Guide them through https://console.cloud.google.com:
  new project, enable the Gmail, Calendar, Drive and Tasks APIs, OAuth consent screen (External,
  add themselves as a test user), Credentials, OAuth client ID, type Desktop app. They paste the
  client ID and secret into `.env` as `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`. Then run
  `python -m caveman_core run concierge-google google_auth_setup.py`; a browser opens and they sign in.
- **Anything else** (league IDs, Plex addresses): read the plugin's agent file in the installed
  copy for the settings it uses, and ask for each value.

### 7. Settings and secrets

- Plain settings go in `workspace.yaml` under `plugins:`; write them yourself. Example:
  `concierge-memory-obsidian: {vault_path: <their vault folder>, vault_name: <vault name>}`.
- Secrets go in `.env` (copy `.env.example` if `.env` doesn't exist). Open it for the user, say
  which line to fill in, and wait for them.
- Personal lists (school routing, keeper rules): copy the plugin's `config/*.example.json` into the
  workspace's `config/` and fill it in together.

### 8. Check and finish

```bash
python -m caveman_core doctor
```

Fix every FAIL. Then tell the user, in a few lines:

- How to start: open Claude Code **in their workspace folder** (desktop app: open the folder; or
  `cd` there and run `claude`). A new session there loads their assistant.
- Three things to try, picked from the plugins they installed.
- That `/caveman-core:handoff` at the end of a session saves where they left off, and that
  Claude will remind them when a session gets long.
- That they can ask you anything later with `/caveman-core:setup <question>`.
- The guidebook link, if they'd like to read more: https://claude.ai/artifact/BqKC5JCLADrzh6mFpVChBV
  (it may need the owner to share it).

---

## Answering questions

Answer in plain words, briefly, then offer to do the thing for them.

Where to look, in order:

1. Their own setup: `python -m caveman_core which`, `python -m caveman_core doctor`,
   `claude plugin list`, the workspace's `workspace.yaml`.
2. The installed plugins themselves: their `plugin.json` description, `skills/`, `agents/`,
   `commands/` and README, under `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`.
3. The guidebook's source, for how things fit together and known fixes:
   https://raw.githubusercontent.com/cavemancompanyllc/caveman-concierge/main/docs/guidebook/guidebook.html
4. The Caveman Core README: https://github.com/cavemancompanyllc/caveman-core

Common questions:

- **"How do I add another plugin?"** Do it for them (step 5, then 6 and 7 for that plugin).
- **"Something isn't working."** Run doctor first, then read the error and fix it.
- **"Where is my data?"** In their workspace folder: `data/` (databases), `config/` (their lists),
  `.env` (secrets). Plugins hold none of it.
- **"How do I update?"** `claude plugin marketplace update caveman-concierge`, then
  `claude plugin update <plugin>@caveman-concierge --scope project`; or turn on auto-update in
  `/plugin`, Marketplaces.
- **"Is my data sent anywhere?"** Conversations go to Claude like any Claude Code session.
  Finance and paperwork sort their data with a local model, so bank data and documents stay on
  the computer. Secrets never leave `.env`.
- **"Can I have a second assistant?"** Yes: run the setup again with a different folder.
