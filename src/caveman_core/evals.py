#!/usr/bin/env python3
"""
``cavecore eval``: subagent eval harness - correctness and efficiency regression checks.

Test cases and run results live in the workspace ledger, in their own tables
(``eval_test_cases``, ``eval_runs``); traces go to ``data/evals/traces/``.

`run generate` drives a subagent directly via headless Claude Code
(`claude -p --agent <name> "<prompt>" --output-format stream-json`) and
captures the full event stream to data/evals/traces/ — this is the
*supported, verified* way to get a subagent's real tool-call trace. Two
things that look plausible do NOT work, confirmed by hands-on testing
before landing on this: (1) the in-conversation Agent tool's
`tasks/<agentId>.output` file is zeroed out by the time a completion
notification arrives — not a supported observability surface, by design;
(2) subagent turns are never written to the main session's own transcript
JSONL (`isSidechain` never appears true there). Headless `--agent` mode
sidesteps both — the subagent's tool calls are the top-level event stream
of that (sub)process, which we capture ourselves via stdout redirect, so
nothing can clear it out from under us afterward.

Headless mode authenticates the same way as the interactive session (checked
via the stream's `apiKeySource` field — `none`, i.e. subscription OAuth, not
a separate ANTHROPIC_API_KEY) unless that env var is set, so `run generate`
draws from the same Pro/Max usage pool as everything else here, not a
second bill — but it is still real usage, so don't loop it needlessly.

llm_judge grading talks to local Ollama through caveman_core.llm (never through
an MCP tool, since this runs outside a Claude Code conversation). The judge model
is --judge-model, else CAVEMAN_EVAL_JUDGE, else qwen3-coder:30b.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import os

from . import ledger, llm
from .paths import Workspace

# Set by configure() from the resolved workspace before any command runs.
WS: Workspace | None = None
ROOT = Path.cwd()
TRACES_DIR = ROOT / "data" / "evals" / "traces"
MCP_CONFIG_PATH = ROOT / ".mcp.json"
DEFAULT_GENERATE_TIMEOUT = 600
MCP_READY_TIMEOUT_S = 20

DEFAULT_JUDGE_MODEL = os.environ.get("CAVEMAN_EVAL_JUDGE", "qwen3-coder:30b")
REQUEST_TIMEOUT = 60.0

NOW = "strftime('%Y-%m-%dT%H:%M:%SZ','now')"

ASSERTION_TYPES = ("tool_trace", "llm_judge", "structural")
TRIGGERS = ("manual", "git_commit", "periodic")

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS eval_test_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    case_id TEXT NOT NULL,
    prompt TEXT NOT NULL,
    assertion_type TEXT NOT NULL CHECK (assertion_type IN {ASSERTION_TYPES!r}),
    assertion_spec TEXT NOT NULL,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT ({NOW}),
    UNIQUE(agent, case_id)
);

CREATE TABLE IF NOT EXISTS eval_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_case_id INTEGER NOT NULL REFERENCES eval_test_cases(id),
    run_at TEXT NOT NULL DEFAULT ({NOW}),
    trigger TEXT NOT NULL CHECK (trigger IN {TRIGGERS!r}),
    pass_fail TEXT NOT NULL CHECK (pass_fail IN ('pass','fail')),
    judge_score REAL,
    duration_s REAL,
    tool_call_count INTEGER,
    tokens_in INTEGER,
    tokens_out INTEGER,
    trace_path TEXT,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS IX_eval_runs_test_case_id ON eval_runs(test_case_id);
"""


def configure(ws: Workspace) -> None:
    global WS, ROOT, TRACES_DIR, MCP_CONFIG_PATH
    WS, ROOT = ws, ws.root
    TRACES_DIR = ROOT / "data" / "evals" / "traces"
    MCP_CONFIG_PATH = ROOT / ".mcp.json"


def get_conn() -> sqlite3.Connection:
    conn = ledger.connect(WS)
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def find_case(conn: sqlite3.Connection, agent: str, case_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM eval_test_cases WHERE agent = ? AND case_id = ?", (agent, case_id)
    ).fetchone()
    if not row:
        die(f"no test case '{case_id}' for agent '{agent}'")
    return row


# ------------------------------------------------------------- case CRUD --

