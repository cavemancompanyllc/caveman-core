---
description: End a session - record it in the ledger, update the living handoff note, optionally write a retro, and commit.
argument-hint: "[--no-git] [--no-push] [--no-retro] [effort-slug]"
---

> If `cavecore` isn't found, use `python -m caveman_core` with the same arguments (the package is installed but its Scripts folder isn't on PATH).

Perform a session handoff for this workspace. Arguments: $ARGUMENTS

Write everything below for a reader with zero memory of this conversation.

1. **Find the workspace.** Run `cavecore which`. If it fails, stop and tell the user to run
   `/caveman-core:workspace-init`. Note the handoff file, retro path and task mode it prints.

2. **Summarise the session yourself** from this conversation and `git status` / `git log` in each repo
   you touched. Don't ask the user to write it:
   - what got done (`--summary`)
   - where the work stands now (`--current-state`)
   - next steps in priority order (`--next-steps`)
   - one line on the state of the work at handoff: committed? pushed? verified? (`--outcome`)
   - key decisions and blockers, if any

3. **Pick the effort.** Use the slug passed in the arguments if there is one. Otherwise run
   `cavecore work status` and choose the effort this session served. If none fits, ask the user
   whether to create one (`cavecore work effort add <project> <slug> "<name>"`) or use `general`.
   Don't guess a project hierarchy.

4. **Record the session** in one command:

   ```bash
   cavecore work session log <effort-slug> --transcript auto \
     --summary "..." --current-state "..." --next-steps "..." --outcome "..." \
     [--decisions "..."] [--blockers "..."] [--git-refs "<commits>"] [--status <new effort status>]
   ```

   `--transcript auto` records this session's token counts. If the workspace tracks tasks in the
   ledger (`tasks: ledger`), also close finished tasks (`cavecore work task done <id>`) and add new
   ones that surfaced. If it tracks tasks in ADO (`tasks: ado`), update work-item states there
   instead and put the IDs in `--git-refs` or the summary.

5. **Update the living handoff file** (path from step 1, default `local/handoff.md`). **Read it
   first.** Other sessions may share it: merge your changes into the sections for the efforts you
   touched and leave everything else as it is. Never overwrite the whole file blindly. It should
   hold, per active effort: where it stands, and next steps in priority order.

6. **Retro** (skip with `--no-retro`). Review the session itself, not just the output:
   - Went well: approaches worth repeating.
   - Went wrong: mistakes, dead ends, wasted turns, and their root cause.
   - Script candidates: multi-step manual sequences that should become a tool.

   If `cavecore which` printed a retro path, append a dated entry there:
   `## <YYYY-MM-DD> - <effort-slug>` with those three bullet groups. If a finding is a durable rule
   about how to work (not specific to this task), save it to memory as well.

7. **Commit** (skip with `--no-git`). For each repo you changed this session: show the user the
   list of files you are about to stage, stage only files this session touched (other sessions
   may have uncommitted work in the same repo), and commit with a message that follows the repo's
   conventions (for example a work-item ID, if the workspace's rules ask for one). Push only if the
   repo has a remote and pushing is routine for it; skip with `--no-push`. Never force-push.

8. **Report back** in a few lines: the effort, what was logged, the token count, what was
   committed and pushed, and the top next step.
