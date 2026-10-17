"""Caller-supplied refs and paths reach git through the remote-gateway REST mirror.

Each hostile value names an executable that records it ran, so a file in the sink
means git parsed request text as an option instead of a revision or a path.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from hermes_cli import web_git, web_server

pytest.importorskip("starlette.testclient")
from starlette.testclient import TestClient

_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_SECRET = "outside-the-repo-secret"


@pytest.fixture
def client():
    previous = getattr(web_server.app.state, "auth_required", None)
    web_server.app.state.auth_required = False
    test_client = TestClient(web_server.app)
    test_client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
    try:
        yield test_client
    finally:
        if previous is None:
            try:
                delattr(web_server.app.state, "auth_required")
            except AttributeError:
                pass
        else:
            web_server.app.state.auth_required = previous


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def sink(tmp_path):
    root = tmp_path / "sink"
    root.mkdir()
    payload = root / "payload"
    payload.write_text(f'#!/bin/sh\ntouch "{root / "ran"}"\n', encoding="utf-8")
    payload.chmod(0o755)
    return root, payload


def _executed(root: Path) -> list[str]:
    return sorted(p.name for p in root.iterdir() if p.name != "payload")


def _hostile(payload: Path) -> list[str]:
    return [f"--upload-pack={payload}", f"-c core.pager={payload}", f"-ccore.pager={payload}",
            f"--output={payload}.out", "-"]


@pytest.fixture
def clone(tmp_path):
    """A clone of a bare origin with ``main`` and one extra commit, a tracked
    modification, an untracked file, and a secret file outside the repo."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)
    root = tmp_path / "clone"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "commit.gpgsign", "false")
    (root / "a.txt").write_text("one\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    (root / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    _git(root, "commit", "-qam", "second")
    _git(root, "remote", "add", "origin", str(origin))
    _git(root, "push", "-q", "origin", "main")
    _git(root, "fetch", "-q", "origin")
    (root / "a.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (root / "new.txt").write_text("brand new\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text(f"{_SECRET}\n", encoding="utf-8")
    return root


def test_ref_endpoints_never_hand_option_shaped_refs_to_git(client, clone, sink):
    root, payload = sink
    for ref in _hostile(payload):
        params = {"path": str(clone), "scope": "lastTurn", "base": ref}
        assert client.get("/api/git/review/rev-parse", params={"path": str(clone), "ref": ref}).json() == {"sha": None}
        assert client.get("/api/git/review/list", params=params).json() == {"files": [], "base": None}
        assert client.get("/api/git/review/diff", params={**params, "file": "a.txt"}).json() == {"diff": ""}
    assert _executed(root) == []


def test_ref_endpoints_resolve_refs_to_the_commit_sha(client, clone):
    parent = _git(clone, "rev-parse", "HEAD~1")
    head = _git(clone, "rev-parse", "HEAD")
    assert client.get("/api/git/review/rev-parse", params={"path": str(clone)}).json() == {"sha": head}

    for ref in ("HEAD:a.txt", "HEAD~1..HEAD", "no-such-branch", "HEAD^{tree}"):
        params = {"path": str(clone), "scope": "lastTurn", "base": ref}
        assert client.get("/api/git/review/rev-parse", params={"path": str(clone), "ref": ref}).json() == {"sha": None}
        assert client.get("/api/git/review/list", params=params).json() == {"files": [], "base": None}

    for ref in ("HEAD~1", parent[:10], parent, "main~1"):
        params = {"path": str(clone), "scope": "lastTurn", "base": ref}
        sha = client.get("/api/git/review/rev-parse", params={"path": str(clone), "ref": ref}).json()["sha"]
        assert sha == parent and _SHA.fullmatch(sha), ref
        listed = client.get("/api/git/review/list", params=params).json()
        assert listed["base"] == parent, ref
        assert {f["path"] for f in listed["files"]} == {"a.txt", "new.txt"}, ref
        diff = client.get("/api/git/review/diff", params={**params, "file": "a.txt"}).json()["diff"]
        assert "+two" in diff and "+three" in diff, ref


def test_worktree_add_never_hands_an_option_shaped_base_to_git(client, clone, sink):
    root, payload = sink
    # The sink is live: the same payload handed to git as an option does run.
    subprocess.run(["git", "fetch", "origin", f"--upload-pack={payload}"], cwd=clone, capture_output=True)
    assert _executed(root) == ["ran"]
    (root / "ran").unlink()

    for value in _hostile(payload):
        for base in (value, f"origin/{value}"):
            resp = client.post("/api/git/worktree/add", json={"path": str(clone), "branch": "hostile", "base": base})
            assert resp.status_code == 400, base
    assert _executed(root) == []
    assert _git(clone, "branch", "--list", "hostile") == ""


def test_worktree_add_base_is_resolved_to_the_commit_it_names(client, clone):
    head = _git(clone, "rev-parse", "HEAD")
    for base in ("no-such-branch", "origin/no-such-branch", "HEAD:a.txt", "HEAD~1..HEAD"):
        resp = client.post("/api/git/worktree/add", json={"path": str(clone), "branch": "bogus", "base": base})
        assert resp.status_code == 400, base

    for base, branch in (("origin/main", "from-remote"), ("main", "from-local"), (head, "from-sha")):
        added = client.post("/api/git/worktree/add", json={"path": str(clone), "branch": branch, "base": base}).json()
        tree = Path(added["path"])
        assert _git(tree, "rev-parse", "HEAD") == head, base
        assert _git(tree, "branch", "--show-current") == branch, base


def test_fetch_never_hands_option_shaped_remote_or_branch_names_to_git(clone, sink):
    root, payload = sink
    origin = _git(clone, "remote", "get-url", "origin")
    # Repo config can carry any remote name, so "configured" is not "safe in argv".
    for remote in _hostile(payload):
        _git(clone, "config", f"remote.{remote}.url", origin)
        assert web_git._fetch_remote_branch(str(clone), remote, "main") is False, remote
    for branch in _hostile(payload):
        assert web_git._fetch_remote_branch(str(clone), "origin", branch) is False, branch
    assert web_git._fetch_remote_branch(str(clone), "not-a-remote", "main") is False
    assert web_git._fetch_remote_branch(str(clone), "origin", "main:refs/heads/clobbered") is False
    assert _executed(root) == []
    assert web_git._fetch_remote_branch(str(clone), "origin", "main") is True


def test_untracked_diff_fallback_stays_inside_the_repo(client, clone):
    outside = clone.parent / "outside"
    os.symlink(outside, clone / "escape-dir")
    os.symlink(outside / "secret.txt", clone / "escape-file")
    hostile = [str(outside / "secret.txt"), "../outside/secret.txt", "escape-dir/secret.txt", "escape-file",
               str(clone / "escape-dir" / "secret.txt"), ".git/config", "missing.txt"]

    for file in [*hostile, str(clone / "new.txt")]:
        params = {"path": str(clone), "file": file}
        assert client.get("/api/git/review/diff", params=params).json() == {"diff": ""}, file
    for file in hostile:
        assert client.get("/api/git/file-diff", params={"path": str(clone), "file": file}).json() == {"diff": ""}, file

    assert "+brand new" in client.get("/api/git/review/diff", params={"path": str(clone), "file": "new.txt"}).json()["diff"]
    for file in ("new.txt", str(clone / "new.txt")):
        assert "+brand new" in client.get("/api/git/file-diff", params={"path": str(clone), "file": file}).json()["diff"]


def test_git_version_problem_refuses_git_older_than_2_30():
    for version in ("git version 2.23.0", "git version 2.24.4", "git version 2.29.3", "git version 1.9"):
        assert re.search(r"too old.*Git 2\.30\.0 or newer", web_git._git_version_problem(version)), version
    for version in ("git version 2.30.0", "git version 2.43.0", "git version 2.39.3 (Apple Git-145)",
                    "git version 2.45.1.windows.1", "git version 3.0.0", "not git output"):
        assert web_git._git_version_problem(version) == "", version


def test_worktree_add_on_old_git_fails_with_a_clear_error(client, clone, tmp_path, monkeypatch):
    real_git = shutil.which("git")
    bin_dir = tmp_path / "old-git-bin"
    bin_dir.mkdir()
    fake = bin_dir / "git"
    fake.write_text(f'#!/bin/sh\n[ "$1" = "--version" ] && {{ echo "git version 2.23.0"; exit 0; }}\n'
                    f'exec "{real_git}" "$@"\n', encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    for options in ({"branch": "fresh"}, {"branch": "fresh", "base": "main"}, {"existingBranch": "main"}):
        resp = client.post("/api/git/worktree/add", json={"path": str(clone), **options})
        assert resp.status_code == 400, options
        assert re.search(r"Git 2\.23\.0 is too old.*Git 2\.30\.0 or newer", resp.json()["detail"]), options
    assert not (clone / ".worktrees").exists()
    assert _git(clone, "branch", "--list", "fresh") == ""
