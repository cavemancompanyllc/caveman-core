---
description: Set up this folder as a Caveman workspace (workspace.yaml, .gitignore, settings, folders).
argument-hint: "[name] [personal|business|kids|dev|sandbox]"
---

Set up the current folder as a workspace. Arguments: $ARGUMENTS

1. Work out the name and kind from the arguments. If either is missing, ask the user. Kinds:
   `personal`, `business`, `kids`, `dev` (a code repo you also work in) or `sandbox` (throwaway,
   for testing plugins). Also ask whether tasks are tracked in the ledger (default), in Azure
   DevOps (`--tasks ado`) or nowhere, and whether retros should be appended to a file
   (`--retro-path docs/lessons-learned.md`, typical for code repos).

2. Show the plan with a dry run and read it to the user:

   ```bash
   cavecore workspace init --name "<name>" --kind <kind> [--tasks ado] [--retro-path <file>]
   ```

   It never overwrites existing files: it only creates missing ones, appends missing `.gitignore`
   lines, and adds the caveman-core marketplace and plugin to `.claude/settings.json`.

3. After the user agrees, run the same command with `--apply`, then `cavecore doctor`, and walk
   the user through any WARN or FAIL lines.

4. Help fill in `CLAUDE.md`: a sentence on what the workspace is for, plus only the rules that are
   specific to it. Rules that come with a plugin load automatically from that plugin, so don't
   copy them into `CLAUDE.md`.

5. If the folder already has session history in an older database (`data/jarvis.db` from
   concierge-core, or a CMC `.data/cmc.db`), offer to import it:
   `cavecore ledger import-legacy <file>` (dry run), then again with `--apply`.