def cmd_case_add(conn, args):
    try:
        spec = json.loads(args.assertion_spec)
    except json.JSONDecodeError as e:
        die(f"--assertion-spec is not valid JSON: {e}")
    conn.execute(
        "INSERT INTO eval_test_cases (agent, case_id, prompt, assertion_type, assertion_spec, notes) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (args.agent, args.case_id, args.prompt, args.assertion_type, json.dumps(spec), args.notes),
    )
    conn.commit()
    print(f"added case '{args.case_id}' for agent '{args.agent}'")


def cmd_case_list(conn, args):
    if args.agent:
        rows = conn.execute(
            "SELECT * FROM eval_test_cases WHERE agent = ? ORDER BY case_id", (args.agent,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM eval_test_cases ORDER BY agent, case_id").fetchall()
    if not rows:
        print("(no test cases yet)")
        return
    for c in rows:
        last = conn.execute(
            "SELECT pass_fail, run_at FROM eval_runs WHERE test_case_id = ? ORDER BY run_at DESC LIMIT 1",
            (c["id"],),
        ).fetchone()
        status = f"{last['pass_fail']} @ {last['run_at']}" if last else "never run"
        print(f"[{c['assertion_type']:10}] {c['agent']:10} {c['case_id']:28} ({status})")


def cmd_case_show(conn, args):
    c = find_case(conn, args.agent, args.case_id)
    print(f"# {c['agent']} / {c['case_id']}")
    print(f"assertion_type: {c['assertion_type']}")
    print(f"assertion_spec: {c['assertion_spec']}")
    print(f"\nprompt:\n{c['prompt']}")
    if c["notes"]:
        print(f"\nnotes: {c['notes']}")
    runs = conn.execute(
        "SELECT * FROM eval_runs WHERE test_case_id = ? ORDER BY run_at DESC LIMIT 10", (c["id"],)
    ).fetchall()
    print(f"\n## Last {len(runs)} run(s)")
    for r in runs:
        print(
            f"  #{r['id']:<5} [{r['pass_fail']:4}] {r['run_at']}  trigger={r['trigger']:10} "
            f"tools={r['tool_call_count']} dur={r['duration_s']}s "
            f"tok_in={r['tokens_in']} tok_out={r['tokens_out']}"
        )


# -------------------------------------------------------- generate run --

def configured_mcp_server_names() -> set[str]:
    if not MCP_CONFIG_PATH.exists():
        return set()
    try:
        config = json.loads(MCP_CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    return set((config.get("mcpServers") or {}).keys())


def wait_for_mcp_ready(debug_log_path: Path, needed: set[str], timeout_s: float) -> set[str]:
    """
    Poll a --debug-file log for "Successfully connected" lines. MCP servers
    start "fully async (nonblocking)" (confirmed via --debug mcp), so a
    plain `claude -p "<prompt>"` call races the connection and reliably
    loses — the model's first turn dispatches with only built-in tools,
    before any MCP server finishes its ~1s stdio handshake. Returns the set
    of servers confirmed connected before the deadline.
    """
    connected: set[str] = set()
    deadline = time.time() + timeout_s
    while time.time() < deadline and connected < needed:
        time.sleep(0.3)
        try:
            log_text = debug_log_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        for name in needed - connected:
            if f'MCP server "{name}": Successfully connected' in log_text:
                connected.add(name)
    return connected


def cmd_run_generate(conn, args):
    """
    Run the target agent directly, headless, via `claude -p --agent <name>`.
    Its tool calls are the top-level events of that process's own
    stream-json output — no Task-tool nesting to unwrap, and nothing the
    platform can clear out from under us since we own the captured file.

    Uses bidirectional streaming (--input-format/--output-format
    stream-json over stdin/stdout) instead of a positional prompt argument,
    specifically so the prompt can be held back until MCP servers report
    connected (see wait_for_mcp_ready) — a plain positional-prompt call
    dispatches before the servers are ready and the agent silently loses
    access to every mcp__* tool for the whole run.
    """
    c = find_case(conn, args.agent, args.case_id)
    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"{args.agent}_{args.case_id}_{datetime.now(timezone.utc):%Y%m%dT%H%M%S}"
    trace_path = TRACES_DIR / f"{stem}.jsonl"
    debug_log_path = TRACES_DIR / f"{stem}.debug.log"

    claude_bin = shutil.which("claude")
    if not claude_bin:
        die("'claude' not found on PATH")
    cmd = [
        claude_bin, "-p", "--agent", args.agent,
        "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
        # Headless mode has no TTY to approve gated tool calls, so without
        # this every case needing a permission-gated tool fails on a denial,
        # not real behavior. Each agent's tool ACCESS is still scoped by its
        # own .claude/agents/<name>.md `tools:` frontmatter — this only
        # removes the per-call approval prompt for local, unattended runs.
        "--permission-mode", "bypassPermissions",
        "--debug-file", str(debug_log_path),
    ]

    proc = subprocess.Popen(
        cmd, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8",
    )

    needed = configured_mcp_server_names()
    connected = wait_for_mcp_ready(debug_log_path, needed, MCP_READY_TIMEOUT_S)
    if connected < needed:
        print(f"warning: MCP server(s) {needed - connected} not confirmed connected after {MCP_READY_TIMEOUT_S}s — sending prompt anyway", file=sys.stderr)

    input_msg = json.dumps({"type": "user", "message": {"role": "user", "content": c["prompt"]}})
    proc.stdin.write(input_msg + "\n")
    proc.stdin.close()

    try:
        out, err = proc.communicate(timeout=args.timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        die(f"claude -p timed out after {args.timeout}s for {args.agent}/{args.case_id}")

    trace_path.write_text(out, encoding="utf-8")
    if proc.returncode != 0:
        print(f"warning: claude exited {proc.returncode}\nstderr: {err[:1000]}", file=sys.stderr)

    print(str(trace_path))
    return trace_path


# --------------------------------------------------- stream-json parsing --

def parse_stream_json(path: Path) -> dict:
    """
    Parse the stream-json output of `claude -p --agent <name> ...` into a
    flat trace: tool calls made (top-level = the agent's own calls, since
    --agent runs it as the session rather than nesting it under a Task
    tool_use), final result text, token totals, and wall-clock duration —
    all read straight from the CLI's own `result` event, not re-derived.
    """
    tool_calls: list[str] = []
    final_text = ""
    tokens_in = 0
    tokens_out = 0
    duration_s = None

    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            if obj.get("type") == "assistant" and obj.get("parent_tool_use_id") is None:
                content = (obj.get("message") or {}).get("content")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name"):
                            tool_calls.append(block["name"])

            elif obj.get("type") == "result":
                final_text = str(obj.get("result", ""))
                usage = obj.get("usage") or {}
                tokens_in = (usage.get("input_tokens", 0) or 0) + (usage.get("cache_creation_input_tokens", 0) or 0)
                tokens_out = usage.get("output_tokens", 0) or 0
                dm = obj.get("duration_ms")
                duration_s = round(dm / 1000, 1) if dm is not None else None

    return {
        "tool_calls": tool_calls,
        "final_text": final_text,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "duration_s": duration_s,
    }


# ------------------------------------------------------------- grading --

PLUGIN_TOOL_RE = re.compile(r"^mcp__plugin_.*_([^_]+)__(.+)$")


def unscope_tool(name: str) -> str:
    """Normalize a plugin-scoped MCP tool name to its bare form.

    Plugin-provided MCP tools are exposed as
    `mcp__plugin_<plugin-name>_<server>__<tool>`, while the same server loaded
    directly is just `mcp__<server>__<tool>`. Assertions are written against the
    bare form, so both sides get normalized before comparison — otherwise a
    plugin migration silently turns every `forbidden` check into a no-op that
    can never fire.
    """
    m = PLUGIN_TOOL_RE.match(name)
    return f"mcp__{m.group(1)}__{m.group(2)}" if m else name


def grade_tool_trace(spec: dict, trace: dict) -> tuple[bool, str]:
    called = {unscope_tool(n) for n in trace["tool_calls"]}
    forbidden = {unscope_tool(n) for n in spec.get("forbidden", [])}
    required = {unscope_tool(n) for n in spec.get("required", [])}
    hit_forbidden = called & forbidden
    if hit_forbidden:
        return False, f"forbidden tool(s) called: {sorted(hit_forbidden)}"
    missing_required = required - called
    if missing_required:
        return False, f"required tool(s) not called: {sorted(missing_required)}"
    return True, "ok"


def grade_structural(spec: dict, trace: dict) -> tuple[bool, str]:
    pattern = spec.get("pattern")
    if not pattern:
        die("structural assertion_spec needs a 'pattern' key")
    flags = re.IGNORECASE if spec.get("ignore_case", True) else 0
    if re.search(pattern, trace["final_text"], flags):
        return True, "pattern matched"
    return False, f"pattern not found in final response: {pattern!r}"


def grade_llm_judge(spec: dict, trace: dict, model: str) -> tuple[bool, str, float | None]:
    rubric = spec.get("rubric")
    threshold = spec.get("threshold", 3)
    if not rubric:
        die("llm_judge assertion_spec needs a 'rubric' key")
    judge_prompt = (
        f"{rubric}\n\n"
        f"Agent's final response to grade:\n{trace['final_text']}\n\n"
        'Return ONLY a JSON object: {"score": <1-5 integer>, "reason": "<one sentence>"}'
    )
    try:
        content = llm.chat([{"role": "user", "content": judge_prompt}], model, timeout=REQUEST_TIMEOUT)
    except llm.LLMError as e:
        die(f"llm_judge grading failed: {e}")
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        return False, f"judge did not return parseable JSON: {content[:200]!r}", None
    try:
        parsed = json.loads(match.group(0))
        score = float(parsed["score"])
    except (json.JSONDecodeError, KeyError, ValueError):
        return False, f"judge JSON malformed: {content[:200]!r}", None
    passed = score >= threshold
    reason = parsed.get("reason", "")
    return passed, f"score={score} threshold={threshold} — {reason}", score


def cmd_run_grade(conn, args):
    c = find_case(conn, args.agent, args.case_id)
    spec = json.loads(c["assertion_spec"])

    transcript_path = Path(args.transcript)
    if not transcript_path.exists():
        die(f"transcript not found: {transcript_path}")
    trace = parse_stream_json(transcript_path)

    judge_score = None
    if c["assertion_type"] == "tool_trace":
        passed, reason = grade_tool_trace(spec, trace)
    elif c["assertion_type"] == "structural":
        passed, reason = grade_structural(spec, trace)
    else:  # llm_judge
        passed, reason, judge_score = grade_llm_judge(spec, trace, args.judge_model)

    conn.execute(
        "INSERT INTO eval_runs (test_case_id, trigger, pass_fail, judge_score, duration_s, "
        "tool_call_count, tokens_in, tokens_out, trace_path, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            c["id"], args.trigger, "pass" if passed else "fail", judge_score, trace["duration_s"],
            len(trace["tool_calls"]), trace["tokens_in"], trace["tokens_out"],
            str(transcript_path), reason,
        ),
    )
    conn.commit()

    status = "PASS" if passed else "FAIL"
    print(f"{status}: {args.agent}/{args.case_id} — {reason}")
    print(
        f"  tools={len(trace['tool_calls'])} {trace['tool_calls']}  "
        f"duration={trace['duration_s']}s  tokens_in={trace['tokens_in']} tokens_out={trace['tokens_out']}"
    )
    if not passed:
        sys.exit(1)


def cmd_run_all(conn, args):
    """
    Run every test case for one agent (generate + grade each), advisory-only:
    a failure or generate error on one case doesn't stop the rest. Built for
    the git subagent's pre-commit check — it needs a pass/fail summary for
    an agent, not a hard exit code, since these results inform the user rather
    than block the commit.
    """
    cases = conn.execute(
        "SELECT * FROM eval_test_cases WHERE agent = ? ORDER BY case_id", (args.agent,)
    ).fetchall()
    if not cases:
        print(f"no test cases for agent '{args.agent}'")
        return

    results: list[tuple[str, str]] = []
    for c in cases:
        gen_args = argparse.Namespace(agent=args.agent, case_id=c["case_id"], timeout=args.timeout)
        try:
            trace_path = cmd_run_generate(conn, gen_args)
        except SystemExit:
            results.append((c["case_id"], "ERROR (generate failed)"))
            continue

        grade_args = argparse.Namespace(
            agent=args.agent, case_id=c["case_id"], transcript=str(trace_path),
            trigger=args.trigger, judge_model=args.judge_model,
        )
        try:
            cmd_run_grade(conn, grade_args)
            results.append((c["case_id"], "PASS"))
        except SystemExit as e:
            results.append((c["case_id"], "FAIL" if e.code == 1 else "ERROR (grade failed)"))

    passed = sum(1 for _, status in results if status == "PASS")
    print(f"\n{args.agent}: {passed}/{len(results)} passed")
    for case_id, status in results:
        print(f"  [{status}] {case_id}")


def cmd_run_list(conn, args):
    query = (
        "SELECT r.*, c.agent, c.case_id FROM eval_runs r "
        "JOIN eval_test_cases c ON c.id = r.test_case_id WHERE 1=1"
    )
    params: list = []
    if args.agent:
        query += " AND c.agent = ?"
        params.append(args.agent)
    if args.case_id:
        query += " AND c.case_id = ?"
        params.append(args.case_id)
    query += " ORDER BY r.run_at DESC LIMIT ?"
    params.append(args.limit)
    rows = conn.execute(query, params).fetchall()
    if not rows:
        print("(no runs yet)")
        return
    for r in rows:
        print(
            f"#{r['id']:<5} [{r['pass_fail']:4}] {r['agent']}/{r['case_id']:24} "
            f"{r['run_at']}  trigger={r['trigger']:10} tools={r['tool_call_count']} "
            f"dur={r['duration_s']}s"
        )


def cmd_run_compare(conn, args):
    def get_run(run_id):
        row = conn.execute(
            "SELECT r.*, c.agent, c.case_id FROM eval_runs r "
            "JOIN eval_test_cases c ON c.id = r.test_case_id WHERE r.id = ?",
            (run_id,),
        ).fetchone()
        if not row:
            die(f"no run #{run_id}")
        return row

    a, b = get_run(args.run_a), get_run(args.run_b)
    if a["test_case_id"] != b["test_case_id"]:
        print("warning: comparing runs from two different test cases\n")

    print(f"#{a['id']} ({a['run_at']})  vs  #{b['id']} ({b['run_at']})")
    print(f"  {a['agent']}/{a['case_id']}  vs  {b['agent']}/{b['case_id']}")
    print(f"  pass_fail:       {a['pass_fail']:6} -> {b['pass_fail']}")
    print(f"  tool_call_count: {a['tool_call_count']} -> {b['tool_call_count']}")
    print(f"  duration_s:      {a['duration_s']} -> {b['duration_s']}")
    print(f"  tokens_in:       {a['tokens_in']} -> {b['tokens_in']}")
    print(f"  tokens_out:      {a['tokens_out']} -> {b['tokens_out']}")

    if a["pass_fail"] == "pass" and b["pass_fail"] == "fail":
        print("\n  REGRESSION: was passing, now failing")
    if a["tool_call_count"] and b["tool_call_count"] and b["tool_call_count"] > a["tool_call_count"] * 1.5:
        print("\n  DRIFT: tool call count up 50%+")
    if a["tokens_out"] and b["tokens_out"] and b["tokens_out"] > a["tokens_out"] * 1.5:
        print("  DRIFT: output tokens up 50%+")


def cmd_run_trend(conn, args):
    """
    Efficiency trend per test case: compares the most recent run's
    tool_call_count/duration_s/tokens_out against the mean of that case's
    prior runs, flagging drift the same way `run compare` does for a single
    pair — but without needing to hand-pick which two run IDs to diff.
    """
    if args.agent and args.case_id:
        cases = [find_case(conn, args.agent, args.case_id)]
    elif args.agent:
        cases = conn.execute(
            "SELECT * FROM eval_test_cases WHERE agent = ? ORDER BY case_id", (args.agent,)
        ).fetchall()
    else:
        cases = conn.execute("SELECT * FROM eval_test_cases ORDER BY agent, case_id").fetchall()

    any_drift = False
    for c in cases:
        runs = conn.execute(
            "SELECT * FROM eval_runs WHERE test_case_id = ? ORDER BY run_at ASC", (c["id"],)
        ).fetchall()
        label = f"{c['agent']}/{c['case_id']}"
        if len(runs) < 2:
            print(f"{label}: {len(runs)} run(s) — not enough history for a trend")
            continue

        *prior, latest = runs
        metrics = ("tool_call_count", "duration_s", "tokens_in", "tokens_out")
        baselines = {}
        for m in metrics:
            vals = [r[m] for r in prior if r[m] is not None]
            baselines[m] = sum(vals) / len(vals) if vals else None

        print(f"{label}: {len(runs)} runs, latest #{latest['id']} [{latest['pass_fail']}] {latest['run_at']}")
        flags = []
        for m in metrics:
            base = baselines[m]
            cur = latest[m]
            if base is None or cur is None:
                continue
            ratio = f" ({cur / base:.2f}x)" if base else ""
            print(f"  {m:16} baseline_avg={base:.1f}  latest={cur}{ratio}")
            if base > 0 and cur > base * args.threshold:
                flags.append(m)
        if flags:
            any_drift = True
            print(f"  DRIFT: {', '.join(flags)} up {int((args.threshold - 1) * 100)}%+ vs baseline")
        if latest["pass_fail"] == "fail" and prior[-1]["pass_fail"] == "pass":
            any_drift = True
            print("  REGRESSION: was passing, now failing")
        print()

    if not any_drift:
        print("no drift or regressions detected")


def cmd_run_exec(conn, args):
    """Shortcut: generate + grade in one shot, mirroring ADK's `eval run`."""
    trace_path = cmd_run_generate(conn, args)
    args.transcript = str(trace_path)
    cmd_run_grade(conn, args)


# ---------------------------------------------------------------- which --

def cmd_which_agents(conn, args):
    """List agents that have at least one test case — used by the git subagent
    to decide whether a staged agent-definition change has an eval set to run."""
    rows = conn.execute("SELECT DISTINCT agent FROM eval_test_cases ORDER BY agent").fetchall()
    for r in rows:
        print(r["agent"])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cavecore eval", description="Subagent eval harness")
    sub = p.add_subparsers(dest="command", required=True)

    case = sub.add_parser("case", help="Manage test cases").add_subparsers(dest="subcommand", required=True)
    a = case.add_parser("add")
    a.add_argument("--agent", required=True)
    a.add_argument("--case-id", required=True)
    a.add_argument("--prompt", required=True)
    a.add_argument("--assertion-type", required=True, choices=ASSERTION_TYPES)
    a.add_argument("--assertion-spec", required=True, help="JSON spec, shape depends on assertion-type")
    a.add_argument("--notes")
    a.set_defaults(func=cmd_case_add)
    a = case.add_parser("list"); a.add_argument("--agent"); a.set_defaults(func=cmd_case_list)
    a = case.add_parser("show"); a.add_argument("agent"); a.add_argument("case_id"); a.set_defaults(func=cmd_case_show)

    run = sub.add_parser("run", help="Generate / grade / inspect eval runs").add_subparsers(dest="subcommand", required=True)
    a = run.add_parser("generate", help="Run the agent headless via `claude -p --agent`, capture its trace")
    a.add_argument("--agent", required=True)
    a.add_argument("--case-id", required=True)
    a.add_argument("--timeout", type=int, default=DEFAULT_GENERATE_TIMEOUT, help=f"Seconds before giving up (default: {DEFAULT_GENERATE_TIMEOUT})")
    a.set_defaults(func=cmd_run_generate)

    a = run.add_parser("grade", help="Parse a captured trace, apply the case's assertion, log the result")
    a.add_argument("--agent", required=True)
    a.add_argument("--case-id", required=True)
    a.add_argument("--transcript", required=True, help="Path to a trace captured by `run generate`")
    a.add_argument("--trigger", required=True, choices=TRIGGERS)
    a.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL, help=f"Ollama model for llm_judge cases (default: {DEFAULT_JUDGE_MODEL})")
    a.set_defaults(func=cmd_run_grade)

    a = run.add_parser("exec", help="generate + grade in one shot")
    a.add_argument("--agent", required=True)
    a.add_argument("--case-id", required=True)
    a.add_argument("--trigger", required=True, choices=TRIGGERS)
    a.add_argument("--timeout", type=int, default=DEFAULT_GENERATE_TIMEOUT)
    a.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    a.set_defaults(func=cmd_run_exec)

    a = run.add_parser("all", help="Run every test case for one agent, advisory-only (never exits nonzero)")
    a.add_argument("--agent", required=True)
    a.add_argument("--trigger", required=True, choices=TRIGGERS)
    a.add_argument("--timeout", type=int, default=DEFAULT_GENERATE_TIMEOUT)
    a.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    a.set_defaults(func=cmd_run_all)

    a = run.add_parser("list")
    a.add_argument("--agent"); a.add_argument("--case-id"); a.add_argument("--limit", type=int, default=30)
    a.set_defaults(func=cmd_run_list)
    a = run.add_parser("compare")
    a.add_argument("run_a", type=int); a.add_argument("run_b", type=int)
    a.set_defaults(func=cmd_run_compare)

    a = run.add_parser("trend", help="Compare latest run vs prior-run average per case, flag drift/regression")
    a.add_argument("--agent")
    a.add_argument("--case-id")
    a.add_argument("--threshold", type=float, default=1.5, help="Flag latest run if metric exceeds baseline avg * threshold (default: 1.5)")
    a.set_defaults(func=cmd_run_trend)

    a = sub.add_parser("which-agents", help="List agents that have at least one test case")
    a.set_defaults(func=cmd_which_agents)

    return p


def main(ws: Workspace, argv: list[str] | None = None) -> int:
    configure(ws)
    parser = build_parser()
    args = parser.parse_args(argv)
    conn = get_conn()
    try:
        args.func(conn, args)
    finally:
        conn.close()
    return 0
