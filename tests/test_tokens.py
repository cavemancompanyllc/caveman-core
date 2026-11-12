import json

from caveman_core import paths, tokens


def _assistant(mid, ctx, out=10, tools=0, sidechain=False, split=1, model="claude-test"):
    """Transcript lines for one assistant message: `split` lines repeating the same usage."""
    usage = {"input_tokens": 5, "cache_read_input_tokens": ctx - 5 - 100, "cache_creation_input_tokens": 100,
             "output_tokens": out}
    lines = []
    for i in range(split):
        content = [{"type": "tool_use", "id": f"t{mid}{i}", "name": "Bash", "input": {}}] if i < tools else \
                  [{"type": "text", "text": "ok"}]
        lines.append(json.dumps({"type": "assistant", "isSidechain": sidechain,
                                 "message": {"id": mid, "model": model, "content": content, "usage": usage}}))
    return lines


def _user(text="hi"):
    return json.dumps({"type": "user", "message": {"role": "user", "content": text}})


def _write(path, lines, mode="w"):
    with path.open(mode, encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _ws(root, extra=""):
    (root / "workspace.yaml").write_text("name: Acme\nslug: acme\nkind: business\n" + extra, encoding="utf-8")
    return paths.resolve(root)


def test_limits_default_and_configured(ws):
    assert tokens.limits(paths.resolve(ws)) == tokens.Limits(tokens.DEFAULT_SOFT, tokens.DEFAULT_HARD)
    w = _ws(ws, "tokens:\n  soft: 1000\n  hard: 0\n")
    lim = tokens.limits(w)
    assert lim == tokens.Limits(1000, 0)
    assert lim.band(999) == 0 and lim.band(1000) == 1 and lim.band(10**9) == 1


def test_current_context_reads_last_main_thread_message(tmp_path):
    t = tmp_path / "s.jsonl"
    _write(t, [_user(), *_assistant("m1", 1000), _user(), *_assistant("m2", 5000, split=3),
               *_assistant("sub", 99999, sidechain=True), _user()])
    assert tokens.current_context(t) == (5000, "claude-test")


def test_current_context_finds_message_beyond_first_tail_window(tmp_path, monkeypatch):
    monkeypatch.setattr(tokens, "TAIL_STEPS", (64, 512, None))
    t = tmp_path / "s.jsonl"
    _write(t, [*_assistant("m1", 4242), *[_user("x" * 200) for _ in range(20)]])
    assert tokens.current_context(t)[0] == 4242


def test_record_is_incremental_and_counts_each_message_once(ws, tmp_path):
    w = paths.resolve(ws)
    t = tmp_path / "abc.jsonl"
    _write(t, [_user(), *_assistant("m1", 1000, out=7, tools=2, split=3)])
    r = tokens.record(w, "abc", t)
    assert (r["turns"], r["tool_calls"], r["output_tokens"], r["context_tokens"]) == (1, 2, 7, 1000)

    # same message continued in the next chunk (not double counted), then a new one, then a sidechain
    _write(t, [*_assistant("m1", 1000, out=7), *_assistant("m2", 3000, out=5), *_assistant("x", 9, sidechain=True)],
           mode="a")
    r = tokens.record(w, "abc", t)
    assert (r["turns"], r["output_tokens"], r["input_tokens"]) == (2, 12, 4000)
    assert (r["context_tokens"], r["peak_context_tokens"]) == (3000, 3000)

    # nothing new: no change
    assert tokens.record(w, "abc", t)["turns"] == 2

    # a partial last line is left for next time
    with t.open("a", encoding="utf-8") as fh:
        fh.write(_assistant("m3", 2000)[0][:30])
    assert tokens.record(w, "abc", t)["turns"] == 2


def test_guard_warns_once_per_band_and_rearms_after_compaction(ws, tmp_path):
    w = _ws(ws, "tokens:\n  soft: 1000\n  hard: 2000\n")
    t = tmp_path / "s1.jsonl"
    _write(t, _assistant("a", 500))
    assert tokens.guard(w, "s1", t) is None

    _write(t, _assistant("b", 1200), mode="a")
    msg = tokens.guard(w, "s1", t)
    assert "soft limit (1k)" in msg and "/caveman-core:handoff" in msg
    assert tokens.guard(w, "s1", t) is None                     # once per band

    _write(t, _assistant("c", 2500), mode="a")
    assert "hard limit (2k)" in tokens.guard(w, "s1", t)
    assert tokens.guard(w, "s1", t) is None

    # compaction: context drops well below soft; the Stop hook re-arms
    _write(t, _assistant("d", 300), mode="a")
    tokens.record(w, "s1", t)
    _write(t, _assistant("e", 1100), mode="a")
    assert "soft limit" in tokens.guard(w, "s1", t)


def test_guard_off_when_limits_are_zero(ws, tmp_path):
    w = _ws(ws, "tokens:\n  soft: 0\n  hard: 0\n")
    t = tmp_path / "s.jsonl"
    _write(t, _assistant("a", 10**7))
    assert tokens.guard(w, "s", t) is None


def test_hook_entry_points(ws, tmp_path, monkeypatch):
    _ws(ws, "tokens:\n  soft: 1000\n  hard: 2000\n")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(ws))
    t = tmp_path / "sess.jsonl"
    _write(t, _assistant("a", 1500))
    payload = json.dumps({"session_id": "sess", "transcript_path": str(t), "cwd": str(ws)})

    out = json.loads(tokens.hook_prompt(payload))
    assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "soft limit" in out["hookSpecificOutput"]["additionalContext"]
    assert out["systemMessage"].startswith("caveman-core:")
    assert tokens.hook_prompt(payload) == ""

    tokens.hook_stop(payload)
    assert "sess" in tokens.report(paths.resolve(ws))

    # broken input never raises
    assert tokens.hook_prompt("not json") == ""
    tokens.hook_stop("not json")


def test_hook_outside_a_workspace_is_silent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    t = tmp_path / "s.jsonl"
    _write(t, _assistant("a", 10**7))
    assert tokens.hook_prompt(json.dumps({"session_id": "s", "transcript_path": str(t), "cwd": str(tmp_path)})) == ""


def test_tokens_cli(run, ws, tmp_path):
    w = paths.resolve(ws)
    t = tmp_path / "deadbeef-1.jsonl"
    _write(t, _assistant("a", 400_000))
    tokens.record(w, "deadbeef-1", t)
    code, out, _ = run("tokens")
    assert code == 0 and "deadbeef" in out and "past hard" in out and "soft 300,000" in out
