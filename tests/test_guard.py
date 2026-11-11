import subprocess

from caveman_core import guard


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def make_repo(tmp_path):
    repo = tmp_path / "pub"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "T")
    (repo / "a.txt").write_text("hello\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    return repo


def test_scan_finds_terms_in_lines_and_messages(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    base = git(repo, "rev-parse", "HEAD").strip()
    (repo / "b.py").write_text("KIDS = ('Zed', 'other')\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "add the Marmot League rules")
    deny = tmp_path / "deny.txt"
    deny.write_text("# comment\nmarmot league\n" + r"re:\bzed\b" + "\nnot-present\n")
    monkeypatch.setenv("CAVEMAN_DENYLIST", str(deny))
    hits = guard.scan_range(repo, f"{base}..HEAD", guard.load_denylist())
    assert any("b.py" in h and "zed" in h for h in hits)
    assert any("commit message" in h and "marmot league" in h for h in hits)
    assert not any("not-present" in h for h in hits)


def test_pre_push_blocks_and_passes(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    base = git(repo, "rev-parse", "HEAD").strip()
    (repo / "c.txt").write_text("fine content\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "clean change")
    head = git(repo, "rev-parse", "HEAD").strip()
    deny = tmp_path / "deny.txt"
    deny.write_text("secret-family-name\n")
    monkeypatch.setenv("CAVEMAN_DENYLIST", str(deny))
    monkeypatch.setattr(guard, "gitleaks_exe", lambda: None)
    stdin = f"refs/heads/main {head} refs/heads/main {base}\n"
    assert guard.pre_push(repo, stdin) == 0
    (repo / "d.txt").write_text("mentions Secret-Family-Name here\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "oops")
    head2 = git(repo, "rev-parse", "HEAD").strip()
    assert guard.pre_push(repo, f"refs/heads/main {head2} refs/heads/main {base}\n") == 1
    # deleting a branch pushes no content
    assert guard.pre_push(repo, f"(delete) {'0' * 40} refs/heads/old {base}\n") == 0


def test_install_writes_hook(tmp_path):
    repo = make_repo(tmp_path)
    hook = guard.install(repo)
    assert hook.name == "pre-push" and "guard pre-push" in hook.read_text()
