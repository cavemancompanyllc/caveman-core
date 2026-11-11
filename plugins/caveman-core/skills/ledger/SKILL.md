---
name: ledger
description: Track work across Claude Code sessions in this workspace's ledger (Project > Effort > Session/Task, plus decisions). Use when the user wants to start, resume or wrap up work on a named effort ("let's pick up the garden project", "what's the status of X", "add that as a task"), create a project or effort, log a decision, or see what's open. This replaces markdown handoff notes as the source of truth for continuity.
---

> If `cavecore` isn't found, use `python -m caveman_core` with the same arguments (the package is installed but its Scripts folder isn't on PATH).

# Ledger

Every workspace has a SQLite ledger at `data/ledger.db`. All access goes through the `cavecore`
CLI, run from anywhere inside the workspace.

```
Project  - a long-running initiative (like an Epic), e.g. "Home Lab"
  Effort - a scoped chunk of work (like a Feature), e.g. "Home Assistant setup"
    Session - one Claude Code conversation working that effort (append-only log)
    Task    - a next-action within the effort; can outlive many sessions
Decision - a choice and its rationale, on a project or effort
```

Each effort has two **living fields**, overwritten whenever a session ends: `current_state` and
`next_steps`. They are the fast path for picking up where you left off.

## Resuming an effort

```bash
cavecore work status                       # active efforts at a glance
cavecore work effort list [--project p]    # all efforts
cavecore work session start <effort-slug>  # opens a session and prints the resume context
cavecore work effort show <effort-slug>    # the same context without opening a session
```

Read the printed context: project, description, current state, next steps, last session and open
tasks. `session start` also marks any stale in-progress session for that effort as abandoned.

If the effort doesn't exist, ask which project it belongs to (or whether it's a new project)
before creating it. Don't guess the hierarchy:

```bash
cavecore work project add <slug> "<Name>" [--description "..."] [--status active]
cavecore work effort add <project-slug> <effort-slug> "<Name>" [--description "..."]
```

## One-off requests: the `general` bucket

Quick questions and one-shot asks need no session at all. For small requests worth remembering
that fit no effort, use `general` as the effort slug. It resolves to a monthly effort
(`general-2026-11`) under a `general` project, created on first use.

## Ending a session

Use `/caveman-core:handoff` for a full wrap-up (ledger, handoff note, retro, commit). To record
the session alone:

```bash
cavecore work session end <effort-slug> --summary "..." --current-state "..." --next-steps "..." \
  [--outcome "committed, pushed"] [--decisions "..."] [--blockers "..."] [--status done] \
  [--transcript auto]
```

`session log` takes the same flags and works without a prior `session start`. Write
`current-state` and `next-steps` for someone with no memory of this conversation.

## Tasks

Tasks belong to an effort, not a session: a task often opens in one session and closes several
sessions later.

```bash
cavecore work task add <effort-slug> "<description>" [--external-ref ado:123]
cavecore work task list [--effort slug] [--all]
cavecore work task done <id>      |  cavecore work task cancel <id>
```

Add a task whenever a concrete to-do surfaces that won't be done right now. If the workspace
tracks tasks in Azure DevOps (`cavecore which` shows `tasks: ado`), create work items there
instead and only reference their IDs here.

## Decisions

```bash
cavecore work decision add <project-slug> [--effort slug] --decision "..." --rationale "..."
cavecore work decision list [--project p] [--effort e]
```

Log a decision when a real trade-off was settled, so a later session can find out why.

## Reading the ledger directly

`cavecore ledger sql "SELECT ..."` runs a read-only query. `cavecore ledger info` lists tables and
row counts. Plugins may add their own tables. Never edit `ledger.db` with other tools.

## Optional: mirror to a notes vault

If a notes-vault agent is installed (for example `concierge-memory-obsidian`), you may mirror
projects (`Projects/<slug>.md`: status, start date, one-paragraph description) and tasks
(`Tasks/<effort>-<id>.md`) there for browsing. The ledger stays the source of truth; don't mirror
session detail.
